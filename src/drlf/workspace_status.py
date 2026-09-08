from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass
from hashlib import sha256
from itertools import islice
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

import psycopg
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

from drlf.database import find_migration_history_gaps
from drlf.doctor_models import CheckStatus, DoctorCheck, DoctorReport

MAX_SOURCE_MANIFESTS = 10_000
MAX_SOURCE_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_SOURCE_MANIFEST_TOTAL_BYTES = 256 * 1024 * 1024
MAX_SOURCE_SCHEMA_BYTES = 2 * 1024 * 1024
MAX_LOCAL_MIGRATIONS = 1_000
MAX_MIGRATION_BYTES = 8 * 1024 * 1024
MAX_MIGRATION_TOTAL_BYTES = 64 * 1024 * 1024
DATABASE_RELEASE_ROW_CAP = 10_000
DATABASE_STATEMENT_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class DatabaseReadiness:
    """Read-only comparison of local migrations with a reachable database."""

    server_version: str
    pending: tuple[str, ...]
    modified: tuple[str, ...]
    database_only: tuple[str, ...]
    history_gaps: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceFileStatus:
    """Local availability and optional integrity result for one manifest file."""

    relative_path: str
    role: str | None
    expected_bytes: int | None
    actual_bytes: int | None
    size_status: str
    expected_sha256: str | None
    actual_sha256: str | None
    hash_status: str


@dataclass(frozen=True)
class ManifestSourceStatus:
    """Source-file status for one release manifest."""

    manifest_path: str
    manifest_status: str
    dataset_slug: str | None
    data_year: int | None
    restore_status: str
    files: tuple[SourceFileStatus, ...]
    error: str | None = None


@dataclass(frozen=True)
class SourceStatusReport:
    """Bounded local-restoration status across release manifests."""

    manifest_count: int
    complete_manifest_count: int
    invalid_manifest_count: int
    declared_file_count: int
    available_file_count: int
    missing_file_count: int
    size_mismatch_count: int
    hash_checked_count: int
    hash_mismatch_count: int
    hash_skipped_for_cap_count: int
    hash_byte_cap: int | None
    hash_bytes_read: int
    all_files_present: bool
    all_sizes_match: bool
    all_hashes_verified: bool
    manifest_source_set_verified: bool
    manifests: tuple[ManifestSourceStatus, ...]

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["manifests"] = [asdict(manifest) for manifest in self.manifests]
        return result


@dataclass(frozen=True)
class LocalRelease:
    """One local source-release manifest inventory row."""

    manifest_path: str
    manifest_sha256: str
    manifest_status: str
    source_name: str | None
    dataset_slug: str | None
    dataset_name: str | None
    data_year: int | None
    version_id: str | None
    observed_at: str | None
    file_count: int
    expected_bytes: int | None


@dataclass(frozen=True)
class DatabaseRelease:
    """One source release registered in the local PostgreSQL metadata schema."""

    source_release_id: int
    source_name: str
    dataset_slug: str
    dataset_name: str
    data_year: int | None
    version_id: str | None
    observed_at: str
    status: str
    manifest_path: str | None
    manifest_sha256: str | None


@dataclass(frozen=True)
class ReleaseListReport:
    """Local manifests plus an optional read-only database inventory."""

    local_releases: tuple[LocalRelease, ...]
    invalid_manifests: tuple[dict[str, str], ...]
    database_status: str
    database_releases: tuple[DatabaseRelease, ...]
    database_truncated: bool
    database_error_type: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "local_release_count": len(self.local_releases),
            "invalid_manifest_count": len(self.invalid_manifests),
            "database_status": self.database_status,
            "database_release_count": len(self.database_releases),
            "database_truncated": self.database_truncated,
            "database_error_type": self.database_error_type,
            "local_releases": [asdict(release) for release in self.local_releases],
            "invalid_manifests": list(self.invalid_manifests),
            "database_releases": [asdict(release) for release in self.database_releases],
        }


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _resolve_from_root(path: Path, root: Path) -> Path:
    return path if path.is_absolute() else root / path


