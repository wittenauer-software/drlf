from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from drlf.sources.manifest import load_and_validate_manifest

API_PAGE_ROLE = "api-page"
MAX_API_PAGE_BYTES = 64 * 1024 * 1024

RowMapper = Callable[[dict[str, Any], int, int], tuple[Any, ...]]


@dataclass(frozen=True)
class PartBLoadSpec:
    dataset_label: str
    dataset_slug: str
    dataset_type_uuid: str
    aggregation_keys: tuple[str, ...]
    table_name: str
    columns: tuple[str, ...]
    row_mapper: RowMapper


def text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def required_text(row: dict[str, Any], field: str) -> str:
    value = text(row.get(field))
    if value is None:
        raise ValueError(f"Part B row requires {field}")
    return value


def integer(value: Any) -> int | None:
    normalized = text(value)
    return int(normalized) if normalized is not None else None


def decimal(value: Any) -> Decimal | None:
    normalized = text(value)
    return Decimal(normalized) if normalized is not None else None


def required_integer(row: dict[str, Any], field: str) -> int:
    value = integer(row.get(field))
    if value is None:
        raise ValueError(f"Part B row requires numeric {field}")
    return value


def required_decimal(row: dict[str, Any], field: str) -> Decimal:
    value = decimal(row.get(field))
    if value is None:
        raise ValueError(f"Part B row requires numeric {field}")
    return value


def _relative_manifest_path(manifest_path: Path) -> str:
    try:
        relative_path = manifest_path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"Manifest is outside the repository: {manifest_path}") from error
    if not relative_path.startswith("research/data-manifests/"):
        raise ValueError("Part B manifests must be committed under research/data-manifests/")
    return relative_path


def _validate_files(files: Sequence[dict[str, Any]]) -> None:
    """Validate immutable inputs without materializing complete files in memory."""
    for file_record in files:
        path = Path(file_record["relative_path"])
        if not path.is_file():
            raise FileNotFoundError(f"Manifest file is missing: {path}")
        if path.stat().st_size != file_record["bytes"]:
            raise ValueError(f"Manifest byte count does not match: {path}")
        digest = sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != file_record["sha256"]:
            raise ValueError(f"Manifest SHA-256 does not match: {path}")


def _validate_manifest_identity(
    manifest: dict[str, Any],
    spec: PartBLoadSpec,
) -> tuple[int, list[dict[str, Any]]]:
    dataset = manifest["dataset"]
    if dataset["dataset_id"] != spec.dataset_type_uuid or dataset["slug"] != spec.dataset_slug:
        raise ValueError(f"Manifest is not for {spec.dataset_label}")
    if dataset["aggregation_keys"] != list(spec.aggregation_keys):
        raise ValueError(
            f"Manifest aggregation keys do not match {spec.dataset_label} source grain"
        )
    data_year = dataset["data_year"]
    if not isinstance(data_year, int):
        raise ValueError("Part B manifests require an integer data_year")
    if manifest["retrieval"]["method"] != "api":
        raise ValueError("Part B page loaders require a retained API acquisition")

    parameters = manifest["retrieval"]["parameters"]
    filters = parameters.get("filters", {})
    if not isinstance(filters, dict):
        raise ValueError("Part B API manifests require structured retrieval filters")
    for key, value in filters.items():
        valid_value = isinstance(value, str) or (
            isinstance(value, list)
            and bool(value)
            and all(isinstance(item, str) and item for item in value)
        )
        if not isinstance(key, str) or not key or not valid_value:
            raise ValueError(
                "Part B API manifest filters require nonempty string keys and "
                "string or string-list values"
            )
    page_size = parameters.get("page_size")
    if not isinstance(page_size, int) or page_size <= 0:
        raise ValueError("Part B API manifests require a positive page_size")
    if parameters.get("pagination") != "offset":
        raise ValueError("Part B API manifests require bounded offset pagination")

    files = manifest["files"]
    api_files = [file_record for file_record in files if file_record["role"] == API_PAGE_ROLE]
    if not api_files:
        raise ValueError("Part B API manifests require at least one retained api-page")
    for file_record in api_files:
        if file_record["bytes"] > MAX_API_PAGE_BYTES:
            raise ValueError("Part B API page exceeds the 64 MiB page-at-a-time ingestion ceiling")
    return data_year, api_files


