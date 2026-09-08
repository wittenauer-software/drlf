from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import threading
from hashlib import sha256
from pathlib import Path, PurePosixPath
from time import sleep

from drlf.public_release.bounds import Budget, ScanFailure
from drlf.public_release.filesystem import read_staging_filesystem
from drlf.public_release.models import (
    ApprovedInventory,
    CandidateSnapshot,
    FileObservation,
    PublicReleasePolicy,
)
from drlf.public_release.policy import validate_relative_path

_OBJECT_ID = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_TREE_LINE = re.compile(
    rb"^(?P<mode>[0-7]{6}) (?P<type>[^ ]+) (?P<oid>[0-9a-f]+) +(?P<size>[0-9-]+)\t(?P<path>.*)$",
    re.DOTALL,
)
_COMMIT_IDENTITY_LINE = re.compile(
    rb"^(?P<role>author|committer) (?P<identity>.*) [0-9]+ [+-][0-9]{4}$"
)


def _is_reparse_point(status: os.stat_result) -> bool:
    attributes = getattr(status, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(flag and attributes & flag)


def _validate_standalone_git_directory(git_dir: Path) -> os.stat_result:
    try:
        git_status = git_dir.lstat()
    except OSError as error:
        raise ScanFailure("git-directory-unreadable") from error
    if (
        not stat.S_ISDIR(git_status.st_mode)
        or git_dir.is_symlink()
        or _is_reparse_point(git_status)
    ):
        raise ScanFailure("git-directory-is-not-standalone", incomplete=False)
    return git_status


def _git_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
    )
    return environment


