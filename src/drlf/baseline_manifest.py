"""Build or verify the bounded public-baseline content manifest."""

from __future__ import annotations

import argparse
import json
import os
import stat
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any

BASELINE_VERSION = "0.1.0"
SOURCE_REVISION = "drlf-v0.1.0-public-baseline"
MANIFEST_PATH = PurePosixPath("baseline-manifest.json")
MAX_PATHS = 5_000
MAX_PATH_BYTES = 512
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024

EXCLUDED_PREFIXES = (
    PurePosixPath(".drlf"),
    PurePosixPath(".git"),
    PurePosixPath(".agents/skills-local"),
    PurePosixPath("data"),
    PurePosixPath("operations/public-sync"),
    PurePosixPath("reports/generated"),
    PurePosixPath("research/cases"),
    PurePosixPath("research/knowledge/local"),
)
FORBIDDEN_COMPONENTS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
}


def _is_reparse(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(flag and attributes & flag)


def _is_within(path: PurePosixPath, prefix: PurePosixPath) -> bool:
    return path == prefix or prefix in path.parents


def _excluded(path: PurePosixPath) -> bool:
    if path == MANIFEST_PATH:
        return True
    if any(part.casefold() in FORBIDDEN_COMPONENTS for part in path.parts):
        return True
    return any(_is_within(path, prefix) for prefix in EXCLUDED_PREFIXES)


def _file_digest(path: Path, expected_size: int) -> str:
    digest = sha256()
    observed = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            observed += len(chunk)
            if observed > MAX_FILE_BYTES:
                raise ValueError(f"baseline file exceeds byte cap: {path}")
            digest.update(chunk)
    if observed != expected_size:
        raise ValueError(f"baseline file changed while hashing: {path}")
    return digest.hexdigest()


def build_manifest_document(root: Path) -> dict[str, Any]:
    """Inventory the exact baseline-managed bytes below a plain directory."""
    root = root.resolve()
    root_metadata = root.lstat()
    if not stat.S_ISDIR(root_metadata.st_mode) or root.is_symlink() or _is_reparse(root_metadata):
        raise ValueError("baseline root must be a plain directory")

    records: list[dict[str, Any]] = []
    total_bytes = 0
    visited_paths = 0
    for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        safe_directories: list[str] = []
        for name in sorted(directories, key=str.casefold):
            directory = current_path / name
            relative = PurePosixPath(directory.relative_to(root).as_posix())
            visited_paths += 1
            if visited_paths > MAX_PATHS:
                raise ValueError("baseline tree exceeds path cap")
            if _excluded(relative):
                continue
            metadata = directory.lstat()
            if directory.is_symlink() or _is_reparse(metadata):
                raise ValueError(f"baseline tree contains a linked directory: {relative}")
            safe_directories.append(name)
        directories[:] = safe_directories

        for name in sorted(filenames, key=str.casefold):
            path = current_path / name
            relative = PurePosixPath(path.relative_to(root).as_posix())
            visited_paths += 1
            if visited_paths > MAX_PATHS:
                raise ValueError("baseline tree exceeds path cap")
            if _excluded(relative):
                continue
            if len(relative.as_posix().encode("utf-8")) > MAX_PATH_BYTES:
                raise ValueError(f"baseline path exceeds byte cap: {relative}")
            metadata = path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or path.is_symlink()
                or _is_reparse(metadata)
                or metadata.st_nlink != 1
            ):
                raise ValueError(f"baseline path is not a plain single-link file: {relative}")
            if metadata.st_size > MAX_FILE_BYTES:
                raise ValueError(f"baseline file exceeds byte cap: {relative}")
            total_bytes += metadata.st_size
            if total_bytes > MAX_TOTAL_BYTES:
                raise ValueError("baseline files exceed aggregate byte cap")
            records.append(
                {
                    "path": relative.as_posix(),
                    "bytes": metadata.st_size,
                    "sha256": _file_digest(path, metadata.st_size),
                    "mode": "100644",
                }
            )

    records.sort(key=lambda record: record["path"].casefold())
    folded = [record["path"].casefold() for record in records]
    if len(folded) != len(set(folded)):
        raise ValueError("baseline tree contains a case-fold path collision")
    return {
        "schema_version": 1,
        "baseline_version": BASELINE_VERSION,
        "source_revision": SOURCE_REVISION,
        "files": records,
    }


def canonical_manifest_bytes(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def check_manifest(root: Path) -> bool:
    expected = canonical_manifest_bytes(build_manifest_document(root))
    manifest = root.resolve() / MANIFEST_PATH.as_posix()
    return manifest.is_file() and manifest.read_bytes() == expected


def write_manifest(root: Path) -> str:
    root = root.resolve()
    content = canonical_manifest_bytes(build_manifest_document(root))
    destination = root / MANIFEST_PATH.as_posix()
    temporary = destination.with_name(f".{destination.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise ValueError(f"temporary manifest path already exists: {temporary}")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return sha256(content).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    if arguments.check:
        if not check_manifest(arguments.root):
            print("baseline-manifest.json does not match the managed tree")
            return 1
        print("baseline-manifest.json matches the managed tree")
        return 0
    digest = write_manifest(arguments.root)
    print(f"Wrote baseline-manifest.json ({digest})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