def _page_rows(path: Path) -> Iterator[dict[str, Any]]:
    if path.stat().st_size > MAX_API_PAGE_BYTES:
        raise ValueError("Part B API page exceeds the 64 MiB page-at-a-time ingestion ceiling")
    with path.open(encoding="utf-8") as stream:
        page = json.load(stream)
    if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
        raise ValueError(f"Part B API page is not a JSON object array: {path}")
    yield from page


def _register_source_release(
    connection: psycopg.Connection[Any],
    manifest: dict[str, Any],
    manifest_hash: str,
    manifest_relative_path: str,
    observed_at: datetime,
    data_year: int,
) -> int:
    retrieval_filters = manifest["retrieval"]["parameters"].get("filters", {})
    dataset_id = connection.execute(
        """
        insert into metadata.source_dataset (source, slug, name, landing_page)
        values (%s, %s, %s, %s)
        on conflict (source, slug) do update
        set name = excluded.name, landing_page = excluded.landing_page
        returning dataset_id
        """,
        (
            manifest["source"]["name"],
            manifest["dataset"]["slug"],
            manifest["dataset"]["name"],
            manifest["documentation"]["landing_page"],
        ),
    ).fetchone()[0]

    existing_release = connection.execute(
        """
        select source_release_id, manifest_sha256
        from metadata.source_release
        where manifest_path = %s
        """,
        (manifest_relative_path,),
    ).fetchone()
    if existing_release:
        stored_manifest_hash = existing_release[1].strip()
        if stored_manifest_hash != manifest_hash:
            raise ValueError(
                "Committed manifest content differs from the previously loaded manifest hash"
            )
        connection.execute(
            """
            update metadata.source_release
            set retrieval_filters = %s::jsonb
            where source_release_id = %s
            """,
            (json.dumps(retrieval_filters), existing_release[0]),
        )
        return int(existing_release[0])

    return int(
        connection.execute(
            """
            insert into metadata.source_release (
                dataset_id, data_year, version_id, published_at, modified_at,
                accessed_at, observed_at, population, aggregation_keys, suppression,
                exclusions, retrieval_filters, status, manifest_path, manifest_sha256
            )
            values (
                %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s,
                %s::jsonb, %s::jsonb, 'validated', %s, %s
            )
            returning source_release_id
            """,
            (
                dataset_id,
                data_year,
                manifest["dataset"]["version_id"],
                manifest["observation"]["published_at"],
                manifest["observation"]["modified_at"],
                manifest["observation"]["accessed_at"],
                observed_at,
                manifest["dataset"]["population"],
                json.dumps(manifest["dataset"]["aggregation_keys"]),
                manifest["semantics"]["suppression"],
                json.dumps(manifest["semantics"]["exclusions"]),
                json.dumps(retrieval_filters),
                manifest_relative_path,
                manifest_hash,
            ),
        ).fetchone()[0]
    )


def _register_release_files(
    connection: psycopg.Connection[Any],
    source_release_id: int,
    files: Sequence[dict[str, Any]],
    observed_at: datetime,
) -> None:
    for ordinal, file_record in enumerate(files, start=1):
        content_artifact_id = connection.execute(
            """
            insert into metadata.content_artifact (bytes, sha256, media_type)
            values (%s, %s, %s)
            on conflict (sha256) do update set sha256 = excluded.sha256
            returning content_artifact_id
            """,
            (file_record["bytes"], file_record["sha256"], file_record["media_type"]),
        ).fetchone()[0]
        connection.execute(
            """
            insert into metadata.source_release_file (
                source_release_id, content_artifact_id, relative_path, filename,
                role, ordinal, retrieved_at, validation
            )
            values (%s, %s, %s, %s, %s, %s, %s, '{}'::jsonb)
            on conflict (source_release_id, relative_path) do nothing
            """,
            (
                source_release_id,
                content_artifact_id,
                file_record["relative_path"],
                file_record["filename"],
                file_record["role"],
                ordinal,
                observed_at,
            ),
        )