def _run_git_bounded(
    git: str,
    arguments: list[str],
    *,
    root: Path,
    budget: Budget,
    output_cap: int,
    input_bytes: bytes | None = None,
) -> bytes:
    budget.check_deadline()
    effective_output_cap = min(output_cap, budget.remaining_git_output_bytes())
    process = subprocess.Popen(
        [
            git,
            "-c",
            "core.fsmonitor=false",
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "submodule.recurse=false",
            "-c",
            "protocol.allow=never",
            *arguments,
        ],
        cwd=root,
        env=_git_environment(),
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout = bytearray()
    stderr = bytearray()
    lock = threading.Lock()
    overflow = threading.Event()
    reader_error = threading.Event()

    def read_stream(stream: object, destination: bytearray) -> None:
        try:
            while True:
                chunk = stream.read(64 * 1024)  # type: ignore[union-attr]
                if not chunk:
                    return
                with lock:
                    used = len(stdout) + len(stderr)
                    available = max(0, effective_output_cap + 1 - used)
                    destination.extend(chunk[:available])
                    if (
                        len(chunk) > available
                        or len(stdout) + len(stderr) > effective_output_cap
                    ):
                        overflow.set()
                        return
        except OSError:
            reader_error.set()

    readers = [
        threading.Thread(target=read_stream, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=read_stream, args=(process.stderr, stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()

    writer_error = threading.Event()

    def write_input() -> None:
        if input_bytes is None or process.stdin is None:
            return
        try:
            process.stdin.write(input_bytes)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            writer_error.set()

    writer = threading.Thread(target=write_input, daemon=True)
    writer.start()
    try:
        while process.poll() is None:
            if overflow.is_set():
                process.kill()
                break
            budget.check_deadline()
            sleep(min(0.02, budget.remaining_seconds()))
    except ScanFailure:
        process.kill()
        process.wait(timeout=5)
        raise
    try:
        process.wait(timeout=min(5.0, max(0.1, budget.remaining_seconds())))
    except subprocess.TimeoutExpired as error:
        process.kill()
        process.wait(timeout=5)
        raise ScanFailure("git-command-timeout") from error
    writer.join(timeout=min(1.0, max(0.1, budget.remaining_seconds())))
    for reader in readers:
        reader.join(timeout=min(1.0, max(0.1, budget.remaining_seconds())))
    if writer.is_alive() or any(reader.is_alive() for reader in readers):
        process.kill()
        raise ScanFailure("git-io-did-not-close")
    output_size = len(stdout) + len(stderr)
    budget.add_git_output(output_size)
    if overflow.is_set() or output_size > effective_output_cap:
        raise ScanFailure("git-output-byte-cap-exceeded")
    if reader_error.is_set() or writer_error.is_set():
        raise ScanFailure("git-io-failed")
    if process.returncode != 0:
        raise ScanFailure("git-command-failed")
    return bytes(stdout)


def _decode_single_line(output: bytes, code: str) -> str:
    try:
        value = output.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise ScanFailure(code) from error
    if not value or "\n" in value or "\r" in value:
        raise ScanFailure(code)
    return value


def _reject_linked_worktree_metadata(git_dir: Path) -> None:
    for name in ("commondir", "gitdir", "worktrees", "config.worktree"):
        path = git_dir / name
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ScanFailure("git-linked-worktree-metadata-unreadable") from error
        raise ScanFailure(
            "git-linked-worktree-or-common-directory-present",
            incomplete=False,
        )


def _verify_local_common_directory(
    git: str,
    *,
    root: Path,
    git_dir: Path,
    budget: Budget,
) -> None:
    common_directory = _decode_single_line(
        _run_git_bounded(
            git,
            ["rev-parse", "--path-format=absolute", "--git-common-dir"],
            root=root,
            budget=budget,
            output_cap=16 * 1024,
        ),
        "git-common-directory-invalid",
    )
    common_path = Path(common_directory)
    if not common_path.is_absolute():
        raise ScanFailure("git-common-directory-invalid")
    try:
        resolved_common = common_path.resolve(strict=True)
        resolved_local = git_dir.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ScanFailure("git-common-directory-invalid") from error
    if resolved_common != resolved_local:
        raise ScanFailure("git-common-directory-is-not-local", incomplete=False)


def _reject_external_object_sources(git_dir: Path) -> None:
    prohibited = (
        git_dir / "objects" / "info" / "alternates",
        git_dir / "objects" / "info" / "http-alternates",
        git_dir / "info" / "grafts",
    )
    for path in prohibited:
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ScanFailure("git-external-object-metadata-unreadable") from error
        raise ScanFailure("git-external-or-rewritten-objects-configured", incomplete=False)


def _reject_replace_refs(
    git: str,
    *,
    root: Path,
    budget: Budget,
) -> None:
    replace_refs = _run_git_bounded(
        git,
        ["for-each-ref", "--format=%(refname)", "refs/replace"],
        root=root,
        budget=budget,
        output_cap=64 * 1024,
    )
    if replace_refs.strip():
        raise ScanFailure("git-replace-refs-present", incomplete=False)


def _object_store_record(relative: str, status: os.stat_result) -> bytes:
    digest = sha256()
    fields = (
        relative,
        str(status.st_mode),
        str(status.st_size),
        str(status.st_mtime_ns),
        str(status.st_ctime_ns),
        str(status.st_dev),
        str(status.st_ino),
        str(status.st_nlink),
        str(getattr(status, "st_file_attributes", 0)),
        str(getattr(status, "st_reparse_tag", 0)),
    )
    for field in fields:
        encoded = field.encode("utf-8", errors="surrogatepass")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.digest()


def _validate_object_store(
    git_dir: Path,
    policy: PublicReleasePolicy,
    budget: Budget,
) -> str:
    git_status = _validate_standalone_git_directory(git_dir)
    object_root = git_dir / "objects"
    try:
        root_status = object_root.lstat()
    except OSError as error:
        raise ScanFailure("git-object-store-unreadable") from error
    if (
        not stat.S_ISDIR(root_status.st_mode)
        or object_root.is_symlink()
        or _is_reparse_point(root_status)
    ):
        raise ScanFailure("git-object-store-is-external-or-linked", incomplete=False)
    git_device = git_status.st_dev
    if git_device and root_status.st_dev and git_device != root_status.st_dev:
        raise ScanFailure("git-object-store-crosses-device-boundary", incomplete=False)
    records = [
        _object_store_record("<git-directory>", git_status),
        _object_store_record(".", root_status),
    ]
    stack = [(object_root, "")]
    entry_count = 0
    entry_cap = min(80_100, policy.bounds.max_git_objects * 4 + 100)
    while stack:
        budget.check_deadline()
        directory, prefix = stack.pop()
        try:
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    budget.check_deadline()
                    entry_count += 1
                    if entry_count > entry_cap:
                        raise ScanFailure("git-object-store-entry-cap-exceeded")
                    try:
                        # DirEntry.stat() reports st_nlink=0 on some Windows/Python
                        # combinations; os.lstat() preserves the actual hard-link count.
                        status = os.lstat(entry.path)
                    except OSError as error:
                        raise ScanFailure("git-object-store-entry-unreadable") from error
                    if entry.is_symlink() or _is_reparse_point(status):
                        raise ScanFailure(
                            "git-object-store-linked-entry-present", incomplete=False
                        )
                    if git_device and status.st_dev and status.st_dev != git_device:
                        raise ScanFailure(
                            "git-object-store-crosses-device-boundary", incomplete=False
                        )
                    relative = f"{prefix}/{entry.name}" if prefix else entry.name
                    records.append(_object_store_record(relative, status))
                    if stat.S_ISDIR(status.st_mode):
                        stack.append((Path(entry.path), relative))
                    elif not stat.S_ISREG(status.st_mode):
                        raise ScanFailure(
                            "git-object-store-special-entry-present", incomplete=False
                        )
                    elif status.st_nlink != 1:
                        raise ScanFailure(
                            "git-object-store-linked-file-present", incomplete=False
                        )
                    elif policy.require_root_commit and entry.name.casefold().endswith(
                        (".pack", ".idx", ".rev")
                    ):
                        raise ScanFailure(
                            "packed-object-store-not-allowed-for-root-candidate",
                            incomplete=False,
                        )
        except OSError as error:
            raise ScanFailure("git-object-store-directory-unreadable") from error
    topology = sha256()
    for record in sorted(records):
        topology.update(record)
    return topology.hexdigest()


def _parse_tree(
    output: bytes,
    policy: PublicReleasePolicy,
    budget: Budget,
) -> tuple[list[tuple[str, str, int, str]], set[str]]:
    entries: list[tuple[str, str, int, str]] = []
    directories: set[str] = set()
    folded: dict[str, str] = {}
    for raw in output.split(b"\0"):
        if not raw:
            continue
        match = _TREE_LINE.fullmatch(raw)
        if match is None:
            raise ScanFailure("git-tree-entry-unparseable")
        mode = match.group("mode")
        object_type = match.group("type")
        if mode == b"120000":
            raise ScanFailure("git-symlink-present", incomplete=False)
        if mode == b"160000" or object_type == b"commit":
            raise ScanFailure("git-submodule-present", incomplete=False)
        is_tree = object_type == b"tree" and mode == b"040000"
        is_blob = object_type == b"blob" and mode in {b"100644", b"100755"}
        if not is_tree and not is_blob:
            raise ScanFailure("unsupported-git-tree-entry", incomplete=False)
        try:
            relative_raw = match.group("path").decode("utf-8")
        except UnicodeDecodeError as error:
            raise ScanFailure("git-path-not-utf8") from error
        try:
            relative = validate_relative_path(
                relative_raw,
                max_bytes=policy.bounds.max_path_bytes,
                label="Git tree path",
            )
        except ValueError as error:
            raise ScanFailure("invalid-git-tree-path") from error
        if PurePosixPath(relative).name.casefold() in {".git", ".gitmodules"}:
            raise ScanFailure("git-metadata-present", path=relative, incomplete=False)
        folded_path = relative.casefold()
        if folded_path in folded and folded[folded_path] != relative:
            raise ScanFailure("case-fold-path-collision", path=relative, incomplete=False)
        folded[folded_path] = relative
        oid = match.group("oid").decode("ascii")
        if not _OBJECT_ID.fullmatch(oid):
            raise ScanFailure("git-object-id-invalid")
        budget.add_path()
        budget.add_git_objects(1)
        if is_tree:
            if match.group("size") != b"-":
                raise ScanFailure("git-tree-size-invalid")
            directories.add(relative)
            continue
        try:
            size = int(match.group("size"))
        except ValueError as error:
            raise ScanFailure("git-blob-size-invalid") from error
        if size < 0 or size > policy.bounds.max_file_bytes:
            raise ScanFailure("per-file-byte-cap-exceeded", path=relative)
        entries.append((relative, oid, size, mode.decode("ascii")))
    return entries, directories


def _parse_batch_blobs(
    output: bytes,
    requested: list[tuple[str, str, int, str]],
) -> dict[str, bytes]:
    cursor = 0
    by_path: dict[str, bytes] = {}
    for path, expected_oid, expected_size, _git_mode in requested:
        header_end = output.find(b"\n", cursor)
        if header_end < 0:
            raise ScanFailure("git-batch-output-truncated")
        header = output[cursor:header_end].split(b" ")
        if len(header) != 3:
            raise ScanFailure("git-batch-header-invalid")
        try:
            returned_oid = header[0].decode("ascii")
            returned_type = header[1].decode("ascii")
            returned_size = int(header[2])
        except (UnicodeDecodeError, ValueError) as error:
            raise ScanFailure("git-batch-header-invalid") from error
        if (
            returned_oid != expected_oid
            or returned_type != "blob"
            or returned_size != expected_size
        ):
            raise ScanFailure("git-batch-object-mismatch")
        start = header_end + 1
        end = start + returned_size
        if end >= len(output) or output[end : end + 1] != b"\n":
            raise ScanFailure("git-batch-output-truncated")
        by_path[path] = output[start:end]
        cursor = end + 1
    if cursor != len(output):
        raise ScanFailure("git-batch-output-unexpected-trailer")
    return by_path


def read_committed_tree(
    root: Path,
    commit: str,
    policy: PublicReleasePolicy,
    inventory: ApprovedInventory,
    budget: Budget,
) -> CandidateSnapshot:
    if not _OBJECT_ID.fullmatch(commit):
        raise ScanFailure("commit-must-be-full-lowercase-object-id", incomplete=False)
    git = shutil.which("git")
    if git is None:
        raise ScanFailure("git-executable-not-found")
    absolute_root = Path(os.path.abspath(root))
    try:
        root_status = absolute_root.lstat()
    except OSError as error:
        raise ScanFailure("repository-root-unreadable") from error
    if not stat.S_ISDIR(root_status.st_mode) or absolute_root.is_symlink() or _is_reparse_point(
        root_status
    ):
        raise ScanFailure("repository-root-not-plain-directory", incomplete=False)

    git_dir = absolute_root / ".git"
    _validate_standalone_git_directory(git_dir)
    _reject_linked_worktree_metadata(git_dir)
    top_level = _decode_single_line(
        _run_git_bounded(
            git,
            ["rev-parse", "--show-toplevel"],
            root=absolute_root,
            budget=budget,
            output_cap=16 * 1024,
        ),
        "git-top-level-invalid",
    )
    if Path(top_level).resolve() != absolute_root.resolve():
        raise ScanFailure("repository-root-is-not-top-level", incomplete=False)
    _verify_local_common_directory(
        git,
        root=absolute_root,
        git_dir=git_dir,
        budget=budget,
    )
    _reject_external_object_sources(git_dir)
    initial_object_store_signature = _validate_object_store(git_dir, policy, budget)
    _reject_replace_refs(git, root=absolute_root, budget=budget)

    resolved_commit = _decode_single_line(
        _run_git_bounded(
            git,
            ["rev-parse", "--verify", "--end-of-options", f"{commit}^{{commit}}"],
            root=absolute_root,
            budget=budget,
            output_cap=16 * 1024,
        ),
        "commit-resolution-invalid",
    )
    if resolved_commit != commit:
        raise ScanFailure("commit-resolution-mismatch")
    commit_object = _run_git_bounded(
        git,
        ["cat-file", "-p", resolved_commit],
        root=absolute_root,
        budget=budget,
        output_cap=256 * 1024,
    )
    commit_headers = commit_object.split(b"\n\n", 1)[0]
    commit_message = (
        commit_object.split(b"\n\n", 1)[1] if b"\n\n" in commit_object else b""
    )
    identity_lines = []
    for line in commit_headers.splitlines():
        match = _COMMIT_IDENTITY_LINE.fullmatch(line)
        if match is not None:
            identity_lines.append(match.group("role") + b" " + match.group("identity"))
    commit_identity = b"\n".join(identity_lines)
    parent_lines = [
        line.removeprefix(b"parent ").decode("ascii")
        for line in commit_headers.splitlines()
        if line.startswith(b"parent ")
    ]
    parent_count = len(parent_lines)
    budget.add_git_objects(1)
    if policy.require_root_commit and parent_count != 0:
        raise ScanFailure("candidate-commit-is-not-root", incomplete=False)
    if not policy.require_root_commit and tuple(parent_lines) != policy.approved_parent_commits:
        raise ScanFailure("candidate-parent-list-not-approved", incomplete=False)
    tree = _decode_single_line(
        _run_git_bounded(
            git,
            ["rev-parse", "--verify", "--end-of-options", f"{resolved_commit}^{{tree}}"],
            root=absolute_root,
            budget=budget,
            output_cap=16 * 1024,
        ),
        "tree-resolution-invalid",
    )
    budget.add_git_objects(1)

    clean_state = "not-required-by-policy"

    tree_output = _run_git_bounded(
        git,
        ["ls-tree", "-rz", "-l", "-t", "-r", "--full-tree", tree],
        root=absolute_root,
        budget=budget,
        output_cap=policy.bounds.max_git_output_bytes,
    )
    entries, directories = _parse_tree(tree_output, policy, budget)
    expected_directories = {
        parent.as_posix()
        for path, _oid, _size, _git_mode in entries
        for parent in PurePosixPath(path).parents
        if parent.as_posix() != "."
    }
    if directories != expected_directories:
        raise ScanFailure("git-tree-directory-inventory-mismatch")
    approved_by_path = {record.path: record for record in inventory.files}
    entry_by_path = {path: (oid, size, git_mode) for path, oid, size, git_mode in entries}
    if len(entry_by_path) != len(entries):
        raise ScanFailure("duplicate-git-tree-path", incomplete=False)
    if set(entry_by_path) != set(approved_by_path):
        code = (
            "unexpected-candidate-file"
            if set(entry_by_path) - set(approved_by_path)
            else "missing-candidate-file"
        )
        raise ScanFailure(code, incomplete=False)
    total_bytes = sum(size for _path, _oid, size, _git_mode in entries)
    if total_bytes > policy.bounds.max_total_bytes:
        raise ScanFailure("aggregate-byte-cap-exceeded")
    for path, _oid, size, git_mode in entries:
        if size != approved_by_path[path].bytes:
            raise ScanFailure("approved-size-mismatch", path=path, incomplete=False)
        if git_mode != approved_by_path[path].git_mode:
            raise ScanFailure("approved-git-mode-mismatch", path=path, incomplete=False)

    request = b"".join(
        oid.encode("ascii") + b"\n" for _path, oid, _size, _git_mode in entries
    )
    batch_output = _run_git_bounded(
        git,
        ["cat-file", "--batch"],
        root=absolute_root,
        budget=budget,
        output_cap=policy.bounds.max_git_output_bytes,
        input_bytes=request,
    )
    contents = _parse_batch_blobs(batch_output, entries)
    observations: list[FileObservation] = []
    for path, _oid, size, git_mode in sorted(entries):
        content = contents[path]
        budget.add_file(size)
        budget.add_bytes_read(len(content))
        digest = sha256(content).hexdigest()
        if digest != approved_by_path[path].sha256:
            raise ScanFailure("approved-hash-mismatch", path=path, incomplete=False)
        observations.append(
            FileObservation(
                path=path,
                bytes=size,
                sha256=digest,
                git_mode=git_mode,
            )
        )

    worktree_usage: dict[str, int] = {}
    if policy.require_clean_worktree:
        worktree_budget = Budget(bounds=policy.bounds, started=budget.started)
        try:
            worktree_snapshot = read_staging_filesystem(
                absolute_root,
                policy,
                inventory,
                worktree_budget,
                allow_root_git_directory=True,
            )
        except ScanFailure as error:
            raise ScanFailure("Git-worktree-not-clean", incomplete=False) from error
        worktree_usage = {
            f"worktree_{key}": value for key, value in worktree_snapshot.usage.items()
        }
        clean_state = "clean-worktree"

    _validate_standalone_git_directory(git_dir)
    _reject_linked_worktree_metadata(git_dir)
    _verify_local_common_directory(
        git,
        root=absolute_root,
        git_dir=git_dir,
        budget=budget,
    )
    _reject_external_object_sources(git_dir)
    terminal_object_store_signature = _validate_object_store(git_dir, policy, budget)
    if terminal_object_store_signature != initial_object_store_signature:
        raise ScanFailure("git-object-store-changed-during-scan")
    _reject_replace_refs(git, root=absolute_root, budget=budget)

    usage = budget.usage()
    usage.update(worktree_usage)

    return CandidateSnapshot(
        observations=tuple(observations),
        contents=contents,
        subject={
            "root": str(absolute_root),
            "commit": resolved_commit,
            "tree": tree,
            "root_commit_required": policy.require_root_commit,
            "approved_parent_count": len(policy.approved_parent_commits),
        },
        stable=True,
        clean_state=clean_state,
        usage=usage,
        commit_metadata=commit_object,
        commit_message=commit_message,
        commit_identity=commit_identity,
    )
