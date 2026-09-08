from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import unicodedata
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

import yaml

from drlf.doctor_models import DoctorCheck, DoctorReport

BASELINE_SCHEMA_VERSION = 1
WORKSPACE_SCHEMA_VERSION = 1
DISTRIBUTION_SCHEMA_VERSION = 1
DEFAULT_MAX_BASELINE_PATHS = 5_000
DEFAULT_MAX_BASELINE_FILE_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_BASELINE_TOTAL_BYTES = 256 * 1024 * 1024
MAX_CONTROL_FILE_BYTES = 4 * 1024 * 1024
GIT_TIMEOUT_SECONDS = 15

CONTROL_DIRECTORY = Path(".drlf")
DISTRIBUTION_MARKER = CONTROL_DIRECTORY / "distribution.json"
WORKSPACE_MARKER = CONTROL_DIRECTORY / "workspace.json"
INSTALLED_BASELINE = CONTROL_DIRECTORY / "baseline.json"
INCOMPLETE_UPGRADE = CONTROL_DIRECTORY / "UPGRADE_INCOMPLETE.json"
TRANSACTION_DIRECTORY = CONTROL_DIRECTORY / "transactions"

LOCAL_DIRECTORIES = (
    Path("research/cases"),
    Path("research/knowledge/local/cards"),
    Path(".agents/skills-local"),
    Path("data/raw"),
    Path("data/derived"),
    Path("data/cache"),
    Path("reports/generated"),
)
LOCAL_INDEX = Path("research/knowledge/local/index.yaml")

