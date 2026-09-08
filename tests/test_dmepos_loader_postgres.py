from __future__ import annotations

import csv
import json
import os
import shutil
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from drlf.database import apply_migrations
from drlf.ingestion.dmepos_supplier import (
    AGGREGATION_KEYS,
    DATASET_NAME,
    DATASET_SLUG,
    DATASET_TYPE_UUID,
    SOURCE_COLUMNS,
    load_dmepos_supplier,
)
from drlf.sources.dmepos_manifest import KNOWN_SUPPLIER_RELEASES

TEST_DATABASE_URL = os.environ.get("DRLF_TEST_DATABASE_URL")
MIGRATIONS = (Path(__file__).parents[1] / "sql" / "migrations").resolve()
SOURCE_SCHEMA = (
    Path(__file__).parents[1] / "research" / "data-manifests" / "source-manifest.schema.json"
).resolve()


def _supplier_row(npi: str = "1003000390") -> dict[str, str]:
    row = {column: "" for column in SOURCE_COLUMNS}
    row.update(
        {
            "Suplr_NPI": npi,
            "Suplr_Prvdr_Last_Name_Org": "Example Supplier",
            "Suplr_Prvdr_Ent_Cd": "O",
            "Suplr_Prvdr_State_Abrvtn": "IN",
            "Suplr_Prvdr_Zip5": "04603",
            "Suplr_Prvdr_Spclty_Desc": "Medical Supply Company with Orthotist",
            "Suplr_Prvdr_Spclty_Srce": "NPPES",
            "Tot_Suplr_HCPCS_Cds": "14",
            "Tot_Suplr_Benes": "238",
            "Tot_Suplr_Clms": "244",
            "Tot_Suplr_Srvcs": "256.750",
            "Suplr_Sbmtd_Chrgs": "82895.123456789",
            "Suplr_Mdcr_Alowd_Amt": "63465.400000001",
            "Suplr_Mdcr_Pymt_Amt": "48799.370000002",
            "Suplr_Mdcr_Stdzd_Pymt_Amt": "48189.270000003",
            "DME_Sprsn_Ind": "*",
            "POS_Tot_Suplr_HCPCS_Cds": "14",
            "POS_Tot_Suplr_Benes": "238",
            "POS_Tot_Suplr_Clms": "244",
            "POS_Tot_Suplr_Srvcs": "256.125",
            "POS_Suplr_Sbmtd_Chrgs": "82895.123456789",
            "POS_Suplr_Mdcr_Alowd_Amt": "63465.400000001",
            "POS_Suplr_Mdcr_Pymt_Amt": "48799.370000002",
            "POS_Suplr_Mdcr_Stdzd_Pymt_Amt": "48189.270000003",
            "Bene_Avg_Age": "73.133891213",
            "Bene_Age_65_74_Cnt": "137",
            "Bene_Ndual_Cnt": "226",
            "Bene_Dual_Cnt": "12",
            "Bene_CC_BH_Tobacco_V1_Pct": "0.0714285714",
            "Bene_CC_PH_Asthma_V2_Pct": "0.1596638655",
            "Bene_CC_PH_COPD_V2_Pct": "0.1218487395",
            "Bene_CC_PH_Hypertension_V2_Pct": "1.1",
            "Bene_Avg_Risk_Scre": "0.8378870293",
        }
    )
    return row


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True)
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=SOURCE_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_manifest(
    repository: Path,
    *,
    token: str,
    rows: list[dict[str, str]],
    expected_rows: int | None = None,
    observed_at: str = "2026-09-02T00:00:00Z",
) -> Path:
    relative_csv = Path(f"data/raw/cms/dmepos-supplier/2024/{token}/supplier.csv")
    csv_path = repository / relative_csv
    _write_csv(csv_path, rows)
    body_hash = sha256(csv_path.read_bytes()).hexdigest()
    schema_fingerprint = sha256("\n".join(sorted(SOURCE_COLUMNS)).encode()).hexdigest()
    manifest = {
        "manifest_version": 2,
        "status": "complete",
        "source": {
            "name": "Centers for Medicare & Medicaid Services",
            "type": "CMS",
            "homepage": "https://data.cms.gov/",
        },
        "dataset": {
            "name": DATASET_NAME,
            "slug": DATASET_SLUG,
            "dataset_id": DATASET_TYPE_UUID,
            "version_id": KNOWN_SUPPLIER_RELEASES[2024].version_id,
            "data_year": 2024,
            "population": "Fixture Original Medicare fee-for-service DMEPOS supplier rows.",
            "aggregation_keys": list(AGGREGATION_KEYS),
        },
        "observation": {
            "published_at": None,
            "modified_at": "2026-07-23",
            "observed_at": observed_at,
            "accessed_at": "2026-09-02",
        },
        "documentation": {
            "landing_page": "https://data.cms.gov/",
            "methodology": "https://data.cms.gov/",
            "data_dictionary": "https://data.cms.gov/",
            "snapshots": [],
        },
        "terms": {
            "license_name": None,
            "license_url": None,
            "access_restrictions": "CMS Public Use File (Free); no authentication or DUA",
            "public_use_verified": True,
        },
        "retrieval": {
            "method": "download",
            "request_url": "https://data.cms.gov/sites/default/files/example.csv",
            "parameters": {"max_download_bytes": 41_943_040},
            "query": None,
        },
        "files": [
            {
                "relative_path": relative_csv.as_posix(),
                "filename": csv_path.name,
                "role": "data",
                "bytes": csv_path.stat().st_size,
                "sha256": body_hash,
                "media_type": "text/csv",
                "compression": None,
                "schema_fingerprint": schema_fingerprint,
            }
        ],
        "semantics": {
            "monetary_fields": {
                "Suplr_Mdcr_Pymt_Amt": "Medicare fee-for-service payment, not net revenue."
            },
            "utilization_fields": {"Tot_Suplr_Clms": "Published supplier claim count."},
            "suppression": "Blank subgroup fields may be suppressed and are not zero.",
            "exclusions": ["Medicare Advantage"],
        },
        "validation": {
            "rows": len(rows) if expected_rows is None else expected_rows,
            "columns": len(SOURCE_COLUMNS),
            "aggregation_key_duplicates": 0,
            "notes": ["Fixture schema and grain validated."],
        },
    }
    manifest_path = repository / f"research/data-manifests/dmepos-{token}.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _delete_test_releases(database_url: str, release_ids: list[int]) -> None:
    if not release_ids:
        return
    with psycopg.connect(database_url) as connection, connection.transaction():
        content_ids = [
            row[0]
            for row in connection.execute(
                """
                select distinct content_artifact_id
                from metadata.source_release_file
                where source_release_id = any(%s)
                """,
                (release_ids,),
            ).fetchall()
        ]
        connection.execute(
            "delete from claims.dmepos_supplier where source_release_id = any(%s)",
            (release_ids,),
        )
        connection.execute(
            "delete from metadata.ingestion_run where source_release_id = any(%s)",
            (release_ids,),
        )
        connection.execute(
            "delete from metadata.source_release_file where source_release_id = any(%s)",
            (release_ids,),
        )
        connection.execute(
            "delete from metadata.source_release where source_release_id = any(%s)",
            (release_ids,),
        )
        for content_id in content_ids:
            connection.execute(
                """
                delete from metadata.content_artifact as artifact
                where artifact.content_artifact_id = %s
                  and not exists (
                      select 1 from metadata.source_release_file as release_file
                      where release_file.content_artifact_id = artifact.content_artifact_id
                  )
                """,
                (content_id,),
            )


@pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="Set DRLF_TEST_DATABASE_URL to an explicitly disposable PostgreSQL database",
)
def test_dmepos_supplier_loader_preserves_grain_lineage_and_idempotence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = str(TEST_DATABASE_URL)
    apply_migrations(database_url, MIGRATIONS)
    repository = tmp_path / "repository"
    schema_path = repository / "research/data-manifests/source-manifest.schema.json"
    schema_path.parent.mkdir(parents=True)
    shutil.copyfile(SOURCE_SCHEMA, schema_path)
    monkeypatch.chdir(repository)

    token = uuid4().hex
    manifest_path = _write_manifest(repository, token=token, rows=[_supplier_row()])
    release_ids: list[int] = []
    try:
        release_id, loaded_rows = load_dmepos_supplier(
            database_url,
            manifest_path,
            code_commit="a" * 40,
            pipeline_version="integration-test",
            case_id="CASE-9999",
        )
        release_ids.append(release_id)
        rerun_id, rerun_rows = load_dmepos_supplier(
            database_url,
            manifest_path,
            code_commit="b" * 40,
            pipeline_version="integration-test",
        )

        assert (loaded_rows, rerun_rows) == (1, 1)
        assert rerun_id == release_id
        with psycopg.connect(database_url) as connection:
            supplier = connection.execute(
                """
                select supplier_npi, supplier_zip5, total_beneficiaries, total_services,
                       total_medicare_payment_amount, dme_total_services,
                       pos_total_services, tobacco_pct,
                       beneficiary_hypertension_percent, average_risk_score
                from claims.dmepos_supplier
                where source_release_id = %s
                """,
                (release_id,),
            ).fetchone()
            assert supplier == (
                "1003000390",
                "04603",
                238,
                Decimal("256.750"),
                Decimal("48799.370000002"),
                None,
                Decimal("256.125"),
                Decimal("0.0714285714"),
                Decimal("1.1"),
                Decimal("0.8378870293"),
            )
            assert (
                connection.execute(
                    """
                select count(*)
                from metadata.ingestion_run
                where source_release_id = %s and status = 'succeeded'
                """,
                    (release_id,),
                ).fetchone()[0]
                == 2
            )
            assert (
                connection.execute(
                    """
                select count(*)
                from metadata.data_quality_result as result
                join metadata.ingestion_run as run using (ingestion_run_id)
                where run.source_release_id = %s and result.status = 'pass'
                """,
                    (release_id,),
                ).fetchone()[0]
                == 6
            )
    finally:
        _delete_test_releases(database_url, release_ids)


@pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="Set DRLF_TEST_DATABASE_URL to an explicitly disposable PostgreSQL database",
)
def test_dmepos_supplier_loader_rolls_back_incomplete_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = str(TEST_DATABASE_URL)
    apply_migrations(database_url, MIGRATIONS)
    repository = tmp_path / "repository"
    schema_path = repository / "research/data-manifests/source-manifest.schema.json"
    schema_path.parent.mkdir(parents=True)
    shutil.copyfile(SOURCE_SCHEMA, schema_path)
    monkeypatch.chdir(repository)

    token = uuid4().hex
    manifest_path = _write_manifest(
        repository,
        token=token,
        rows=[_supplier_row()],
        expected_rows=2,
    )
    with pytest.raises(ValueError, match="Loaded row count 1 does not match manifest row count 2"):
        load_dmepos_supplier(
            database_url,
            manifest_path,
            code_commit="c" * 40,
            pipeline_version="rollback-test",
        )

    relative_manifest = manifest_path.relative_to(repository).as_posix()
    with psycopg.connect(database_url) as connection:
        assert (
            connection.execute(
                "select count(*) from metadata.source_release where manifest_path = %s",
                (relative_manifest,),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "select count(*) from claims.dmepos_supplier where supplier_npi = %s",
                ("1003000390",),
            ).fetchone()[0]
            == 0
        )