def discover_manifest_paths(
    root: Path,
    manifest_dir: Path = Path("research/data-manifests"),
    manifests: list[Path] | None = None,
) -> tuple[Path, ...]:
    """Find source manifests while excluding the colocated JSON Schema."""
    root = root.resolve()
    if manifests:
        paths = [
            _resolve_from_root(path, root)
            for path in manifests
            if not path.name.endswith(".schema.json")
        ]
    else:
        directory = _resolve_from_root(manifest_dir, root)
        if not directory.is_dir():
            raise ValueError(f"Manifest directory does not exist: {_display_path(directory, root)}")
        manifest_paths = (
            path for path in directory.rglob("*.json") if not path.name.endswith(".schema.json")
        )
        paths = list(islice(manifest_paths, MAX_SOURCE_MANIFESTS + 1))
    resolved = tuple(sorted(path.resolve() for path in paths))
    if len(resolved) > MAX_SOURCE_MANIFESTS:
        raise ValueError(
            f"Manifest selection exceeds the {MAX_SOURCE_MANIFESTS:,}-file safety limit"
        )
    return resolved


def _check_file_size_envelope(
    paths: tuple[Path, ...] | list[Path],
    *,
    per_file_cap: int,
    total_cap: int,
    label: str,
) -> None:
    total_bytes = 0
    for path in paths:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > per_file_cap:
            raise ValueError(
                f"{label} exceeds the {per_file_cap:,}-byte per-file safety limit: {path.name}"
            )
        total_bytes += size
        if total_bytes > total_cap:
            raise ValueError(f"{label} files exceed the {total_cap:,}-byte aggregate safety limit")


def _read_bounded_bytes(path: Path, byte_cap: int, label: str) -> bytes:
    if not path.is_file():
        raise ValueError(f"{label} file does not exist")
    try:
        with path.open("rb") as handle:
            content = handle.read(byte_cap + 1)
    except OSError as error:
        raise ValueError(f"unreadable {label} ({type(error).__name__})") from error
    if len(content) > byte_cap:
        raise ValueError(f"{label} exceeds the {byte_cap:,}-byte safety limit")
    return content


def _source_manifest_validator(root: Path) -> Draft202012Validator:
    schema_path = root / "research/data-manifests/source-manifest.schema.json"
    schema_bytes = _read_bounded_bytes(schema_path, MAX_SOURCE_SCHEMA_BYTES, "manifest schema")
    try:
        schema = json.loads(schema_bytes)
        Draft202012Validator.check_schema(schema)
    except (UnicodeError, json.JSONDecodeError, SchemaError) as error:
        raise ValueError(f"invalid source-manifest schema ({type(error).__name__})") from error
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _schema_error_location(error: ValidationError) -> str:
    if not error.absolute_path:
        return "<root>"
    return ".".join(str(part) for part in error.absolute_path)


def _read_source_manifest(
    path: Path, validator: Draft202012Validator
) -> tuple[dict[str, Any], bytes]:
    manifest_bytes = _read_bounded_bytes(path, MAX_SOURCE_MANIFEST_BYTES, "manifest")
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"unreadable JSON ({type(error).__name__})") from error
    try:
        validator.validate(manifest)
    except ValidationError as error:
        location = _schema_error_location(error)
        message = error.message.replace("\n", " ")[:240]
        raise ValueError(f"schema validation failed at {location}: {message}") from error
    return manifest, manifest_bytes