def load_part_b_pages(
    database_url: str,
    manifest_path: Path,
    *,
    spec: PartBLoadSpec,
    code_commit: str,
    pipeline_version: str,
    case_id: str | None = None,
) -> tuple[int, int]:
    """Load immutable CMS API pages into one release-scoped Part B fact table."""
    manifest, manifest_hash = load_and_validate_manifest(manifest_path)
    data_year, api_files = _validate_manifest_identity(manifest, spec)
    manifest_relative_path = _relative_manifest_path(manifest_path)
    observed_at = datetime.fromisoformat(
        manifest["observation"]["observed_at"].replace("Z", "+00:00")
    )
    files = manifest["files"]
    _validate_files(files)

    expected_rows = manifest["validation"]["rows"]
    if not isinstance(expected_rows, int) or expected_rows < 0:
        raise ValueError("Part B manifests require a nonnegative validation row count")

    with psycopg.connect(database_url) as connection, connection.transaction():
        source_release_id = _register_source_release(
            connection,
            manifest,
            manifest_hash,
            manifest_relative_path,
            observed_at,
            data_year,
        )
        _register_release_files(connection, source_release_id, files, observed_at)

        ingestion_run_id = connection.execute(
            """
            insert into metadata.ingestion_run (
                source_release_id, case_id, pipeline_version, code_commit, status
            )
            values (%s, %s, %s, %s, 'running')
            returning ingestion_run_id
            """,
            (source_release_id, case_id, pipeline_version, code_commit),
        ).fetchone()[0]
        connection.execute(
            """
            insert into metadata.ingestion_run_file (
                ingestion_run_id, source_release_id, source_release_file_id, role, ordinal
            )
            select %s, source_release_id, source_release_file_id, role, ordinal
            from metadata.source_release_file
            where source_release_id = %s
            order by ordinal
            """,
            (ingestion_run_id, source_release_id),
        )

        table = sql.Identifier("claims", spec.table_name)
        connection.execute(
            sql.SQL("delete from {} where source_release_id = %s").format(table),
            (source_release_id,),
        )

        loaded_rows = 0
        copy_statement = sql.SQL("copy {} ({}) from stdin").format(
            table,
            sql.SQL(", ").join(map(sql.Identifier, spec.columns)),
        )
        with connection.cursor().copy(copy_statement) as copy:
            for file_record in api_files:
                page_path = Path(file_record["relative_path"])
                for row in _page_rows(page_path):
                    copy.write_row(spec.row_mapper(row, source_release_id, data_year))
                    loaded_rows += 1

        if loaded_rows != expected_rows:
            raise ValueError(
                f"Loaded row count {loaded_rows} does not match manifest row count {expected_rows}"
            )

        connection.execute(
            """
            insert into metadata.data_quality_result (
                ingestion_run_id, check_name, status, observed, details
            )
            values
                (%s, 'manifest-file-integrity', 'pass', %s::jsonb,
                 'All retained files matched manifest byte counts and SHA-256 hashes.'),
                (%s, 'loaded-row-count', 'pass', %s::jsonb,
                 'Loaded rows matched the complete manifest validation count.'),
                (%s, 'part-b-published-grain', 'pass', %s::jsonb,
                 'Rows were loaded without collapsing the CMS published aggregation grain.')
            """,
            (
                ingestion_run_id,
                json.dumps({"validated_files": len(files)}),
                ingestion_run_id,
                json.dumps({"expected": expected_rows, "loaded": loaded_rows}),
                ingestion_run_id,
                json.dumps(
                    {
                        "table": f"claims.{spec.table_name}",
                        "aggregation_keys": list(spec.aggregation_keys),
                    }
                ),
            ),
        )
        connection.execute(
            """
            update metadata.ingestion_run
            set status = 'succeeded', completed_at = now(), source_rows = %s,
                loaded_rows = %s, rejected_rows = 0
            where ingestion_run_id = %s
            """,
            (loaded_rows, loaded_rows, ingestion_run_id),
        )
        connection.execute(
            "update metadata.source_release set status = 'loaded' where source_release_id = %s",
            (source_release_id,),
        )

    return source_release_id, loaded_rows