_LOCAL_OWNED_PREFIXES = (
    PurePosixPath("research/cases"),
    PurePosixPath("research/knowledge/local"),
    PurePosixPath(".agents/skills-local"),
    PurePosixPath("data"),
    PurePosixPath("reports/generated"),
)
_FORBIDDEN_COMPONENTS = {
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
_FORBIDDEN_PREFIXES = (
    PurePosixPath("operations/public-sync"),
    PurePosixPath("dist"),
    PurePosixPath("build"),
    PurePosixPath("reports/generated"),
    PurePosixPath("data"),
    PurePosixPath(".drlf"),
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_TRANSACTION_ID_RE = re.compile(r"upgrade-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}")
_SEMVER_RE = re.compile(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}

PRIVATE_SYSTEM_DISTRIBUTION = "private-system-of-record"
PUBLIC_BASELINE_DISTRIBUTION = "public-baseline"
PRIVATE_WORKSPACE_KIND = "private-research-workspace"


class WorkspaceLifecycleError(RuntimeError):
    """A fail-closed workspace bootstrap, inspection, upgrade, or recovery error."""


@dataclass(frozen=True)
class BaselineFile:
    """One exact public-baseline file declared by a source or installed manifest."""

    path: str
    bytes: int
    sha256: str
    mode: Literal["100644", "100755"]


@dataclass(frozen=True)
class BaselineManifest:
    """Validated public-baseline content inventory."""

    baseline_version: str
    source_revision: str
    files: tuple[BaselineFile, ...]
    source_manifest_sha256: str

    def as_installed_dict(self, *, installed_at: str) -> dict[str, Any]:
        return {
            "schema_version": BASELINE_SCHEMA_VERSION,
            "baseline_version": self.baseline_version,
            "source_revision": self.source_revision,
            "source_manifest_sha256": self.source_manifest_sha256,
            "installed_at": installed_at,
            "files": [asdict(file) for file in self.files],
        }


@dataclass(frozen=True)
class WorkspaceBootstrapResult:
    status: Literal["created", "already_current"]
    destination: str
    baseline_version: str
    baseline_manifest_sha256: str
    managed_file_count: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UpgradeAction:
    path: str
    classification: str
    action: Literal["none", "add", "replace", "preserve", "block"]
    detail: str


@dataclass(frozen=True)
class BaselineUpgradePlan:
    from_version: str
    to_version: str
    installed_baseline_sha256: str
    new_manifest_sha256: str
    actions: tuple[UpgradeAction, ...]
    blocked: bool
    requires_skill_approval: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "plan_kind": "drlf-baseline-upgrade",
            "from_version": self.from_version,
            "to_version": self.to_version,
            "installed_baseline_sha256": self.installed_baseline_sha256,
            "new_manifest_sha256": self.new_manifest_sha256,
            "blocked": self.blocked,
            "requires_skill_approval": self.requires_skill_approval,
            "actions": [asdict(action) for action in self.actions],
        }


@dataclass(frozen=True)
class WorkspaceUpgradeResult:
    status: Literal["upgraded", "already_current"]
    from_version: str
    to_version: str
    added: int
    replaced: int
    preserved: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkspaceRecoveryResult:
    status: Literal["recovered", "nothing_to_recover"]
    transaction_id: str | None
    restored: int
    removed_additions: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkspaceFinalizeResult:
    status: Literal["finalized", "already_finalized"]
    commit: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _read_bounded(path: Path, byte_cap: int, label: str) -> bytes:
    if not path.is_file():
        raise WorkspaceLifecycleError(f"{label} does not exist or is not a regular file: {path}")
    if _is_link_or_reparse(path):
        raise WorkspaceLifecycleError(f"{label} must not be a link or reparse point: {path}")
    if path.stat().st_nlink != 1:
        raise WorkspaceLifecycleError(f"{label} must not be a hard-linked file: {path}")
    with path.open("rb") as handle:
        content = handle.read(byte_cap + 1)
    if len(content) > byte_cap:
        raise WorkspaceLifecycleError(f"{label} exceeds the {byte_cap:,}-byte safety limit")
    return content


def _read_json(path: Path, byte_cap: int, label: str) -> tuple[dict[str, Any], bytes]:
    content = _read_bounded(path, byte_cap, label)
    try:

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, item in pairs:
                if key in result:
                    raise WorkspaceLifecycleError(f"{label} contains duplicate key {key!r}")
                result[key] = item
            return result

        value = json.loads(content, object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise WorkspaceLifecycleError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise WorkspaceLifecycleError(f"{label} must contain a JSON object")
    return value, content


def load_distribution_identity(root: Path) -> str:
    """Return a strict DRLF distribution identity from one repository root."""
    root = root.resolve()
    marker, _raw = _read_json(
        root / DISTRIBUTION_MARKER,
        MAX_CONTROL_FILE_BYTES,
        "DRLF distribution marker",
    )
    if marker.get("schema_version") != DISTRIBUTION_SCHEMA_VERSION:
        raise WorkspaceLifecycleError("unsupported DRLF distribution marker schema_version")
    distribution_kind = marker.get("distribution_kind")
    if distribution_kind == PRIVATE_SYSTEM_DISTRIBUTION:
        if set(marker) != {"schema_version", "distribution_kind"}:
            raise WorkspaceLifecycleError(
                "private system distribution marker has unexpected fields"
            )
        return distribution_kind
    if distribution_kind == PUBLIC_BASELINE_DISTRIBUTION:
        if set(marker) != {"schema_version", "distribution_kind", "product"}:
            raise WorkspaceLifecycleError(
                "public baseline distribution marker has unexpected fields"
            )
        if marker.get("product") != "DRLF":
            raise WorkspaceLifecycleError("public baseline distribution marker has wrong product")
        return distribution_kind
    raise WorkspaceLifecycleError("unknown DRLF distribution kind")


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _is_relative_to(path: PurePosixPath, parent: PurePosixPath) -> bool:
    return path == parent or parent in path.parents


def _validate_relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 512:
        raise WorkspaceLifecycleError(
            "baseline path must be a nonempty UTF-8 string of at most 512 bytes"
        )
    if "\\" in value or "\x00" in value:
        raise WorkspaceLifecycleError(f"baseline path must use safe POSIX separators: {value!r}")
    path = PurePosixPath(value)
    if unicodedata.normalize("NFC", value) != value:
        raise WorkspaceLifecycleError(f"baseline path must use NFC Unicode: {value!r}")
    for part in path.parts:
        stem = part.split(".", 1)[0].upper()
        if (
            part.endswith((" ", "."))
            or stem in _WINDOWS_RESERVED_NAMES
            or any(character in '<>:"|?*' or ord(character) < 32 for character in part)
        ):
            raise WorkspaceLifecycleError(f"baseline path is not portable to Windows: {value!r}")
    if (
        path.is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise WorkspaceLifecycleError(f"baseline path is not normalized and relative: {value!r}")
    if any(part.lower() in _FORBIDDEN_COMPONENTS for part in path.parts):
        raise WorkspaceLifecycleError(f"baseline path uses a forbidden component: {value}")
    if path == PurePosixPath(".env"):
        raise WorkspaceLifecycleError("the local .env file cannot be baseline-managed")
    if any(_is_relative_to(path, prefix) for prefix in _FORBIDDEN_PREFIXES):
        raise WorkspaceLifecycleError(f"baseline path uses a forbidden prefix: {value}")
    if any(_is_relative_to(path, prefix) for prefix in _LOCAL_OWNED_PREFIXES):
        raise WorkspaceLifecycleError(
            f"baseline path collides with a local-owned namespace: {value}"
        )
    return path


def _validate_text(value: object, label: str, *, max_bytes: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkspaceLifecycleError(f"{label} must be a nonempty string")
    normalized = value.strip()
    if "\x00" in normalized or "\r" in normalized or "\n" in normalized:
        raise WorkspaceLifecycleError(f"{label} must be one line")
    if len(normalized.encode("utf-8")) > max_bytes:
        raise WorkspaceLifecycleError(f"{label} exceeds the {max_bytes}-byte safety limit")
    return normalized


def _semantic_version(value: str) -> tuple[int, int, int] | None:
    match = _SEMVER_RE.fullmatch(value)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def load_source_baseline_manifest(
    source_root: Path,
    manifest_path: Path,
    *,
    expected_version: str,
    expected_manifest_sha256: str,
    max_paths: int = DEFAULT_MAX_BASELINE_PATHS,
    max_file_bytes: int = DEFAULT_MAX_BASELINE_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_BASELINE_TOTAL_BYTES,
) -> BaselineManifest:
    """Validate an exact, bounded baseline inventory and all source bytes."""
    if not (1 <= max_paths <= DEFAULT_MAX_BASELINE_PATHS):
        raise WorkspaceLifecycleError(
            f"max_paths must be between 1 and {DEFAULT_MAX_BASELINE_PATHS:,}"
        )
    if (
        max_file_bytes < 1
        or max_file_bytes > DEFAULT_MAX_BASELINE_FILE_BYTES
        or max_total_bytes < 1
        or max_total_bytes > DEFAULT_MAX_BASELINE_TOTAL_BYTES
        or max_file_bytes > max_total_bytes
    ):
        raise WorkspaceLifecycleError(
            "baseline byte limits must be positive and internally consistent"
        )
    if not source_root.is_absolute() or not manifest_path.is_absolute():
        raise WorkspaceLifecycleError("source root and baseline manifest must be absolute paths")
    if not source_root.is_dir() or _is_link_or_reparse(source_root):
        raise WorkspaceLifecycleError(
            "source root must be a regular directory, not a link or reparse point"
        )
    source_root = source_root.resolve()
    manifest_path = manifest_path.resolve()
    try:
        manifest_path.relative_to(source_root)
    except ValueError as error:
        raise WorkspaceLifecycleError("baseline manifest must be inside the source root") from error

    document, raw_manifest = _read_json(manifest_path, MAX_CONTROL_FILE_BYTES, "baseline manifest")
    observed_manifest_sha256 = sha256(raw_manifest).hexdigest()
    if (
        _SHA256_RE.fullmatch(expected_manifest_sha256) is None
        or observed_manifest_sha256 != expected_manifest_sha256
    ):
        raise WorkspaceLifecycleError("baseline manifest does not match the expected SHA-256")
    if document.get("schema_version") != BASELINE_SCHEMA_VERSION:
        raise WorkspaceLifecycleError("unsupported baseline manifest schema_version")
    baseline_version = _validate_text(document.get("baseline_version"), "baseline_version")
    if baseline_version != expected_version:
        raise WorkspaceLifecycleError(
            f"expected baseline version {expected_version!r}, found {baseline_version!r}"
        )
    source_revision = _validate_text(document.get("source_revision"), "source_revision")
    records = document.get("files")
    if not isinstance(records, list) or not records:
        raise WorkspaceLifecycleError("baseline manifest files must be a nonempty list")
    if len(records) > max_paths:
        raise WorkspaceLifecycleError(f"baseline manifest exceeds the {max_paths:,}-path limit")

    files: list[BaselineFile] = []
    seen_casefold: set[str] = set()
    total_bytes = 0
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256", "mode"}:
            raise WorkspaceLifecycleError(
                "each baseline file requires only path, bytes, sha256, and mode"
            )
        relative = _validate_relative_path(record.get("path"))
        casefolded = relative.as_posix().casefold()
        if casefolded in seen_casefold:
            raise WorkspaceLifecycleError(
                f"baseline paths collide on a case-insensitive filesystem: {relative}"
            )
        seen_casefold.add(casefolded)
        byte_count = record.get("bytes")
        digest = record.get("sha256")
        mode = record.get("mode")
        if not isinstance(byte_count, int) or isinstance(byte_count, bool) or byte_count < 0:
            raise WorkspaceLifecycleError(f"invalid byte count for baseline path: {relative}")
        if byte_count > max_file_bytes:
            raise WorkspaceLifecycleError(
                f"baseline file exceeds the {max_file_bytes:,}-byte limit: {relative}"
            )
        total_bytes += byte_count
        if total_bytes > max_total_bytes:
            raise WorkspaceLifecycleError(
                f"baseline files exceed the {max_total_bytes:,}-byte aggregate limit"
            )
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise WorkspaceLifecycleError(f"invalid SHA-256 for baseline path: {relative}")
        if mode not in {"100644", "100755"}:
            raise WorkspaceLifecycleError(f"invalid Git mode for baseline path: {relative}")
        source_path = source_root / Path(*relative.parts)
        resolved_source = source_path.resolve()
        try:
            resolved_source.relative_to(source_root)
        except ValueError as error:
            raise WorkspaceLifecycleError(
                f"baseline path escapes the source root: {relative}"
            ) from error
        content = _read_bounded(source_path, max_file_bytes, f"baseline source {relative}")
        if len(content) != byte_count or sha256(content).hexdigest() != digest:
            raise WorkspaceLifecycleError(
                f"baseline source does not match its manifest: {relative}"
            )
        files.append(BaselineFile(relative.as_posix(), byte_count, digest, mode))

    file_paths = {PurePosixPath(file.path) for file in files}
    for path in file_paths:
        if any(parent in file_paths for parent in path.parents if parent != PurePosixPath(".")):
            raise WorkspaceLifecycleError(f"baseline file path is also used as a directory: {path}")
    files.sort(key=lambda file: file.path.casefold())
    return BaselineManifest(
        baseline_version=baseline_version,
        source_revision=source_revision,
        files=tuple(files),
        source_manifest_sha256=observed_manifest_sha256,
    )


def _load_installed_baseline(root: Path) -> tuple[BaselineManifest, str]:
    _validate_control_directory(root)
    document, raw = _read_json(
        root / INSTALLED_BASELINE, MAX_CONTROL_FILE_BYTES, "installed baseline"
    )
    if document.get("schema_version") != BASELINE_SCHEMA_VERSION:
        raise WorkspaceLifecycleError("unsupported installed baseline schema_version")
    baseline_version = _validate_text(document.get("baseline_version"), "baseline_version")
    source_revision = _validate_text(document.get("source_revision"), "source_revision")
    manifest_sha = document.get("source_manifest_sha256")
    if not isinstance(manifest_sha, str) or _SHA256_RE.fullmatch(manifest_sha) is None:
        raise WorkspaceLifecycleError("installed baseline has an invalid source manifest SHA-256")
    records = document.get("files")
    if not isinstance(records, list) or len(records) > DEFAULT_MAX_BASELINE_PATHS:
        raise WorkspaceLifecycleError("installed baseline has an invalid or over-limit files list")
    files: list[BaselineFile] = []
    seen: set[str] = set()
    total_bytes = 0
    for record in records:
        if not isinstance(record, dict):
            raise WorkspaceLifecycleError("installed baseline file entry must be an object")
        relative = _validate_relative_path(record.get("path"))
        key = relative.as_posix().casefold()
        if key in seen:
            raise WorkspaceLifecycleError("installed baseline contains colliding paths")
        seen.add(key)
        byte_count = record.get("bytes")
        digest = record.get("sha256")
        mode = record.get("mode")
        if not isinstance(byte_count, int) or isinstance(byte_count, bool) or byte_count < 0:
            raise WorkspaceLifecycleError("installed baseline contains an invalid byte count")
        if byte_count > DEFAULT_MAX_BASELINE_FILE_BYTES:
            raise WorkspaceLifecycleError("installed baseline file exceeds the hard byte limit")
        total_bytes += byte_count
        if total_bytes > DEFAULT_MAX_BASELINE_TOTAL_BYTES:
            raise WorkspaceLifecycleError(
                "installed baseline exceeds the hard aggregate byte limit"
            )
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise WorkspaceLifecycleError("installed baseline contains an invalid SHA-256")
        if mode not in {"100644", "100755"}:
            raise WorkspaceLifecycleError("installed baseline contains an invalid mode")
        files.append(BaselineFile(relative.as_posix(), byte_count, digest, mode))
    files.sort(key=lambda file: file.path.casefold())
    return BaselineManifest(baseline_version, source_revision, tuple(files), manifest_sha), sha256(
        raw
    ).hexdigest()


def _load_workspace_marker(root: Path) -> dict[str, Any]:
    _validate_control_directory(root)
    marker, _raw = _read_json(root / WORKSPACE_MARKER, MAX_CONTROL_FILE_BYTES, "workspace marker")
    if marker.get("schema_version") != WORKSPACE_SCHEMA_VERSION:
        raise WorkspaceLifecycleError("unsupported workspace marker schema_version")
    if marker.get("workspace_kind") != PRIVATE_WORKSPACE_KIND:
        raise WorkspaceLifecycleError("workspace marker is not for a private research workspace")
    _validate_text(marker.get("baseline_version"), "workspace baseline_version")
    digest = marker.get("baseline_manifest_sha256")
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise WorkspaceLifecycleError("workspace marker has an invalid baseline manifest SHA-256")
    installed_digest = marker.get("installed_baseline_sha256")
    if not isinstance(installed_digest, str) or _SHA256_RE.fullmatch(installed_digest) is None:
        raise WorkspaceLifecycleError("workspace marker has an invalid installed-baseline SHA-256")
    runtime = marker.get("local_runtime")
    if not isinstance(runtime, dict):
        raise WorkspaceLifecycleError("workspace marker has no local runtime identity")
    compose_name = runtime.get("compose_project_name")
    postgres_port = runtime.get("postgres_port")
    if not isinstance(compose_name, str) or not isinstance(postgres_port, int):
        raise WorkspaceLifecycleError("workspace marker local runtime identity is invalid")
    _validate_runtime_identity(compose_name, postgres_port)
    if marker.get("publication_authorized") is not False:
        raise WorkspaceLifecycleError("workspace marker must not authorize publication")
    if not isinstance(marker.get("research_authorized"), bool):
        raise WorkspaceLifecycleError("workspace marker research authorization is invalid")
    boundary_reviewed_at = marker.get("boundary_reviewed_at")
    if boundary_reviewed_at is not None and not isinstance(boundary_reviewed_at, str):
        raise WorkspaceLifecycleError("workspace marker boundary review timestamp is invalid")
    attestations = marker.get("boundary_attestations")
    required_attestations = {
        "private_filesystem",
        "private_synchronization",
        "private_backups",
        "no_public_remote",
    }
    if not isinstance(attestations, dict) or set(attestations) != required_attestations:
        raise WorkspaceLifecycleError("workspace marker boundary attestations are invalid")
    if any(not isinstance(value, bool) for value in attestations.values()):
        raise WorkspaceLifecycleError("workspace marker boundary attestations must be boolean")
    if marker["research_authorized"] != all(attestations.values()):
        raise WorkspaceLifecycleError("workspace marker research authorization is inconsistent")
    return marker


def _validate_control_directory(root: Path) -> Path:
    control = root / CONTROL_DIRECTORY
    if not control.is_dir() or _is_link_or_reparse(control):
        raise WorkspaceLifecycleError("workspace control directory is missing or unsafe")
    try:
        control.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise WorkspaceLifecycleError("workspace control directory escapes its root") from error
    return control


def _workspace_marker_document(
    manifest: BaselineManifest,
    *,
    created_at: str,
    installed_baseline_sha256: str,
    compose_project_name: str,
    postgres_port: int,
    research_authorized: bool = False,
    boundary_reviewed_at: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": WORKSPACE_SCHEMA_VERSION,
        "workspace_kind": PRIVATE_WORKSPACE_KIND,
        "created_at": created_at,
        "updated_at": _utc_now(),
        "baseline_version": manifest.baseline_version,
        "baseline_manifest_sha256": manifest.source_manifest_sha256,
        "installed_baseline_sha256": installed_baseline_sha256,
        "local_namespaces": {
            "cases": "research/cases",
            "learning": "research/knowledge/local",
            "skills": ".agents/skills-local",
            "source_data": "data",
            "generated_reports": "reports/generated",
        },
        "local_runtime": {
            "compose_project_name": compose_project_name,
            "postgres_port": postgres_port,
        },
        "publication_authorized": False,
        "research_authorized": research_authorized,
        "boundary_reviewed_at": boundary_reviewed_at,
        "boundary_attestations": {
            "private_filesystem": research_authorized,
            "private_synchronization": research_authorized,
            "private_backups": research_authorized,
            "no_public_remote": research_authorized,
        },
    }


def _write_new(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(content)


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        _write_new(temporary, content)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _apply_mode(path: Path, mode: str) -> None:
    path.chmod(0o755 if mode == "100755" else 0o644)


def _copy_manifest_files(source_root: Path, destination: Path, manifest: BaselineManifest) -> None:
    for file in manifest.files:
        relative = PurePosixPath(file.path)
        source = source_root / Path(*relative.parts)
        target = destination / Path(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        content = _read_bounded(
            source,
            DEFAULT_MAX_BASELINE_FILE_BYTES,
            f"baseline source {relative}",
        )
        if len(content) != file.bytes or sha256(content).hexdigest() != file.sha256:
            raise WorkspaceLifecycleError(f"baseline source changed during bootstrap: {relative}")
        _write_new(target, content)
        _apply_mode(target, file.mode)


def _workspace_target(root: Path, relative: PurePosixPath, *, create_parents: bool) -> Path:
    """Resolve a manifest target without traversing a link or reparse point."""
    target = root / Path(*relative.parts)
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.exists():
            if not current.is_dir() or _is_link_or_reparse(current):
                raise WorkspaceLifecycleError(f"workspace path has an unsafe parent: {relative}")
        elif create_parents:
            current.mkdir()
        else:
            break
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise WorkspaceLifecycleError(f"workspace path escapes its root: {relative}") from error
    return target


def _run_git(command: list[str], *, cwd: Path, input_text: str | None = None) -> str:
    git = shutil.which("git")
    if git is None:
        raise WorkspaceLifecycleError("Git executable was not found")
    try:
        environment = {
            key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
        }
        environment["GIT_CONFIG_NOSYSTEM"] = "1"
        environment["GIT_CONFIG_GLOBAL"] = os.devnull
        environment["GIT_LITERAL_PATHSPECS"] = "1"
        result = subprocess.run(
            [git, *command],
            cwd=cwd,
            capture_output=True,
            text=True,
            input=input_text,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorkspaceLifecycleError(
            f"Git command failed safely ({type(error).__name__})"
        ) from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        message = detail[0][:200] if detail else f"exit code {result.returncode}"
        raise WorkspaceLifecycleError(f"Git command failed: {message}")
    return result.stdout.strip()


def _initialize_private_git(root: Path, author_name: str, author_email: str) -> None:
    author_name = _validate_text(author_name, "Git author name")
    author_email = _validate_text(author_email, "Git author email")
    empty_template = root / CONTROL_DIRECTORY / "empty-git-template"
    empty_template.mkdir()
    try:
        _run_git(["init", "--initial-branch=main", f"--template={empty_template}"], cwd=root)
    finally:
        empty_template.rmdir()
    _run_git(["config", "--local", "user.name", author_name], cwd=root)
    _run_git(["config", "--local", "user.email", author_email], cwd=root)
    _run_git(["config", "--local", "core.autocrlf", "false"], cwd=root)
    _run_git(["config", "--local", "core.eol", "lf"], cwd=root)
    if _run_git(["remote"], cwd=root):
        raise WorkspaceLifecycleError("fresh private workspace unexpectedly has a Git remote")
    exclude = root / ".git/info/exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n# Local private-workspace runtime artifacts\n")
        handle.write(".env\ndata/\nreports/generated/\n.drlf/transactions/\n")


def _validate_runtime_identity(compose_project_name: str, postgres_port: int) -> str:
    compose_project_name = _validate_text(
        compose_project_name, "Compose project name", max_bytes=63
    )
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", compose_project_name) is None:
        raise WorkspaceLifecycleError(
            "Compose project name must use lowercase letters, digits, hyphens, or underscores"
        )
    if not 1 <= postgres_port <= 65_535:
        raise WorkspaceLifecycleError("PostgreSQL port must be between 1 and 65535")
    return compose_project_name


def _write_private_environment(root: Path, compose_project_name: str, postgres_port: int) -> None:
    password = secrets.token_urlsafe(32)
    encoded_password = quote(password, safe="")
    content = (
        f"COMPOSE_PROJECT_NAME={compose_project_name}\n"
        "POSTGRES_DB=drlf\n"
        "POSTGRES_USER=drlf\n"
        f"POSTGRES_PORT={postgres_port}\n"
        f"POSTGRES_PASSWORD={password}\n"
        "DATABASE_URL=postgresql://drlf:"
        f"{encoded_password}"
        f"@127.0.0.1:{postgres_port}/drlf\n"
    ).encode()
    _write_new(root / ".env", content)


def _safe_remove_staging(path: Path, parent: Path, prefix: str) -> None:
    if not path.exists() or not path.name.startswith(prefix) or _is_link_or_reparse(path):
        return
    resolved_parent = parent.resolve()
    resolved_path = path.resolve()
    if path.parent.resolve() != resolved_parent or resolved_path.parent != resolved_parent:
        return
    shutil.rmtree(resolved_path)


def bootstrap_private_workspace(
    source_root: Path,
    manifest_path: Path,
    destination: Path,
    *,
    expected_version: str,
    expected_manifest_sha256: str,
    git_author_name: str,
    git_author_email: str,
    compose_project_name: str,
    postgres_port: int,
    max_paths: int = DEFAULT_MAX_BASELINE_PATHS,
    max_file_bytes: int = DEFAULT_MAX_BASELINE_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_BASELINE_TOTAL_BYTES,
) -> WorkspaceBootstrapResult:
    """Create a separate private workspace with fresh Git history and exact baseline lineage."""
    if not destination.is_absolute():
        raise WorkspaceLifecycleError("destination must be an absolute path")
    source_root = source_root.resolve()
    destination = destination.resolve()
    if load_distribution_identity(source_root) != PUBLIC_BASELINE_DISTRIBUTION:
        raise WorkspaceLifecycleError("private workspaces require a DRLF public-baseline source")
    compose_project_name = _validate_runtime_identity(compose_project_name, postgres_port)
    if destination == source_root or source_root in destination.parents:
        raise WorkspaceLifecycleError("destination must be outside the public baseline source")
    if not destination.parent.is_dir() or _is_link_or_reparse(destination.parent):
        raise WorkspaceLifecycleError("destination parent must be an existing regular directory")

    manifest = load_source_baseline_manifest(
        source_root,
        manifest_path.resolve(),
        expected_version=expected_version,
        expected_manifest_sha256=expected_manifest_sha256,
        max_paths=max_paths,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
    )
    if destination.exists():
        if (destination / WORKSPACE_MARKER).is_file():
            installed, installed_hash = _load_installed_baseline(destination)
            marker = _load_workspace_marker(destination)
            if (
                installed.baseline_version == manifest.baseline_version
                and installed.source_manifest_sha256 == manifest.source_manifest_sha256
                and marker["baseline_version"] == manifest.baseline_version
                and marker["baseline_manifest_sha256"] == manifest.source_manifest_sha256
                and marker["installed_baseline_sha256"] == installed_hash
                and marker.get("local_runtime", {}).get("compose_project_name")
                == compose_project_name
                and marker.get("local_runtime", {}).get("postgres_port") == postgres_port
            ):
                report = inspect_private_workspace(destination)
                exact = all(
                    _hash_current_file(destination, file)[0] == "match" for file in installed.files
                )
                permitted_failures = (
                    set()
                    if marker["research_authorized"]
                    else {"workspace-boundary", "workspace-git-state"}
                )
                unexpected_failures = {
                    check.name
                    for check in report.checks
                    if check.status == "fail" and check.name not in permitted_failures
                }
                if not marker["research_authorized"]:
                    try:
                        _run_git(["rev-parse", "--verify", "HEAD"], cwd=destination)
                    except WorkspaceLifecycleError:
                        pass
                    else:
                        unexpected_failures.add("workspace-unreviewed-history")
                if exact and not unexpected_failures:
                    return WorkspaceBootstrapResult(
                        "already_current",
                        str(destination),
                        manifest.baseline_version,
                        manifest.source_manifest_sha256,
                        len(manifest.files),
                    )
            raise WorkspaceLifecycleError(
                "destination is an existing workspace with different or unhealthy baseline state"
            )
        if not destination.is_dir() or any(destination.iterdir()):
            raise WorkspaceLifecycleError(
                "destination must not exist or must be an empty directory"
            )

    prefix = f".{destination.name}.bootstrap-"
    staging = Path(tempfile.mkdtemp(prefix=prefix, dir=destination.parent))
    destination_was_empty = destination.is_dir()
    try:
        _copy_manifest_files(source_root, staging, manifest)
        for directory in LOCAL_DIRECTORIES:
            (staging / directory).mkdir(parents=True, exist_ok=True)
        _write_new(
            staging / LOCAL_INDEX,
            b"schema_version: 1\nnamespace: LOCAL-KNOWLEDGE\n"
            b"origin: private-workspace\nlearnings: []\n",
        )
        _write_private_environment(staging, compose_project_name, postgres_port)
        installed_at = _utc_now()
        installed_document = manifest.as_installed_dict(installed_at=installed_at)
        installed_bytes = _canonical_json_bytes(installed_document)
        marker_document = _workspace_marker_document(
            manifest,
            created_at=installed_at,
            installed_baseline_sha256=sha256(installed_bytes).hexdigest(),
            compose_project_name=compose_project_name,
            postgres_port=postgres_port,
        )
        _write_new(staging / INSTALLED_BASELINE, installed_bytes)
        _write_new(staging / WORKSPACE_MARKER, _canonical_json_bytes(marker_document))
        _initialize_private_git(staging, git_author_name, git_author_email)
        if destination_was_empty:
            destination.rmdir()
        try:
            os.replace(staging, destination)
        except OSError:
            if destination_was_empty and not destination.exists():
                destination.mkdir()
            raise
    except Exception:
        _safe_remove_staging(staging, destination.parent, prefix)
        raise

    return WorkspaceBootstrapResult(
        "created",
        str(destination),
        manifest.baseline_version,
        manifest.source_manifest_sha256,
        len(manifest.files),
    )


def _hash_current_file(root: Path, file: BaselineFile) -> tuple[str, str | None]:
    try:
        path = _workspace_target(root, PurePosixPath(file.path), create_parents=False)
    except WorkspaceLifecycleError:
        return "unsafe", None
    if not path.exists():
        return "missing", None
    if not path.is_file() or _is_link_or_reparse(path):
        return "unsafe", None
    content = _read_bounded(path, DEFAULT_MAX_BASELINE_FILE_BYTES, f"workspace file {file.path}")
    digest = sha256(content).hexdigest()
    if len(content) == file.bytes and digest == file.sha256:
        return "match", digest
    return "modified", digest


def _git_workspace_checks(root: Path) -> tuple[DoctorCheck, ...]:
    if not (root / ".git").is_dir() or _is_link_or_reparse(root / ".git"):
        return (DoctorCheck("workspace-git", "fail", "fresh local .git directory is missing"),)
    try:
        top = Path(_run_git(["rev-parse", "--show-toplevel"], cwd=root)).resolve()
        common_raw = _run_git(["rev-parse", "--git-common-dir"], cwd=root)
        common = Path(common_raw)
        if not common.is_absolute():
            common = (root / common).resolve()
        remotes = tuple(line for line in _run_git(["remote"], cwd=root).splitlines() if line)
    except WorkspaceLifecycleError as error:
        return (DoctorCheck("workspace-git", "fail", str(error)),)
    if top != root.resolve() or common != (root / ".git").resolve():
        return (
            DoctorCheck(
                "workspace-git",
                "fail",
                "workspace uses another worktree or shared Git object directory",
            ),
        )
    if (root / ".git/objects/info/alternates").exists():
        return (DoctorCheck("workspace-git", "fail", "Git object alternates are not permitted"),)
    if remotes:
        return (
            DoctorCheck(
                "workspace-git-remotes",
                "fail",
                "Git remote visibility cannot be verified locally; review before identifiable work",
            ),
        )
    try:
        commit = _run_git(["rev-parse", "--verify", "HEAD"], cwd=root)
        dirty = _run_git(["status", "--porcelain", "--untracked-files=all"], cwd=root)
    except WorkspaceLifecycleError:
        return (
            DoctorCheck("workspace-git", "pass", "fresh standalone Git metadata detected"),
            DoctorCheck("workspace-git-remotes", "pass", "no Git remotes configured"),
            DoctorCheck(
                "workspace-git-state",
                "fail",
                "initial private baseline has not been reviewed and committed",
            ),
        )
    return (
        DoctorCheck("workspace-git", "pass", "fresh standalone Git metadata detected"),
        DoctorCheck("workspace-git-remotes", "pass", "no Git remotes configured"),
        DoctorCheck(
            "workspace-git-state",
            "fail" if dirty else "pass",
            (
                "workspace has uncommitted changes; create a recovery commit before upgrading"
                if dirty
                else f"clean private history at {commit[:12]}"
            ),
        ),
    )


def inspect_private_workspace(root: Path) -> DoctorReport:
    """Inspect workspace lineage, namespaces, drift, recovery state, and Git isolation."""
    root = root.resolve()
    checks: list[DoctorCheck] = []
    try:
        marker = _load_workspace_marker(root)
        installed, installed_hash = _load_installed_baseline(root)
    except WorkspaceLifecycleError as error:
        return DoctorReport(False, (DoctorCheck("workspace-control", "fail", str(error)),))
    if (
        marker["baseline_version"] != installed.baseline_version
        or marker["baseline_manifest_sha256"] != installed.source_manifest_sha256
        or marker["installed_baseline_sha256"] != installed_hash
    ):
        checks.append(
            DoctorCheck(
                "workspace-control",
                "fail",
                "workspace marker and installed baseline manifest differ",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                "workspace-control",
                "pass",
                f"private workspace marker and baseline {installed.baseline_version} agree",
            )
        )

    if marker["research_authorized"] and marker.get("boundary_reviewed_at"):
        checks.append(
            DoctorCheck(
                "workspace-boundary",
                "pass",
                "private filesystem, synchronization, backup, and remote boundary was attested",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                "workspace-boundary",
                "fail",
                "private boundary review is required before identifiable research",
            )
        )

    if (root / INCOMPLETE_UPGRADE).exists():
        checks.append(
            DoctorCheck(
                "workspace-upgrade",
                "fail",
                "incomplete upgrade detected; run workspace-recover before continuing",
            )
        )
    else:
        checks.append(DoctorCheck("workspace-upgrade", "pass", "no incomplete upgrade"))

    missing = 0
    unsafe = 0
    modified = 0
    for file in installed.files:
        status, _digest = _hash_current_file(root, file)
        missing += status == "missing"
        unsafe += status == "unsafe"
        modified += status == "modified"
    if missing or unsafe:
        checks.append(
            DoctorCheck(
                "workspace-baseline",
                "fail",
                f"baseline files missing={missing}, unsafe={unsafe}, locally modified={modified}",
            )
        )
    elif modified:
        checks.append(
            DoctorCheck(
                "workspace-baseline",
                "fail",
                f"{modified} unrecorded baseline drift file(s); reconcile before research",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                "workspace-baseline",
                "pass",
                f"{len(installed.files)} managed baseline file(s) match",
            )
        )

    namespace_errors: list[str] = []
    for directory in LOCAL_DIRECTORIES:
        path = root / directory
        if not path.is_dir() or _is_link_or_reparse(path):
            namespace_errors.append(directory.as_posix())
    if not (root / LOCAL_INDEX).is_file() or _is_link_or_reparse(root / LOCAL_INDEX):
        namespace_errors.append(LOCAL_INDEX.as_posix())
    checks.append(
        DoctorCheck(
            "workspace-local-namespaces",
            "fail" if namespace_errors else "pass",
            (
                "missing or unsafe: " + ", ".join(namespace_errors)
                if namespace_errors
                else "case, learning, skill, data, and report namespaces are separate"
            ),
        )
    )
    checks.append(_inspect_local_knowledge_index(root))
    checks.extend(_git_workspace_checks(root))
    return DoctorReport(
        ready=all(check.status in {"pass", "warn"} for check in checks),
        checks=tuple(checks),
    )


def require_research_root(root: Path, *, operation: str = "research command") -> str:
    """Fail closed unless *root* is authorized for real-data research."""
    root = root.resolve()
    distribution_exists = (root / DISTRIBUTION_MARKER).exists()
    workspace_exists = (root / WORKSPACE_MARKER).exists()
    if distribution_exists and workspace_exists:
        raise WorkspaceLifecycleError(
            "ambiguous DRLF identity: distribution and workspace markers both exist"
        )
    if distribution_exists:
        distribution_kind = load_distribution_identity(root)
        if distribution_kind != PRIVATE_SYSTEM_DISTRIBUTION:
            raise WorkspaceLifecycleError(
                f"{operation} is disabled in a public DRLF baseline; bootstrap and finalize a "
                "separate private research workspace"
            )
        return distribution_kind
    if workspace_exists:
        marker = _load_workspace_marker(root)
        report = inspect_private_workspace(root)
        if not marker["research_authorized"] or not report.ready:
            raise WorkspaceLifecycleError(
                "private research workspace is not finalized and ready; run workspace-doctor"
            )
        return PRIVATE_WORKSPACE_KIND
    raise WorkspaceLifecycleError(
        f"unknown DRLF root identity; {operation} requires a private-system-of-record marker or "
        "a finalized private research workspace"
    )


def require_case_research_root(root: Path) -> str:
    """Fail closed unless *root* is authorized for identifiable case research."""
    return require_research_root(root, operation="case-init")


def inspect_research_root_identity(root: Path) -> DoctorCheck:
    """Describe the durable root identity without authorizing a public baseline for cases."""
    root = root.resolve()
    distribution_exists = (root / DISTRIBUTION_MARKER).exists()
    workspace_exists = (root / WORKSPACE_MARKER).exists()
    if distribution_exists and workspace_exists:
        return DoctorCheck(
            "research-root-identity",
            "fail",
            "ambiguous DRLF identity: distribution and workspace markers both exist",
        )
    if distribution_exists:
        try:
            identity = load_distribution_identity(root)
        except WorkspaceLifecycleError as error:
            return DoctorCheck("research-root-identity", "fail", str(error))
        if identity == PRIVATE_SYSTEM_DISTRIBUTION:
            return DoctorCheck(
                "research-root-identity",
                "pass",
                "private DRLF system of record; case-init is permitted",
            )
        return DoctorCheck(
            "research-root-identity",
            "warn",
            "public DRLF baseline; only synthetic work is permitted in this root",
        )
    if workspace_exists:
        try:
            marker = _load_workspace_marker(root)
        except WorkspaceLifecycleError as error:
            return DoctorCheck("research-root-identity", "fail", str(error))
        if marker["research_authorized"]:
            return DoctorCheck(
                "research-root-identity",
                "pass",
                "finalized private DRLF research workspace",
            )
        return DoctorCheck(
            "research-root-identity",
            "fail",
            "private DRLF workspace has not completed boundary finalization",
        )
    return DoctorCheck(
        "research-root-identity",
        "fail",
        "no durable DRLF distribution or private-workspace identity marker",
    )


def _inspect_local_knowledge_index(root: Path) -> DoctorCheck:
    """Check the bounded index envelope; the shipped validator checks cards and lineage."""
    try:
        raw = _read_bounded(root / LOCAL_INDEX, MAX_CONTROL_FILE_BYTES, "local knowledge index")
        document = yaml.safe_load(raw.decode("utf-8"))
        valid = (
            isinstance(document, dict)
            and document.get("schema_version") == 1
            and document.get("namespace") == "LOCAL-KNOWLEDGE"
            and document.get("origin") == "private-workspace"
            and isinstance(document.get("learnings"), list)
        )
    except (OSError, UnicodeError, yaml.YAMLError, WorkspaceLifecycleError):
        valid = False
    return DoctorCheck(
        "workspace-knowledge-index",
        "pass" if valid else "fail",
        (
            "local index envelope is valid; run the knowledge validator for cards and lineage"
            if valid
            else "local index requires schema_version 1, namespace LOCAL-KNOWLEDGE, "
            "origin private-workspace and a learnings list; existing content was not changed"
        ),
    )


def finalize_private_workspace(
    root: Path,
    *,
    confirm_private_filesystem: bool,
    confirm_private_sync: bool,
    confirm_private_backups: bool,
    confirm_no_public_remote: bool,
) -> WorkspaceFinalizeResult:
    """Attest the private boundary and create the reviewed fresh-history root commit."""
    confirmations = (
        confirm_private_filesystem,
        confirm_private_sync,
        confirm_private_backups,
        confirm_no_public_remote,
    )
    if not all(confirmations):
        raise WorkspaceLifecycleError(
            "all private filesystem, synchronization, backup, and remote confirmations are required"
        )
    root = root.resolve()
    marker, marker_raw = _read_json(
        root / WORKSPACE_MARKER, MAX_CONTROL_FILE_BYTES, "workspace marker"
    )
    marker = _load_workspace_marker(root)
    installed, installed_hash = _load_installed_baseline(root)
    if marker["installed_baseline_sha256"] != installed_hash:
        raise WorkspaceLifecycleError("workspace marker does not bind the installed baseline")
    if (root / INCOMPLETE_UPGRADE).exists():
        raise WorkspaceLifecycleError("recover the incomplete upgrade before finalizing")
    drift = [file.path for file in installed.files if _hash_current_file(root, file)[0] != "match"]
    if drift:
        raise WorkspaceLifecycleError("baseline changed before finalization: " + ", ".join(drift))
    knowledge_check = _inspect_local_knowledge_index(root)
    if knowledge_check.status != "pass":
        raise WorkspaceLifecycleError(knowledge_check.detail)
    git_checks = _git_workspace_checks(root)
    remote_failure = next(
        (
            check
            for check in git_checks
            if check.name in {"workspace-git", "workspace-git-remotes"} and check.status == "fail"
        ),
        None,
    )
    if remote_failure is not None:
        raise WorkspaceLifecycleError(remote_failure.detail)

    if marker["research_authorized"]:
        report = inspect_private_workspace(root)
        if not report.ready:
            raise WorkspaceLifecycleError("already-finalized workspace is not healthy")
        commit = _run_git(["rev-parse", "--verify", "HEAD"], cwd=root)
        return WorkspaceFinalizeResult("already_finalized", commit)

    try:
        _run_git(["rev-parse", "--verify", "HEAD"], cwd=root)
    except WorkspaceLifecycleError:
        pass
    else:
        raise WorkspaceLifecycleError(
            "unreviewed workspace unexpectedly has Git history; refusing to create a root commit"
        )

    finalized_marker = dict(marker)
    finalized_marker["research_authorized"] = True
    finalized_marker["boundary_reviewed_at"] = _utc_now()
    finalized_marker["boundary_attestations"] = {
        "private_filesystem": True,
        "private_synchronization": True,
        "private_backups": True,
        "no_public_remote": True,
    }
    finalized_marker["updated_at"] = _utc_now()
    commit_created = False
    try:
        _write_atomic(root / WORKSPACE_MARKER, _canonical_json_bytes(finalized_marker))
        required_tracked = {
            *(file.path for file in installed.files),
            WORKSPACE_MARKER.as_posix(),
            INSTALLED_BASELINE.as_posix(),
            LOCAL_INDEX.as_posix(),
        }
        visible_untracked = {
            path
            for path in _run_git(
                ["ls-files", "--others", "--exclude-standard", "-z"], cwd=root
            ).split("\0")
            if path
        }
        unexpected = sorted(visible_untracked - required_tracked)
        if unexpected:
            raise WorkspaceLifecycleError(
                "unexpected files appeared before private workspace finalization: "
                + ", ".join(unexpected[:20])
            )
        _run_git(
            ["add", "--force", "--pathspec-from-file=-", "--pathspec-file-nul"],
            cwd=root,
            input_text="\0".join(sorted(required_tracked)) + "\0",
        )
        tracked = set(_run_git(["ls-files"], cwd=root).splitlines())
        missing = sorted(required_tracked - tracked)
        if missing:
            raise WorkspaceLifecycleError(
                "baseline or control paths are ignored and cannot form the root commit: "
                + ", ".join(missing)
            )
        _run_git(
            [
                "commit",
                "--no-gpg-sign",
                "-m",
                f"Initialize private workspace from {installed.baseline_version}",
            ],
            cwd=root,
        )
        commit_created = True
        commit = _run_git(["rev-parse", "--verify", "HEAD"], cwd=root)
        root_record = _run_git(["rev-list", "--parents", "-n", "1", "HEAD"], cwd=root)
        if root_record.split() != [commit]:
            raise WorkspaceLifecycleError("initial private workspace commit is not a root commit")
        if _run_git(["status", "--porcelain", "--untracked-files=all"], cwd=root):
            raise WorkspaceLifecycleError(
                "private workspace root commit did not leave a clean tree"
            )
    except Exception:
        if not commit_created:
            _write_atomic(root / WORKSPACE_MARKER, marker_raw)
        raise
    return WorkspaceFinalizeResult("finalized", commit)


def plan_baseline_upgrade(
    root: Path,
    source_root: Path,
    manifest_path: Path,
    *,
    expected_version: str,
    expected_manifest_sha256: str,
    retain_removed: bool = False,
    max_paths: int = DEFAULT_MAX_BASELINE_PATHS,
    max_file_bytes: int = DEFAULT_MAX_BASELINE_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_BASELINE_TOTAL_BYTES,
) -> tuple[BaselineUpgradePlan, BaselineManifest]:
    """Build a bounded three-way plan without changing the private workspace."""
    root = root.resolve()
    source_root = source_root.resolve()
    if load_distribution_identity(source_root) != PUBLIC_BASELINE_DISTRIBUTION:
        raise WorkspaceLifecycleError("baseline upgrades require a DRLF public-baseline source")
    marker = _load_workspace_marker(root)
    old, installed_hash = _load_installed_baseline(root)
    if (
        marker["baseline_version"] != old.baseline_version
        or marker["baseline_manifest_sha256"] != old.source_manifest_sha256
        or marker["installed_baseline_sha256"] != installed_hash
    ):
        raise WorkspaceLifecycleError("workspace marker and installed baseline manifest differ")
    if (root / INCOMPLETE_UPGRADE).exists():
        raise WorkspaceLifecycleError("recover the incomplete upgrade before planning another")
    readiness = inspect_private_workspace(root)
    blocking_checks = [
        check
        for check in readiness.checks
        if check.status == "fail" and check.name != "workspace-baseline"
    ]
    if blocking_checks:
        failures = ", ".join(check.name for check in blocking_checks)
        raise WorkspaceLifecycleError(
            "workspace is not a clean, verified recovery point: " + failures
        )
    new = load_source_baseline_manifest(
        source_root.resolve(),
        manifest_path.resolve(),
        expected_version=expected_version,
        expected_manifest_sha256=expected_manifest_sha256,
        max_paths=max_paths,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
    )
    if (
        new.baseline_version == old.baseline_version
        and new.source_manifest_sha256 != old.source_manifest_sha256
    ):
        raise WorkspaceLifecycleError("same-version baseline mutation is not permitted")
    old_semver = _semantic_version(old.baseline_version)
    new_semver = _semantic_version(new.baseline_version)
    if old_semver is not None and new_semver is not None and new_semver < old_semver:
        raise WorkspaceLifecycleError("baseline downgrade is not permitted")
    old_by_path = {file.path: file for file in old.files}
    new_by_path = {file.path: file for file in new.files}
    actions: list[UpgradeAction] = []
    for path in sorted(set(old_by_path) | set(new_by_path), key=str.casefold):
        old_file = old_by_path.get(path)
        new_file = new_by_path.get(path)
        if old_file is not None:
            local_status, _digest = _hash_current_file(root, old_file)
        else:
            try:
                target = _workspace_target(root, PurePosixPath(path), create_parents=False)
            except WorkspaceLifecycleError:
                local_status = "unsafe"
            else:
                local_status = "absent" if not target.exists() else "present"

        if old_file is None and new_file is not None:
            if local_status == "absent":
                actions.append(UpgradeAction(path, "new-upstream", "add", "add baseline file"))
            else:
                actions.append(
                    UpgradeAction(
                        path,
                        "local-path-collision",
                        "block",
                        "new baseline path already exists locally",
                    )
                )
        elif old_file is not None and new_file is None:
            if local_status in {"missing", "unsafe"}:
                actions.append(
                    UpgradeAction(
                        path,
                        f"removed-upstream-local-{local_status}",
                        "block",
                        "removed baseline path is already missing or unsafe locally",
                    )
                )
            elif not retain_removed:
                actions.append(
                    UpgradeAction(
                        path,
                        "removed-upstream",
                        "block",
                        "choose explicit retention before upgrade",
                    )
                )
            else:
                actions.append(
                    UpgradeAction(
                        path, "removed-upstream", "preserve", "retain as local-only content"
                    )
                )
        elif old_file is not None and new_file is not None:
            upstream_changed = old_file != new_file
            if local_status in {"missing", "unsafe"}:
                actions.append(
                    UpgradeAction(
                        path, f"local-{local_status}", "block", "baseline path is missing or unsafe"
                    )
                )
            elif not upstream_changed and local_status == "match":
                actions.append(UpgradeAction(path, "unchanged", "none", "no change"))
            elif not upstream_changed:
                actions.append(
                    UpgradeAction(
                        path,
                        "local-divergence",
                        "block",
                        "baseline-managed path changed locally; move reusable work to a local "
                        "namespace or reconcile it explicitly",
                    )
                )
            elif local_status == "match":
                actions.append(
                    UpgradeAction(
                        path, "changed-upstream", "replace", "replace unchanged baseline bytes"
                    )
                )
            else:
                actions.append(
                    UpgradeAction(
                        path, "concurrent-change", "block", "local and upstream bytes both changed"
                    )
                )
    plan = BaselineUpgradePlan(
        from_version=old.baseline_version,
        to_version=new.baseline_version,
        installed_baseline_sha256=installed_hash,
        new_manifest_sha256=new.source_manifest_sha256,
        actions=tuple(actions),
        blocked=any(action.action == "block" for action in actions),
        requires_skill_approval=any(
            action.action != "none"
            and _is_relative_to(PurePosixPath(action.path), PurePosixPath(".agents/skills"))
            for action in actions
        ),
    )
    return plan, new


def _require_outside_roots(path: Path, roots: tuple[Path, ...], label: str) -> Path:
    resolved = path.resolve()
    for root in roots:
        resolved_root = root.resolve()
        if resolved == resolved_root or resolved_root in resolved.parents:
            raise WorkspaceLifecycleError(f"{label} must be outside {resolved_root}")
    return resolved


def write_upgrade_plan(
    plan: BaselineUpgradePlan,
    output_path: Path,
    *,
    forbidden_roots: tuple[Path, ...] = (),
) -> str:
    """Persist a non-overwriting, digestible upgrade plan for separate review."""
    output_path = _require_outside_roots(output_path, forbidden_roots, "upgrade plan")
    if not output_path.parent.is_dir() or _is_link_or_reparse(output_path.parent):
        raise WorkspaceLifecycleError("upgrade plan parent must be an existing regular directory")
    if output_path.exists():
        raise WorkspaceLifecycleError("upgrade plan output already exists")
    content = _canonical_json_bytes(plan.as_dict())
    _write_new(output_path, content)
    return sha256(content).hexdigest()


def _verify_approved_plan(
    plan: BaselineUpgradePlan,
    approved_plan_path: Path,
    approved_plan_sha256: str,
    *,
    forbidden_roots: tuple[Path, ...],
) -> None:
    if _SHA256_RE.fullmatch(approved_plan_sha256) is None:
        raise WorkspaceLifecycleError("approved plan SHA-256 is invalid")
    approved_plan_path = _require_outside_roots(
        approved_plan_path, forbidden_roots, "approved upgrade plan"
    )
    document, raw = _read_json(approved_plan_path, MAX_CONTROL_FILE_BYTES, "approved upgrade plan")
    if sha256(raw).hexdigest() != approved_plan_sha256:
        raise WorkspaceLifecycleError("approved upgrade plan does not match its expected SHA-256")
    if document != plan.as_dict():
        raise WorkspaceLifecycleError(
            "approved upgrade plan is stale or does not match this workspace"
        )


def _transaction_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"upgrade-{stamp}-{uuid4().hex[:12]}"


def _install_staged_file(staged: Path, target: Path, mode: str, *, root: Path) -> None:
    relative = PurePosixPath(target.relative_to(root).as_posix())
    target = _workspace_target(root, relative, create_parents=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.upgrade")
    try:
        shutil.copyfile(staged, temporary)
        _apply_mode(temporary, mode)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def apply_baseline_upgrade(
    root: Path,
    source_root: Path,
    manifest_path: Path,
    *,
    expected_version: str,
    expected_manifest_sha256: str,
    approved: bool,
    approved_plan_path: Path,
    approved_plan_sha256: str,
    skill_changes_approved: bool = False,
    retain_removed: bool = False,
    max_paths: int = DEFAULT_MAX_BASELINE_PATHS,
    max_file_bytes: int = DEFAULT_MAX_BASELINE_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_BASELINE_TOTAL_BYTES,
) -> WorkspaceUpgradeResult:
    """Apply an approved plan with a write-ahead recovery journal."""
    if not approved:
        raise WorkspaceLifecycleError("upgrade requires explicit plan approval")
    root = root.resolve()
    plan, new = plan_baseline_upgrade(
        root,
        source_root,
        manifest_path,
        expected_version=expected_version,
        expected_manifest_sha256=expected_manifest_sha256,
        retain_removed=retain_removed,
        max_paths=max_paths,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
    )
    if plan.blocked:
        blocked = ", ".join(action.path for action in plan.actions if action.action == "block")
        raise WorkspaceLifecycleError(f"upgrade plan is blocked: {blocked}")
    _verify_approved_plan(
        plan,
        approved_plan_path,
        approved_plan_sha256,
        forbidden_roots=(root, source_root.resolve()),
    )
    if plan.requires_skill_approval and not skill_changes_approved:
        raise WorkspaceLifecycleError(
            "upgrade changes baseline skills and requires separate explicit skill-change approval"
        )
    if plan.from_version == plan.to_version and all(
        action.action in {"none", "preserve"} for action in plan.actions
    ):
        return WorkspaceUpgradeResult(
            "already_current", plan.from_version, plan.to_version, 0, 0, 0
        )

    old, _installed_hash = _load_installed_baseline(root)
    old_marker, old_marker_raw = _read_json(
        root / WORKSPACE_MARKER, MAX_CONTROL_FILE_BYTES, "workspace marker"
    )
    _old_baseline_doc, old_baseline_raw = _read_json(
        root / INSTALLED_BASELINE, MAX_CONTROL_FILE_BYTES, "installed baseline"
    )
    transaction_id = _transaction_id()
    transactions_root = root / TRANSACTION_DIRECTORY
    if transactions_root.exists() and (
        not transactions_root.is_dir() or _is_link_or_reparse(transactions_root)
    ):
        raise WorkspaceLifecycleError("workspace transaction directory is unsafe")
    transactions_root.mkdir(exist_ok=True)
    transaction_root = transactions_root / transaction_id
    stage_root = transaction_root / "staged"
    backup_root = transaction_root / "backup"
    transaction_root.mkdir(parents=True)
    source_root = source_root.resolve()
    actionable = [action for action in plan.actions if action.action in {"add", "replace"}]
    new_by_path = {file.path: file for file in new.files}
    old_by_path = {file.path: file for file in old.files}
    operations: list[dict[str, Any]] = []
    try:
        for action in actionable:
            relative = PurePosixPath(action.path)
            new_file = new_by_path[action.path]
            staged = stage_root / Path(*relative.parts)
            staged.parent.mkdir(parents=True, exist_ok=True)
            content = _read_bounded(
                source_root / Path(*relative.parts),
                max_file_bytes,
                f"new baseline source {relative}",
            )
            if len(content) != new_file.bytes or sha256(content).hexdigest() != new_file.sha256:
                raise WorkspaceLifecycleError(
                    f"new baseline source changed after planning: {relative}"
                )
            _write_new(staged, content)
            _apply_mode(staged, new_file.mode)
            operation: dict[str, Any] = {
                "path": action.path,
                "action": action.action,
                "new_sha256": new_file.sha256,
                "new_mode": new_file.mode,
            }
            if action.action == "replace":
                old_file = old_by_path[action.path]
                backup = backup_root / Path(*relative.parts)
                backup.parent.mkdir(parents=True, exist_ok=True)
                old_content = _read_bounded(
                    root / Path(*relative.parts),
                    max_file_bytes,
                    f"current baseline {relative}",
                )
                if (
                    len(old_content) != old_file.bytes
                    or sha256(old_content).hexdigest() != old_file.sha256
                ):
                    raise WorkspaceLifecycleError(
                        f"current baseline changed after planning: {relative}"
                    )
                _write_new(backup, old_content)
                operation["old_sha256"] = old_file.sha256
                operation["old_mode"] = old_file.mode
            operations.append(operation)
        _write_new(backup_root / "workspace.json", old_marker_raw)
        _write_new(backup_root / "baseline.json", old_baseline_raw)
        journal = {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "state": "prepared",
            "from_version": plan.from_version,
            "to_version": plan.to_version,
            "old_workspace_sha256": sha256(old_marker_raw).hexdigest(),
            "old_baseline_sha256": sha256(old_baseline_raw).hexdigest(),
            "operations": operations,
        }
        _write_new(transaction_root / "journal.json", _canonical_json_bytes(journal))
        incomplete = {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "started_at": _utc_now(),
            "from_version": plan.from_version,
            "to_version": plan.to_version,
        }
        _write_new(root / INCOMPLETE_UPGRADE, _canonical_json_bytes(incomplete))
        for operation in operations:
            relative = PurePosixPath(operation["path"])
            _install_staged_file(
                stage_root / Path(*relative.parts),
                root / Path(*relative.parts),
                operation["new_mode"],
                root=root,
            )

        installed_at = _utc_now()
        installed_bytes = _canonical_json_bytes(new.as_installed_dict(installed_at=installed_at))
        _write_atomic(root / INSTALLED_BASELINE, installed_bytes)
        created_at = str(old_marker.get("created_at") or installed_at)
        runtime = old_marker["local_runtime"]
        _write_atomic(
            root / WORKSPACE_MARKER,
            _canonical_json_bytes(
                _workspace_marker_document(
                    new,
                    created_at=created_at,
                    installed_baseline_sha256=sha256(installed_bytes).hexdigest(),
                    compose_project_name=runtime["compose_project_name"],
                    postgres_port=runtime["postgres_port"],
                    research_authorized=old_marker["research_authorized"],
                    boundary_reviewed_at=old_marker.get("boundary_reviewed_at"),
                )
            ),
        )
        (root / INCOMPLETE_UPGRADE).unlink()
        try:
            journal["state"] = "completed"
            journal["completed_at"] = _utc_now()
            _write_atomic(transaction_root / "journal.json", _canonical_json_bytes(journal))
            _safe_remove_staging(transaction_root, transactions_root, "upgrade-")
        except OSError:
            # The authoritative marker and manifest are already committed. Stale transaction
            # scratch is ignored and can be removed later without changing workspace state.
            pass
    except Exception as error:
        if not (root / INCOMPLETE_UPGRADE).exists():
            _safe_remove_staging(transaction_root, root / TRANSACTION_DIRECTORY, "upgrade-")
        if isinstance(error, WorkspaceLifecycleError):
            raise
        raise WorkspaceLifecycleError(
            f"upgrade stopped safely ({type(error).__name__}); run workspace-recover"
        ) from error

    return WorkspaceUpgradeResult(
        "upgraded",
        plan.from_version,
        plan.to_version,
        sum(action.action == "add" for action in plan.actions),
        sum(action.action == "replace" for action in plan.actions),
        sum(action.action == "preserve" for action in plan.actions),
    )


def _current_digest(path: Path) -> str | None:
    if not path.exists():
        return None
    if not path.is_file() or _is_link_or_reparse(path):
        raise WorkspaceLifecycleError(f"recovery target is not a regular file: {path}")
    return sha256(
        _read_bounded(path, DEFAULT_MAX_BASELINE_FILE_BYTES, "recovery target")
    ).hexdigest()


def recover_baseline_upgrade(root: Path) -> WorkspaceRecoveryResult:
    """Restore the prior baseline after a partial upgrade without touching local paths."""
    root = root.resolve()
    incomplete_path = root / INCOMPLETE_UPGRADE
    if not incomplete_path.exists():
        return WorkspaceRecoveryResult("nothing_to_recover", None, 0, 0)
    incomplete, _raw = _read_json(incomplete_path, MAX_CONTROL_FILE_BYTES, "incomplete marker")
    transaction_id = incomplete.get("transaction_id")
    if not isinstance(transaction_id, str) or _TRANSACTION_ID_RE.fullmatch(transaction_id) is None:
        raise WorkspaceLifecycleError("incomplete marker has an invalid transaction ID")
    transaction_root = root / TRANSACTION_DIRECTORY / transaction_id
    transactions_root = root / TRANSACTION_DIRECTORY
    if not transactions_root.is_dir() or _is_link_or_reparse(transactions_root):
        raise WorkspaceLifecycleError("workspace transaction directory is unsafe")
    if not transaction_root.is_dir() or _is_link_or_reparse(transaction_root):
        raise WorkspaceLifecycleError("workspace recovery transaction is missing or unsafe")
    journal, _journal_raw = _read_json(
        transaction_root / "journal.json", MAX_CONTROL_FILE_BYTES, "upgrade journal"
    )
    if journal.get("transaction_id") != transaction_id or journal.get("state") != "prepared":
        raise WorkspaceLifecycleError("upgrade journal does not match the recoverable transaction")
    operations = journal.get("operations")
    if not isinstance(operations, list) or len(operations) > DEFAULT_MAX_BASELINE_PATHS:
        raise WorkspaceLifecycleError("upgrade journal has an invalid operation list")

    old_marker = _read_bounded(
        transaction_root / "backup/workspace.json",
        MAX_CONTROL_FILE_BYTES,
        "workspace backup",
    )
    old_baseline = _read_bounded(
        transaction_root / "backup/baseline.json",
        MAX_CONTROL_FILE_BYTES,
        "baseline backup",
    )
    try:
        old_marker_document = json.loads(old_marker)
        old_baseline_document = json.loads(old_baseline)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise WorkspaceLifecycleError("recovery control backup is invalid JSON") from error
    if not isinstance(old_marker_document, dict) or not isinstance(old_baseline_document, dict):
        raise WorkspaceLifecycleError("recovery control backup must contain JSON objects")
    if sha256(old_marker).hexdigest() != journal.get("old_workspace_sha256") or sha256(
        old_baseline
    ).hexdigest() != journal.get("old_baseline_sha256"):
        raise WorkspaceLifecycleError("recovery control backup hash mismatch")

    # Validate every target before making any recovery change.
    parsed: list[tuple[PurePosixPath, str, str, str | None, str | None]] = []
    for operation in operations:
        if not isinstance(operation, dict):
            raise WorkspaceLifecycleError("upgrade journal operation is invalid")
        relative = _validate_relative_path(operation.get("path"))
        action = operation.get("action")
        new_digest = operation.get("new_sha256")
        old_digest = operation.get("old_sha256")
        old_mode = operation.get("old_mode")
        if action not in {"add", "replace"}:
            raise WorkspaceLifecycleError("upgrade journal contains an unsupported action")
        if not isinstance(new_digest, str) or _SHA256_RE.fullmatch(new_digest) is None:
            raise WorkspaceLifecycleError("upgrade journal contains an invalid new hash")
        if action == "replace" and (
            not isinstance(old_digest, str) or _SHA256_RE.fullmatch(old_digest) is None
        ):
            raise WorkspaceLifecycleError("upgrade journal contains an invalid old hash")
        if action == "replace" and old_mode not in {"100644", "100755"}:
            raise WorkspaceLifecycleError("upgrade journal contains an invalid old mode")
        try:
            target = _workspace_target(root, relative, create_parents=False)
        except WorkspaceLifecycleError as error:
            raise WorkspaceLifecycleError(f"recovery target is unsafe: {relative}") from error
        current = _current_digest(target)
        allowed = {None, new_digest}
        if action == "replace":
            allowed.add(old_digest)
        if current not in allowed:
            raise WorkspaceLifecycleError(
                f"recovery target changed after the failed upgrade; refusing: {relative}"
            )
        if action == "replace":
            backup = transaction_root / "backup" / Path(*relative.parts)
            if _current_digest(backup) != old_digest:
                raise WorkspaceLifecycleError(f"recovery backup hash mismatch: {relative}")
        parsed.append(
            (
                relative,
                action,
                new_digest,
                old_digest if isinstance(old_digest, str) else None,
                old_mode if isinstance(old_mode, str) else None,
            )
        )

    restored = 0
    removed = 0
    for relative, action, _new_digest, _old_digest, old_mode in parsed:
        target = _workspace_target(root, relative, create_parents=False)
        if action == "add":
            if target.exists():
                target.unlink()
                removed += 1
            continue
        backup = transaction_root / "backup" / Path(*relative.parts)
        _install_staged_file(backup, target, old_mode or "100644", root=root)
        restored += 1
    _write_atomic(root / WORKSPACE_MARKER, old_marker)
    _write_atomic(root / INSTALLED_BASELINE, old_baseline)
    incomplete_path.unlink()
    _safe_remove_staging(transaction_root, transactions_root, "upgrade-")
    return WorkspaceRecoveryResult("recovered", transaction_id, restored, removed)