def _safe_source_path(root: Path, relative_path: object) -> Path | None:
    if not isinstance(relative_path, str):
        return None
    posix_path = PurePosixPath(relative_path)
    if posix_path.is_absolute() or ".." in posix_path.parts:
        return None
    candidate = (root / Path(*posix_path.parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _hash_file_bounded(path: Path, byte_limit: int) -> tuple[str | None, int, str]:
    """Hash at most byte_limit bytes and reject a file that changes while read."""
    before = path.stat()
    if before.st_size > byte_limit:
        return None, 0, "skipped_cap"

    digest = sha256()
    consumed = 0
    with path.open("rb") as handle:
        while consumed < before.st_size:
            chunk = handle.read(min(1024 * 1024, before.st_size - consumed))
            if not chunk:
                break
            digest.update(chunk)
            consumed += len(chunk)

    after = path.stat()
    if (
        consumed != before.st_size
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
    ):
        return None, consumed, "changed_during_hash"
    return digest.hexdigest(), consumed, "checked"


def inspect_source_status(
    root: Path,
    *,
    manifest_dir: Path = Path("research/data-manifests"),
    manifests: list[Path] | None = None,
    hash_byte_cap: int | None = None,
) -> SourceStatusReport:
    """Inspect manifest-referenced files without hashing unless an explicit cap is supplied."""
    if hash_byte_cap is not None and hash_byte_cap < 1:
        raise ValueError("hash_byte_cap must be at least 1")

    root = root.resolve()
    paths = discover_manifest_paths(root, manifest_dir, manifests)
    _check_file_size_envelope(
        paths,
        per_file_cap=MAX_SOURCE_MANIFEST_BYTES,
        total_cap=MAX_SOURCE_MANIFEST_TOTAL_BYTES,
        label="source manifest",
    )
    validator = _source_manifest_validator(root)
    remaining_hash_bytes = hash_byte_cap
    hash_bytes_read = 0
    hash_cache: dict[Path, tuple[str | None, int, str]] = {}
    manifest_results: list[ManifestSourceStatus] = []

    for manifest_path in paths:
        display_manifest = _display_path(manifest_path, root)
        try:
            manifest, _manifest_bytes = _read_source_manifest(manifest_path, validator)
        except ValueError as error:
            manifest_results.append(
                ManifestSourceStatus(
                    manifest_path=display_manifest,
                    manifest_status="invalid",
                    dataset_slug=None,
                    data_year=None,
                    restore_status="invalid_manifest",
                    files=(),
                    error=str(error),
                )
            )
            continue

        dataset = manifest.get("dataset") if isinstance(manifest.get("dataset"), dict) else {}
        manifest_status = str(manifest.get("status", "unknown"))
        file_results: list[SourceFileStatus] = []
        for file_record in manifest["files"]:
            record = file_record if isinstance(file_record, dict) else {}
            relative_path = record.get("relative_path")
            relative_label = relative_path if isinstance(relative_path, str) else "<invalid>"
            expected_bytes = record.get("bytes") if isinstance(record.get("bytes"), int) else None
            expected_hash = record.get("sha256") if isinstance(record.get("sha256"), str) else None
            source_path = _safe_source_path(root, relative_path)
            actual_bytes: int | None = None
            actual_hash: str | None = None

            if source_path is None:
                size_status = "unsafe_path"
                hash_status = "unsafe_path"
            elif not source_path.is_file():
                size_status = "missing"
                hash_status = "unavailable"
            else:
                actual_bytes = source_path.stat().st_size
                if expected_bytes is None:
                    size_status = "not_available"
                elif actual_bytes == expected_bytes:
                    size_status = "match"
                else:
                    size_status = "mismatch"

                if expected_hash is None:
                    hash_status = "not_available"
                elif manifest_status != "complete":
                    hash_status = "not_checked_noncomplete"
                elif remaining_hash_bytes is None:
                    hash_status = "not_checked"
                else:
                    cached = hash_cache.get(source_path)
                    if cached is None:
                        cached = _hash_file_bounded(source_path, remaining_hash_bytes)
                        hash_cache[source_path] = cached
                        hash_bytes_read += cached[1]
                        remaining_hash_bytes -= cached[1]
                    actual_hash, _consumed, check_status = cached
                    if check_status != "checked":
                        hash_status = check_status
                    elif actual_hash == expected_hash:
                        hash_status = "match"
                    else:
                        hash_status = "mismatch"

            file_results.append(
                SourceFileStatus(
                    relative_path=relative_label,
                    role=record.get("role") if isinstance(record.get("role"), str) else None,
                    expected_bytes=expected_bytes,
                    actual_bytes=actual_bytes,
                    size_status=size_status,
                    expected_sha256=expected_hash,
                    actual_sha256=actual_hash,
                    hash_status=hash_status,
                )
            )

        restore_status = _manifest_restore_status(manifest_status, file_results, hash_byte_cap)
        manifest_results.append(
            ManifestSourceStatus(
                manifest_path=display_manifest,
                manifest_status=manifest_status,
                dataset_slug=(
                    dataset.get("slug") if isinstance(dataset.get("slug"), str) else None
                ),
                data_year=(
                    dataset.get("data_year") if isinstance(dataset.get("data_year"), int) else None
                ),
                restore_status=restore_status,
                files=tuple(file_results),
            )
        )

    complete = [result for result in manifest_results if result.manifest_status == "complete"]
    files = [file for result in complete for file in result.files]
    invalid_count = sum(result.manifest_status == "invalid" for result in manifest_results)
    available_count = sum(file.actual_bytes is not None for file in files)
    missing_count = sum(file.size_status == "missing" for file in files)
    size_mismatch_count = sum(file.size_status == "mismatch" for file in files)
    hash_checked_count = sum(file.hash_status in {"match", "mismatch"} for file in files)
    hash_mismatch_count = sum(file.hash_status == "mismatch" for file in files)
    hash_skipped_count = sum(file.hash_status == "skipped_cap" for file in files)
    all_present = bool(files) and all(file.actual_bytes is not None for file in files)
    all_sizes = all_present and all(file.size_status == "match" for file in files)
    all_hashes = all_present and all(file.hash_status == "match" for file in files)

    return SourceStatusReport(
        manifest_count=len(manifest_results),
        complete_manifest_count=len(complete),
        invalid_manifest_count=invalid_count,
        declared_file_count=len(files),
        available_file_count=available_count,
        missing_file_count=missing_count,
        size_mismatch_count=size_mismatch_count,
        hash_checked_count=hash_checked_count,
        hash_mismatch_count=hash_mismatch_count,
        hash_skipped_for_cap_count=hash_skipped_count,
        hash_byte_cap=hash_byte_cap,
        hash_bytes_read=hash_bytes_read,
        all_files_present=all_present,
        all_sizes_match=all_sizes,
        all_hashes_verified=all_hashes,
        manifest_source_set_verified=(
            invalid_count == 0 and bool(complete) and all_sizes and all_hashes
        ),
        manifests=tuple(manifest_results),
    )


def _manifest_restore_status(
    manifest_status: str,
    files: list[SourceFileStatus],
    hash_byte_cap: int | None,
) -> str:
    if manifest_status != "complete":
        return "draft_manifest"
    if any(file.size_status == "unsafe_path" for file in files):
        return "unsafe_path"
    if any(file.size_status == "missing" for file in files):
        return "missing_files"
    if any(file.size_status == "mismatch" for file in files):
        return "size_mismatch"
    if any(file.hash_status == "mismatch" for file in files):
        return "hash_mismatch"
    if hash_byte_cap is None:
        return "available_size_checked"
    if files and all(file.hash_status == "match" for file in files):
        return "verified"
    return "available_partially_hashed"


def inventory_local_releases(
    root: Path,
    *,
    manifest_dir: Path = Path("research/data-manifests"),
    dataset_slug: str | None = None,
    data_year: int | None = None,
) -> tuple[tuple[LocalRelease, ...], tuple[dict[str, str], ...]]:
    """Inventory local manifest metadata without requiring raw files or PostgreSQL."""
    root = root.resolve()
    validator = _source_manifest_validator(root)
    releases: list[LocalRelease] = []
    invalid: list[dict[str, str]] = []
    paths = discover_manifest_paths(root, manifest_dir)
    _check_file_size_envelope(
        paths,
        per_file_cap=MAX_SOURCE_MANIFEST_BYTES,
        total_cap=MAX_SOURCE_MANIFEST_TOTAL_BYTES,
        label="source manifest",
    )
    for path in paths:
        display_path = _display_path(path, root)
        try:
            manifest, manifest_bytes = _read_source_manifest(path, validator)
        except ValueError as error:
            invalid.append({"manifest_path": display_path, "error": str(error)})
            continue

        dataset = manifest.get("dataset") if isinstance(manifest.get("dataset"), dict) else {}
        source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
        observation = (
            manifest.get("observation") if isinstance(manifest.get("observation"), dict) else {}
        )
        slug = dataset.get("slug") if isinstance(dataset.get("slug"), str) else None
        year = dataset.get("data_year") if isinstance(dataset.get("data_year"), int) else None
        if dataset_slug is not None and slug != dataset_slug:
            continue
        if data_year is not None and year != data_year:
            continue

        expected_sizes = [
            record.get("bytes")
            for record in manifest["files"]
            if isinstance(record, dict) and isinstance(record.get("bytes"), int)
        ]
        files_complete = len(expected_sizes) == len(manifest["files"])
        releases.append(
            LocalRelease(
                manifest_path=display_path,
                manifest_sha256=sha256(manifest_bytes).hexdigest(),
                manifest_status=str(manifest.get("status", "unknown")),
                source_name=(source.get("name") if isinstance(source.get("name"), str) else None),
                dataset_slug=slug,
                dataset_name=(
                    dataset.get("name") if isinstance(dataset.get("name"), str) else None
                ),
                data_year=year,
                version_id=(
                    dataset.get("version_id")
                    if isinstance(dataset.get("version_id"), str)
                    else None
                ),
                observed_at=(
                    observation.get("observed_at")
                    if isinstance(observation.get("observed_at"), str)
                    else None
                ),
                file_count=len(manifest["files"]),
                expected_bytes=sum(expected_sizes) if files_complete else None,
            )
        )
    releases.sort(
        key=lambda release: (
            release.dataset_slug or "",
            release.data_year if release.data_year is not None else -1,
            release.observed_at or "",
            release.manifest_path,
        )
    )
    return tuple(releases), tuple(invalid)


def list_database_releases(
    database_url: str,
    *,
    dataset_slug: str | None = None,
    data_year: int | None = None,
    row_cap: int = DATABASE_RELEASE_ROW_CAP,
    statement_timeout_seconds: int = DATABASE_STATEMENT_TIMEOUT_SECONDS,
) -> tuple[tuple[DatabaseRelease, ...], bool]:
    """Read a bounded source-release register without modifying the database."""
    if row_cap < 1 or row_cap > 100_000:
        raise ValueError("database release row_cap must be between 1 and 100,000")
    if statement_timeout_seconds < 1 or statement_timeout_seconds > 30:
        raise ValueError("database statement timeout must be between 1 and 30 seconds")

    conditions: list[str] = []
    parameters: list[object] = []
    if dataset_slug is not None:
        conditions.append("dataset.slug = %s")
        parameters.append(dataset_slug)
    if data_year is not None:
        conditions.append("release.data_year = %s")
        parameters.append(data_year)
    where_clause = " where " + " and ".join(conditions) if conditions else ""
    query = (
        """
        select
            release.source_release_id,
            dataset.source,
            dataset.slug,
            dataset.name,
            release.data_year,
            release.version_id,
            release.observed_at,
            release.status,
            release.manifest_path,
            release.manifest_sha256
        from metadata.source_release as release
        join metadata.source_dataset as dataset using (dataset_id)
        """
        + where_clause
        + """
        order by dataset.slug, release.data_year nulls first, release.observed_at,
                 release.source_release_id
        limit %s
        """
    )
    parameters.append(row_cap + 1)
    timeout_ms = statement_timeout_seconds * 1_000
    with psycopg.connect(
        database_url,
        connect_timeout=statement_timeout_seconds,
        options=(f"-c statement_timeout={timeout_ms} -c default_transaction_read_only=on"),
    ) as connection:
        rows = connection.execute(query, tuple(parameters)).fetchmany(row_cap + 1)
    truncated = len(rows) > row_cap
    rows = rows[:row_cap]
    releases = tuple(
        DatabaseRelease(
            source_release_id=int(row[0]),
            source_name=str(row[1]),
            dataset_slug=str(row[2]),
            dataset_name=str(row[3]),
            data_year=int(row[4]) if row[4] is not None else None,
            version_id=str(row[5]) if row[5] is not None else None,
            observed_at=row[6].isoformat(),
            status=str(row[7]),
            manifest_path=str(row[8]) if row[8] is not None else None,
            manifest_sha256=str(row[9]).strip() if row[9] is not None else None,
        )
        for row in rows
    )
    return releases, truncated


def build_release_list(
    root: Path,
    *,
    manifest_dir: Path = Path("research/data-manifests"),
    dataset_slug: str | None = None,
    data_year: int | None = None,
    include_database: bool = False,
    database_row_cap: int = DATABASE_RELEASE_ROW_CAP,
    database_statement_timeout_seconds: int = DATABASE_STATEMENT_TIMEOUT_SECONDS,
) -> ReleaseListReport:
    """Build a local inventory and optionally add available database metadata."""
    local, invalid = inventory_local_releases(
        root,
        manifest_dir=manifest_dir,
        dataset_slug=dataset_slug,
        data_year=data_year,
    )
    if not include_database:
        return ReleaseListReport(local, invalid, "not_checked", (), False, None)

    try:
        database_url = effective_database_url(root)
        database_releases, database_truncated = list_database_releases(
            database_url,
            dataset_slug=dataset_slug,
            data_year=data_year,
            row_cap=database_row_cap,
            statement_timeout_seconds=database_statement_timeout_seconds,
        )
    except Exception as error:
        return ReleaseListReport(local, invalid, "unavailable", (), False, type(error).__name__)

    status = "truncated" if database_truncated else "available"
    return ReleaseListReport(
        local,
        invalid,
        status,
        database_releases,
        database_truncated,
        None,
    )


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _effective_environment(root: Path) -> tuple[dict[str, str], bool]:
    dotenv_path = root.resolve() / ".env"
    values = _read_dotenv(dotenv_path)
    for key in (
        "COMPOSE_PROJECT_NAME",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_PORT",
        "DATABASE_URL",
    ):
        if key in os.environ:
            values[key] = os.environ[key]
    return values, dotenv_path.is_file()


def effective_database_url(root: Path) -> str:
    """Return the effective database URL without ever rendering it in status output."""
    values, _has_dotenv = _effective_environment(root)
    database_url = values.get("DATABASE_URL", "").strip()
    if not database_url:
        raise ValueError("DATABASE_URL is not configured")
    return database_url


def _environment_check(root: Path) -> tuple[DoctorCheck, str | None]:
    values, has_dotenv = _effective_environment(root)
    compose_project_name = values.get("COMPOSE_PROJECT_NAME", "")
    password = values.get("POSTGRES_PASSWORD", "")
    database_url = values.get("DATABASE_URL", "")
    if not compose_project_name or not password or not database_url:
        return (
            DoctorCheck(
                "environment",
                "fail",
                "COMPOSE_PROJECT_NAME, POSTGRES_PASSWORD, and DATABASE_URL must be configured",
            ),
            None,
        )

    placeholder_markers = ("replace-with", "change-me", "changeme", "<password>")
    if any(
        marker in compose_project_name.lower()
        or marker in password.lower()
        or marker in database_url.lower()
        for marker in placeholder_markers
    ):
        return DoctorCheck(
            "environment", "fail", "local environment placeholder is unchanged"
        ), None

    if (
        len(compose_project_name) > 63
        or re.fullmatch(r"[a-z0-9][a-z0-9_-]*", compose_project_name) is None
    ):
        return (
            DoctorCheck(
                "environment",
                "fail",
                "COMPOSE_PROJECT_NAME must be 1-63 lowercase letters, digits, "
                "hyphens, or underscores",
            ),
            None,
        )

    try:
        parsed = urlsplit(database_url)
        url_password = unquote(parsed.password or "")
        url_user = unquote(parsed.username or "")
        url_database = unquote(parsed.path.lstrip("/"))
        url_port = parsed.port or 5432
        configured_port = int(values.get("POSTGRES_PORT", "") or "5432")
        valid_url = (
            parsed.scheme in {"postgres", "postgresql"}
            and parsed.hostname is not None
            and bool(url_user)
            and bool(url_database)
            and 1 <= configured_port <= 65_535
        )
    except (TypeError, ValueError):
        valid_url = False
        url_password = ""
        url_user = ""
        url_database = ""
        url_port = 0
        configured_port = 0
    if not valid_url:
        return DoctorCheck(
            "environment", "fail", "DATABASE_URL is not a valid PostgreSQL URL"
        ), None
    if url_password != password:
        return (
            DoctorCheck(
                "environment",
                "fail",
                "POSTGRES_PASSWORD and the encoded DATABASE_URL password do not match",
            ),
            None,
        )
    expected_user = values.get("POSTGRES_USER", "") or "drlf"
    expected_database = values.get("POSTGRES_DB", "") or "drlf"
    mismatches: list[str] = []
    if url_user != expected_user:
        mismatches.append("POSTGRES_USER")
    if url_database != expected_database:
        mismatches.append("POSTGRES_DB")
    if url_port != configured_port:
        mismatches.append("POSTGRES_PORT")
    if mismatches:
        return (
            DoctorCheck(
                "environment",
                "fail",
                "DATABASE_URL does not match " + ", ".join(mismatches),
            ),
            None,
        )
    detail = (
        "local environment file is configured"
        if has_dotenv
        else "process environment is configured"
    )
    status: CheckStatus = "pass" if has_dotenv else "warn"
    return DoctorCheck("environment", status, detail), database_url


def _local_migration_hashes(migrations_dir: Path) -> dict[str, str]:
    paths = sorted(migrations_dir.glob("*.sql"))
    if not paths:
        raise ValueError("no local SQL migrations found")
    if len(paths) > MAX_LOCAL_MIGRATIONS:
        raise ValueError(f"local migrations exceed the {MAX_LOCAL_MIGRATIONS:,}-file safety limit")
    _check_file_size_envelope(
        paths,
        per_file_cap=MAX_MIGRATION_BYTES,
        total_cap=MAX_MIGRATION_TOTAL_BYTES,
        label="migration",
    )
    # Match apply_migrations(): text mode normalizes checkout-specific line endings.
    result: dict[str, str] = {}
    for path in paths:
        content = _read_bounded_bytes(path, MAX_MIGRATION_BYTES, "migration")
        try:
            normalized = content.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeError as error:
            raise ValueError(f"migration is not valid UTF-8: {path.name}") from error
        result[path.name] = sha256(normalized.encode("utf-8")).hexdigest()
    return result


def inspect_database_readiness(database_url: str, migrations_dir: Path) -> DatabaseReadiness:
    """Check database reachability and migration hashes without making schema changes."""
    local = _local_migration_hashes(migrations_dir)
    timeout_ms = DATABASE_STATEMENT_TIMEOUT_SECONDS * 1_000
    with psycopg.connect(
        database_url,
        connect_timeout=DATABASE_STATEMENT_TIMEOUT_SECONDS,
        options=(f"-c statement_timeout={timeout_ms} -c default_transaction_read_only=on"),
    ) as connection:
        version = str(connection.execute("select current_setting('server_version')").fetchone()[0])
        table_name = connection.execute(
            "select to_regclass('metadata.schema_migration')"
        ).fetchone()[0]
        if table_name is None:
            applied: dict[str, str] = {}
        else:
            rows = connection.execute(
                "select version, sha256 from metadata.schema_migration order by version limit %s",
                (MAX_LOCAL_MIGRATIONS + 1,),
            ).fetchmany(MAX_LOCAL_MIGRATIONS + 1)
            if len(rows) > MAX_LOCAL_MIGRATIONS:
                raise ValueError(
                    "database migration register exceeds the "
                    f"{MAX_LOCAL_MIGRATIONS:,}-row safety limit"
                )
            applied = {str(row[0]): str(row[1]).strip() for row in rows}

    pending = tuple(name for name in local if name not in applied)
    modified = tuple(name for name in local if name in applied and local[name] != applied[name])
    database_only = tuple(sorted(name for name in applied if name not in local))
    history_gaps = find_migration_history_gaps(tuple(local), set(applied))
    return DatabaseReadiness(version, pending, modified, database_only, history_gaps)


def _probe_command(
    command: list[str],
    *,
    root: Path,
    timeout_seconds: int,
) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, type(error).__name__
    output = (result.stdout or result.stderr).strip().splitlines()
    detail = output[0][:160] if output else f"exit code {result.returncode}"
    return result.returncode == 0, detail


def build_doctor_report(
    root: Path,
    *,
    skip_database: bool = False,
    command_timeout_seconds: int = 5,
) -> DoctorReport:
    """Run bounded, non-mutating clean-clone prerequisite checks."""
    if command_timeout_seconds < 1 or command_timeout_seconds > 30:
        raise ValueError("command_timeout_seconds must be between 1 and 30")
    root = root.resolve()
    checks: list[DoctorCheck] = []

    version = sys.version_info[:3]
    python_ok = (3, 13) <= version < (3, 14)
    checks.append(
        DoctorCheck(
            "python",
            "pass" if python_ok else "fail",
            f"Python {version[0]}.{version[1]}.{version[2]}; requires >=3.13,<3.14",
        )
    )

    required_paths = (
        Path("pyproject.toml"),
        Path("AGENTS.md"),
        Path("compose.yaml"),
        Path("sql/migrations"),
        Path("research/data-manifests/source-manifest.schema.json"),
    )
    missing = [path.as_posix() for path in required_paths if not (root / path).exists()]
    if missing:
        checks.append(DoctorCheck("project", "fail", "missing: " + ", ".join(missing)))
    else:
        try:
            project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
            project_name = project["project"]["name"]
        except (OSError, UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError):
            checks.append(DoctorCheck("project", "fail", "pyproject.toml is not readable"))
        else:
            checks.append(DoctorCheck("project", "pass", f"project files found ({project_name})"))

    git = shutil.which("git")
    if git is None:
        checks.append(DoctorCheck("git", "fail", "Git executable was not found"))
    else:
        version_ok, version_detail = _probe_command(
            [git, "--version"], root=root, timeout_seconds=command_timeout_seconds
        )
        repo_ok, _repo_detail = _probe_command(
            [git, "rev-parse", "--show-toplevel"],
            root=root,
            timeout_seconds=command_timeout_seconds,
        )
        if version_ok and repo_ok:
            checks.append(DoctorCheck("git", "pass", f"{version_detail}; repository detected"))
        else:
            checks.append(
                DoctorCheck("git", "fail", "Git is unavailable or root is not a worktree")
            )

    docker = shutil.which("docker")
    if docker is None:
        checks.extend(
            (
                DoctorCheck("docker", "fail", "Docker executable was not found"),
                DoctorCheck("docker-daemon", "fail", "Docker daemon could not be checked"),
                DoctorCheck("docker-compose", "fail", "Docker Compose could not be checked"),
                DoctorCheck(
                    "compose-project", "fail", "Docker Compose project could not be checked"
                ),
            )
        )
    else:
        docker_ok, docker_detail = _probe_command(
            [docker, "--version"], root=root, timeout_seconds=command_timeout_seconds
        )
        checks.append(
            DoctorCheck(
                "docker",
                "pass" if docker_ok else "fail",
                docker_detail if docker_ok else "Docker client check failed",
            )
        )
        daemon_ok, daemon_detail = _probe_command(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            root=root,
            timeout_seconds=command_timeout_seconds,
        )
        checks.append(
            DoctorCheck(
                "docker-daemon",
                "pass" if daemon_ok else "fail",
                (
                    f"Docker daemon reachable ({daemon_detail})"
                    if daemon_ok
                    else "Docker daemon check failed"
                ),
            )
        )
        compose_ok, compose_detail = _probe_command(
            [docker, "compose", "version"],
            root=root,
            timeout_seconds=command_timeout_seconds,
        )
        checks.append(
            DoctorCheck(
                "docker-compose",
                "pass" if compose_ok else "fail",
                compose_detail if compose_ok else "Docker Compose plugin check failed",
            )
        )
        project_ok, _project_detail = _probe_command(
            [docker, "compose", "config", "--quiet"],
            root=root,
            timeout_seconds=command_timeout_seconds,
        )
        checks.append(
            DoctorCheck(
                "compose-project",
                "pass" if project_ok else "fail",
                (
                    "Compose project configuration is valid"
                    if project_ok
                    else "Compose project configuration check failed"
                ),
            )
        )

    environment_check, database_url = _environment_check(root)
    checks.append(environment_check)

    if skip_database:
        checks.append(DoctorCheck("database", "skip", "database check was explicitly skipped"))
    elif database_url is None:
        checks.append(DoctorCheck("database", "skip", "database check requires valid environment"))
    else:
        try:
            readiness = inspect_database_readiness(database_url, root / "sql/migrations")
        except Exception as error:
            checks.append(
                DoctorCheck(
                    "database",
                    "fail",
                    f"database is unavailable ({type(error).__name__}); credentials were not shown",
                )
            )
        else:
            if readiness.modified:
                checks.append(
                    DoctorCheck(
                        "database",
                        "fail",
                        "applied migration hashes differ: " + ", ".join(readiness.modified),
                    )
                )
            elif readiness.database_only:
                checks.append(
                    DoctorCheck(
                        "database",
                        "fail",
                        "checkout is older than or incompatible with the database; "
                        "database contains migrations absent from this checkout: "
                        + ", ".join(readiness.database_only)
                        + (
                            f"; {len(readiness.pending)} local migration(s) are also pending"
                            if readiness.pending
                            else ""
                        ),
                    )
                )
            elif readiness.history_gaps:
                checks.append(
                    DoctorCheck(
                        "database",
                        "fail",
                        "applied migration history is not an exact prefix; earlier migration(s) "
                        "are missing: " + ", ".join(readiness.history_gaps),
                    )
                )
            elif readiness.pending:
                checks.append(
                    DoctorCheck(
                        "database",
                        "fail",
                        f"PostgreSQL {readiness.server_version} reachable; "
                        f"{len(readiness.pending)} migration(s) pending",
                    )
                )
            else:
                checks.append(
                    DoctorCheck(
                        "database",
                        "pass",
                        f"PostgreSQL {readiness.server_version} reachable; migrations current",
                    )
                )

    return DoctorReport(
        ready=all(check.status in {"pass", "warn"} for check in checks),
        checks=tuple(checks),
    )
