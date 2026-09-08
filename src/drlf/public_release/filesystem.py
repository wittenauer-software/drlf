from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath

from drlf.public_release.bounds import Budget, ScanFailure
from drlf.public_release.models import (
    ApprovedInventory,
    CandidateSnapshot,
    FileObservation,
    PublicReleasePolicy,
)
from drlf.public_release.policy import validate_relative_path

_GIT_METADATA_NAMES = {".git", ".gitmodules"}


@dataclass(frozen=True)
class _Entry:
    path: str
    absolute_path: Path
    kind: str
    size: int
    mtime_ns: int
    mode: int
    device: int
    inode: int
    link_count: int


def _is_reparse_point(status: os.stat_result) -> bool:
    attribute = getattr(status, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(flag and attribute & flag)


def _entry_signature(entry: _Entry) -> tuple[object, ...]:
    if entry.kind == "directory":
        # Directory mtimes can be lazily updated by NTFS after a completed child write.
        # The exact name set is compared separately and every file has stricter checks.
        return (entry.kind, entry.mode, entry.device, entry.inode)
    return (
        entry.kind,
        entry.size,
        entry.mtime_ns,
        entry.mode,
        entry.device,
        entry.inode,
        entry.link_count,
    )


def _enumerate(
    root: Path,
    policy: PublicReleasePolicy,
    budget: Budget,
    *,
    account: bool,
    allow_root_git_directory: bool,
) -> dict[str, _Entry]:
    discovered: dict[str, _Entry] = {}
    folded: dict[str, str] = {}
    stack: list[tuple[Path, str]] = [(root, "")]
    local_count = 0

    while stack:
        budget.check_deadline()
        directory, prefix = stack.pop()
        try:
            entries: list[os.DirEntry[str]] = []
            with os.scandir(directory) as iterator:
                for item in iterator:
                    if allow_root_git_directory and not prefix and item.name == ".git":
                        continue
                    local_count += 1
                    if local_count > policy.bounds.max_paths:
                        raise ScanFailure("path-count-cap-exceeded")
                    entries.append(item)
        except OSError as error:
            raise ScanFailure("directory-unreadable", path=prefix or None) from error
        entries.sort(key=lambda item: item.name.encode("utf-8", errors="surrogatepass"))
        for item in entries:
            if account:
                budget.add_path()
            else:
                budget.check_deadline()
            raw_relative = f"{prefix}/{item.name}" if prefix else item.name
            try:
                relative = validate_relative_path(
                    raw_relative,
                    max_bytes=policy.bounds.max_path_bytes,
                    label="candidate path",
                )
            except ValueError as error:
                raise ScanFailure("invalid-candidate-path") from error
            folded_path = relative.casefold()
            if folded_path in folded and folded[folded_path] != relative:
                raise ScanFailure("case-fold-path-collision", path=relative, incomplete=False)
            folded[folded_path] = relative
            if PurePosixPath(relative).name.casefold() in _GIT_METADATA_NAMES:
                raise ScanFailure("git-metadata-present", path=relative, incomplete=False)

            try:
                status = os.lstat(item.path)
            except OSError as error:
                raise ScanFailure("path-stat-failed", path=relative) from error
            if item.is_symlink() or _is_reparse_point(status):
                raise ScanFailure("link-or-reparse-point-present", path=relative, incomplete=False)
            if stat.S_ISDIR(status.st_mode):
                kind = "directory"
            elif stat.S_ISREG(status.st_mode):
                kind = "file"
                if status.st_nlink != 1:
                    raise ScanFailure(
                        "candidate-hard-linked-file-present",
                        path=relative,
                        incomplete=False,
                    )
            else:
                raise ScanFailure("special-file-present", path=relative, incomplete=False)
            discovered[relative] = _Entry(
                path=relative,
                absolute_path=Path(item.path),
                kind=kind,
                size=status.st_size,
                mtime_ns=status.st_mtime_ns,
                mode=status.st_mode,
                device=status.st_dev,
                inode=status.st_ino,
                link_count=status.st_nlink,
            )
            if kind == "directory":
                stack.append((Path(item.path), relative))

    return discovered


def _expected_directories(inventory: ApprovedInventory) -> set[str]:
    result: set[str] = set()
    for record in inventory.files:
        parent = PurePosixPath(record.path).parent
        while parent.parts and parent.as_posix() != ".":
            result.add(parent.as_posix())
            parent = parent.parent
    return result


def _staging_git_mode(entry: _Entry, approved_mode: str) -> str:
    """Map filesystem metadata to Git's portable regular-file mode.

    Git persists only whether any executable bit is set for ordinary files. Windows
    does not expose a reliable equivalent for a future Git index, so staging mode
    accepts only the portable non-executable default there. An intended executable
    must be validated from the resulting committed tree on a platform that can set
    it explicitly; staging fails closed instead of pretending to observe it.
    """
    if os.name == "nt":
        if approved_mode == "100755":
            raise ScanFailure(
                "executable-git-mode-not-verifiable-on-platform",
                path=entry.path,
                incomplete=False,
            )
        return "100644"
    executable_mask = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    return "100755" if entry.mode & executable_mask else "100644"


def _read_file(entry: _Entry, budget: Budget) -> bytes:
    budget.add_file(entry.size)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(entry.absolute_path, flags)
    except OSError as error:
        raise ScanFailure("file-open-failed", path=entry.path) from error
    try:
        opened = os.fstat(descriptor)
        if _is_reparse_point(opened) or not stat.S_ISREG(opened.st_mode):
            raise ScanFailure("file-type-changed", path=entry.path)
        if opened.st_nlink != 1:
            raise ScanFailure(
                "candidate-hard-linked-file-present", path=entry.path, incomplete=False
            )
        if (
            opened.st_size != entry.size
            or opened.st_mtime_ns != entry.mtime_ns
            or (entry.device != 0 and opened.st_dev != entry.device)
            or (entry.inode != 0 and opened.st_ino != entry.inode)
        ):
            raise ScanFailure("file-changed-before-read", path=entry.path)
        content = bytearray()
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            remaining = entry.size
            while remaining:
                budget.check_deadline()
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                budget.add_bytes_read(len(chunk))
                content.extend(chunk)
                remaining -= len(chunk)
        after = os.fstat(descriptor)
        if len(content) != entry.size or (
            after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
        ):
            raise ScanFailure("file-changed-during-read", path=entry.path)
        return bytes(content)
    finally:
        os.close(descriptor)


def _verification_hash(entry: _Entry, budget: Budget) -> str:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(entry.absolute_path, flags)
    except OSError as error:
        raise ScanFailure("verification-file-open-failed", path=entry.path) from error
    try:
        opened = os.fstat(descriptor)
        if _is_reparse_point(opened) or not stat.S_ISREG(opened.st_mode):
            raise ScanFailure("verification-file-type-changed", path=entry.path)
        if opened.st_nlink != 1:
            raise ScanFailure(
                "candidate-hard-linked-file-present", path=entry.path, incomplete=False
            )
        if opened.st_size != entry.size or opened.st_mtime_ns != entry.mtime_ns:
            raise ScanFailure("verification-file-changed-before-read", path=entry.path)
        digest = sha256()
        consumed = 0
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            while consumed < opened.st_size:
                budget.check_deadline()
                chunk = handle.read(min(1024 * 1024, opened.st_size - consumed))
                if not chunk:
                    break
                budget.add_verification_bytes(len(chunk))
                digest.update(chunk)
                consumed += len(chunk)
        after = os.fstat(descriptor)
        if consumed != opened.st_size or (
            after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
        ):
            raise ScanFailure("verification-file-changed-during-read", path=entry.path)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def read_staging_filesystem(
    root: Path,
    policy: PublicReleasePolicy,
    inventory: ApprovedInventory,
    budget: Budget,
    *,
    allow_root_git_directory: bool = False,
) -> CandidateSnapshot:
    absolute_root = Path(os.path.abspath(root))
    try:
        root_status = absolute_root.lstat()
    except OSError as error:
        raise ScanFailure("candidate-root-unreadable") from error
    if not stat.S_ISDIR(root_status.st_mode):
        raise ScanFailure("candidate-root-not-directory")
    if absolute_root.is_symlink() or _is_reparse_point(root_status):
        raise ScanFailure("candidate-root-link-or-reparse-point", incomplete=False)

    initial = _enumerate(
        absolute_root,
        policy,
        budget,
        account=True,
        allow_root_git_directory=allow_root_git_directory,
    )
    actual_files = {path for path, entry in initial.items() if entry.kind == "file"}
    actual_directories = {path for path, entry in initial.items() if entry.kind == "directory"}
    approved_files = {record.path: record for record in inventory.files}
    expected_files = set(approved_files)
    expected_directories = _expected_directories(inventory)
    if actual_files != expected_files:
        code = (
            "unexpected-candidate-file"
            if actual_files - expected_files
            else "missing-candidate-file"
        )
        raise ScanFailure(code, incomplete=False)
    if actual_directories != expected_directories:
        code = (
            "unexpected-candidate-directory"
            if actual_directories - expected_directories
            else "missing-candidate-directory"
        )
        raise ScanFailure(code, incomplete=False)

    total_declared = sum(initial[path].size for path in actual_files)
    if total_declared > policy.bounds.max_total_bytes:
        raise ScanFailure("aggregate-byte-cap-exceeded")

    contents: dict[str, bytes] = {}
    observations: list[FileObservation] = []
    for relative in sorted(actual_files):
        entry = initial[relative]
        approved = approved_files[relative]
        if entry.size != approved.bytes:
            raise ScanFailure("approved-size-mismatch", path=relative, incomplete=False)
        observed_mode = _staging_git_mode(entry, approved.git_mode)
        if observed_mode != approved.git_mode:
            raise ScanFailure("approved-git-mode-mismatch", path=relative, incomplete=False)
        content = _read_file(entry, budget)
        digest = sha256(content).hexdigest()
        if digest != approved.sha256:
            raise ScanFailure("approved-hash-mismatch", path=relative, incomplete=False)
        contents[relative] = content
        observations.append(
            FileObservation(
                path=relative,
                bytes=len(content),
                sha256=digest,
                git_mode=observed_mode,
            )
        )

    final = _enumerate(
        absolute_root,
        policy,
        budget,
        account=False,
        allow_root_git_directory=allow_root_git_directory,
    )
    if set(initial) != set(final) or any(
        _entry_signature(initial[path]) != _entry_signature(final[path]) for path in initial
    ):
        raise ScanFailure("candidate-changed-during-scan")
    for relative in sorted(actual_files):
        if _verification_hash(final[relative], budget) != approved_files[relative].sha256:
            raise ScanFailure("candidate-changed-after-snapshot", path=relative)
    terminal = _enumerate(
        absolute_root,
        policy,
        budget,
        account=False,
        allow_root_git_directory=allow_root_git_directory,
    )
    if set(final) != set(terminal) or any(
        _entry_signature(final[path]) != _entry_signature(terminal[path]) for path in final
    ):
        raise ScanFailure("candidate-changed-during-verification")

    return CandidateSnapshot(
        observations=tuple(observations),
        contents=contents,
        subject={"root": str(absolute_root), "commit": None, "tree": None},
        stable=True,
        clean_state="stable-exact-filesystem-snapshot",
        usage=budget.usage(),
    )
