from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

import psycopg

from drlf.sources.manifest import load_and_validate_manifest
from drlf.sources.part_d_geography_manifest import (
    AGGREGATION_KEYS,
    DATASET_SLUG,
    DATASET_TYPE_UUID,
)


def _integer(value: Any) -> int | None:
    return int(value) if value not in (None, "") else None


def _decimal(value: Any) -> Decimal | None:
    return Decimal(value) if value not in (None, "") else None


def _row_values(row: dict[str, Any], source_release_id: int, data_year: int) -> tuple[Any, ...]:
    return (
        source_release_id,
        data_year,
        str(row["Prscrbr_Geo_Lvl"]),
        str(row.get("Prscrbr_Geo_Cd", "")),
        row["Prscrbr_Geo_Desc"],
        row["Brnd_Name"],
        row["Gnrc_Name"],
        _integer(row.get("Tot_Prscrbrs")),
        _integer(row.get("Tot_Clms")),
        _decimal(row.get("Tot_30day_Fills")),
        _decimal(row.get("Tot_Drug_Cst")),
        _integer(row.get("Tot_Benes")),
        row.get("GE65_Sprsn_Flag") or None,
        _integer(row.get("GE65_Tot_Clms")),
        _decimal(row.get("GE65_Tot_30day_Fills")),
        _decimal(row.get("GE65_Tot_Drug_Cst")),
        row.get("GE65_Bene_Sprsn_Flag") or None,
        _integer(row.get("GE65_Tot_Benes")),
        _decimal(row.get("LIS_Bene_Cst_Shr")),
        _decimal(row.get("NonLIS_Bene_Cst_Shr")),
        row.get("Opioid_Drug_Flag") or None,
        row.get("Opioid_LA_Drug_Flag") or None,
        row.get("Antbtc_Drug_Flag") or None,
        row.get("Antpsyct_Drug_Flag") or None,
    )


def _relative_manifest_path(manifest_path: Path) -> str:
    return manifest_path.resolve().relative_to(Path.cwd().resolve()).as_posix()


def _validate_files(files: list[dict[str, Any]]) -> None:
    for file_record in files:
        path = Path(file_record["relative_path"])
        if not path.is_file():
            raise FileNotFoundError(f"Manifest file is missing: {path}")
        file_bytes = path.read_bytes()
        if len(file_bytes) != file_record["bytes"]:
            raise ValueError(f"Manifest byte count does not match: {path}")
        if sha256(file_bytes).hexdigest() != file_record["sha256"]:
            raise ValueError(f"Manifest SHA-256 does not match: {path}")


def _validate_manifest_identity(manifest: dict[str, Any]) -> int:
    dataset = manifest["dataset"]
    if dataset["dataset_id"] != DATASET_TYPE_UUID or dataset["slug"] != DATASET_SLUG:
        raise ValueError("Manifest is not for CMS Part D Prescribers by Geography and Drug")
    if dataset["aggregation_keys"] != list(AGGREGATION_KEYS):
        raise ValueError("Manifest aggregation keys do not match the geography-and-drug grain")
    data_year = dataset["data_year"]
    if not isinstance(data_year, int):
        raise ValueError("Part D geography/drug manifests require an integer data_year")
    return data_year


def load_part_d_geography_drug(
    database_url: str,
    manifest_path: Path,
    *,
    code_commit: str,
    pipeline_version: str,
    case_id: str | None = None,
) -> tuple[int, int]:
    """Load a complete filtered Part D geography/drug manifest into PostgreSQL."""
    manifest, manifest_hash = load_and_validate_manifest(manifest_path)
    data_year = _validate_manifest_identity(manifest)
    manifest_relative_path = _relative_manifest_path(manifest_path)
    observed_at = datetime.fromisoformat(
        manifest["observation"]["observed_at"].replace("Z", "+00:00")
    )
    files = manifest["files"]
    _validate_files(files)

    with psycopg.connect(database_url) as connection, connection.transaction():
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
            source_release_id = existing_release[0]
            stored_manifest_hash = existing_release[1].strip()
            if stored_manifest_hash != manifest_hash:
                raise ValueError(
                    "Committed manifest content differs from the previously loaded manifest hash"
                )
        else:
            source_release_id = connection.execute(
                """
                insert into metadata.source_release (
                    dataset_id, data_year, version_id, published_at, modified_at,
                    accessed_at, observed_at, population, aggregation_keys, suppression,
                    exclusions, status, manifest_path, manifest_sha256
                )
                values (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s,
                    %s::jsonb, 'validated', %s, %s
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
                    manifest_relative_path,
                    manifest_hash,
                ),
            ).fetchone()[0]

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

        connection.execute(
            "delete from claims.part_d_geography_drug where source_release_id = %s",
            (source_release_id,),
        )

        loaded_rows = 0
        columns = (
            "source_release_id, data_year, prescriber_geography_level, "
            "prescriber_geography_code, prescriber_geography_description, brand_name, "
            "generic_name, total_prescribers, total_claims, total_30day_fills, "
            "total_drug_cost, total_beneficiaries, ge65_suppression_flag, "
            "ge65_total_claims, ge65_total_30day_fills, ge65_total_drug_cost, "
            "ge65_beneficiary_suppression_flag, ge65_total_beneficiaries, "
            "lis_beneficiary_cost_share, nonlis_beneficiary_cost_share, opioid_drug_flag, "
            "long_acting_opioid_drug_flag, antibiotic_drug_flag, antipsychotic_drug_flag"
        )
        with connection.cursor().copy(
            f"copy claims.part_d_geography_drug ({columns}) from stdin"  # noqa: S608
        ) as copy:
            for file_record in files:
                if file_record["role"] != "api-page":
                    continue
                page_path = Path(file_record["relative_path"])
                page_rows = json.loads(page_path.read_bytes())
                for row in page_rows:
                    copy.write_row(_row_values(row, source_release_id, data_year))
                    loaded_rows += 1

        expected_rows = manifest["validation"]["rows"]
        if expected_rows is not None and loaded_rows != expected_rows:
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
                 'Loaded rows matched the complete manifest validation count.')
            """,
            (
                ingestion_run_id,
                json.dumps({"validated_files": len(files)}),
                ingestion_run_id,
                json.dumps({"expected": expected_rows, "loaded": loaded_rows}),
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
