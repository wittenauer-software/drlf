from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from drlf.database import apply_migrations
from drlf.ingestion.part_b_provider import load_part_b_provider
from drlf.ingestion.part_b_provider_service import (
    load_part_b_provider_service,
)
from drlf.sources.part_b_provider_manifest import (
    REQUIRED_COLUMNS as PROVIDER_REQUIRED_COLUMNS,
)
from drlf.sources.part_b_provider_manifest import (
    build_part_b_provider_api_manifest,
)
from drlf.sources.part_b_provider_service_manifest import (
    REQUIRED_COLUMNS as SERVICE_REQUIRED_COLUMNS,
)
from drlf.sources.part_b_provider_service_manifest import (
    build_part_b_provider_service_api_manifest,
)

TEST_DATABASE_URL = os.environ.get("DRLF_TEST_DATABASE_URL")
MIGRATIONS = (Path(__file__).parents[1] / "sql" / "migrations").resolve()
SOURCE_SCHEMA = (
    Path(__file__).parents[1] / "research" / "data-manifests" / "source-manifest.schema.json"
).resolve()
SCAN_QUERY = (
    Path(__file__).parents[1] / "sql" / "analysis" / "part_b" / "provider_service_scan.sql"
).resolve()


def _provider_row(npi: str) -> dict[str, str]:
    row = {column: "" for column in PROVIDER_REQUIRED_COLUMNS}
    row.update(
        {
            "Rndrng_NPI": npi,
            "Rndrng_Prvdr_Last_Org_Name": "Example",
            "Rndrng_Prvdr_First_Name": "Allergist",
            "Rndrng_Prvdr_Ent_Cd": "I",
            "Rndrng_Prvdr_State_Abrvtn": "IL",
            "Rndrng_Prvdr_Type": "Allergy/ Immunology",
            "Rndrng_Prvdr_Mdcr_Prtcptg_Ind": "Y",
            "Tot_HCPCS_Cds": "17",
            "Tot_Benes": "123",
            "Tot_Srvcs": "456.750",
            "Tot_Sbmtd_Chrg": "10000.125",
            "Tot_Mdcr_Alowd_Amt": "7000.375",
            "Tot_Mdcr_Pymt_Amt": "5400.625",
            "Tot_Mdcr_Stdzd_Amt": "5300.875",
            "Drug_Sprsn_Ind": "*",
            "Med_Sprsn_Ind": "#",
            "Bene_CC_BH_Tobacco_V1_Pct": "12.5",
            "Bene_CC_PH_Asthma_V2_Pct": "44.4",
            "Bene_CC_PH_COPD_V2_Pct": "7.1",
            "Bene_Avg_Risk_Scre": "1.23456",
        }
    )
    return row


def _service_row(npi: str) -> dict[str, str]:
    row = {column: "" for column in SERVICE_REQUIRED_COLUMNS}
    row.update(
        {
            "Rndrng_NPI": npi,
            "Rndrng_Prvdr_Last_Org_Name": "Example",
            "Rndrng_Prvdr_First_Name": "Allergist",
            "Rndrng_Prvdr_Ent_Cd": "I",
            "Rndrng_Prvdr_State_Abrvtn": "IL",
            "Rndrng_Prvdr_Type": "Allergy/ Immunology",
            "Rndrng_Prvdr_Mdcr_Prtcptg_Ind": "Y",
            "HCPCS_Cd": "TSTAB",
            "HCPCS_Desc": "Synthetic test service B",
            "HCPCS_Drug_Ind": "N",
            "Place_Of_Srvc": "O",
            "Tot_Benes": "13",
            "Tot_Srvcs": "13.750",
            "Tot_Bene_Day_Srvcs": "13.250",
            "Avg_Sbmtd_Chrg": "100.123456",
            "Avg_Mdcr_Alowd_Amt": "37.595384615",
            "Avg_Mdcr_Pymt_Amt": "27.644615385",
            "Avg_Mdcr_Stdzd_Amt": "28.123456789",
        }
    )
    return row


def _inventory(
    root: Path,
    *,
    slug: str,
    version_uuid: str,
    npi: str,
    row: dict[str, str] | list[dict[str, str]],
    filters: dict[str, str] | None = None,
) -> Path:
    snapshot_dir = root / f"data/raw/cms/{slug}/2024/integration"
    snapshot_dir.mkdir(parents=True)
    rows = row if isinstance(row, list) else [row]
    body = json.dumps(rows, separators=(",", ":")).encode()
    page_path = snapshot_dir / "page-00001-offset-000000000.json"
    page_path.write_bytes(body)
    inventory = {
        "inventory_version": 1,
        "dataset_uuid": version_uuid,
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "filters": filters or {"Rndrng_NPI": npi},
        "page_size": 5_000,
        "max_pages": 1,
        "total_rows": len(rows),
        "pages": [
            {
                "ordinal": 1,
                "offset": 0,
                "rows": len(rows),
                "url": (
                    "https://data.cms.gov/data-api/v1/dataset/"
                    f"{version_uuid}/data?size=5000&offset=0"
                ),
                "relative_filename": page_path.name,
                "bytes": len(body),
                "sha256": sha256(body).hexdigest(),
                "media_type": "application/json",
            }
        ],
    }
    inventory_path = snapshot_dir / "inventory.json"
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    return inventory_path


def _scan_parameters(
    release_ids: list[int],
    *,
    hcpcs_codes: str,
) -> dict[str, str]:
    return {
        "source_release_ids": "{" + ",".join(map(str, release_ids)) + "}",
        "from_year": "2024",
        "to_year": "2024",
        "provider_type": "Allergy/ Immunology",
        "entity_code": "I",
        "hcpcs_codes": hcpcs_codes,
    }


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
            "delete from claims.part_b_provider_service where source_release_id = any(%s)",
            (release_ids,),
        )
        connection.execute(
            "delete from claims.part_b_provider where source_release_id = any(%s)",
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
def test_part_b_loaders_preserve_grain_lineage_and_idempotence(
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
    npi = "1234567890"
    provider_inventory = _inventory(
        repository,
        slug=f"part-b-provider-{token}",
        version_uuid=str(uuid4()),
        npi=npi,
        row=_provider_row(npi),
    )
    service_inventory = _inventory(
        repository,
        slug=f"part-b-provider-service-{token}",
        version_uuid=str(uuid4()),
        npi=npi,
        row=_service_row(npi),
    )
    provider_manifest = repository / f"research/data-manifests/provider-{token}.json"
    service_manifest = repository / f"research/data-manifests/service-{token}.json"
    build_part_b_provider_api_manifest(
        provider_inventory,
        provider_manifest,
        data_year=2024,
        modified_at="2026-05-21",
        documentation_snapshots=[],
    )
    build_part_b_provider_service_api_manifest(
        service_inventory,
        service_manifest,
        data_year=2024,
        modified_at="2026-05-21",
        documentation_snapshots=[],
    )

    release_ids: list[int] = []
    try:
        provider_release_id, provider_rows = load_part_b_provider(
            database_url,
            provider_manifest,
            code_commit="a" * 40,
            pipeline_version="integration-test",
            case_id="CASE-9999",
        )
        release_ids.append(provider_release_id)
        service_release_id, service_rows = load_part_b_provider_service(
            database_url,
            service_manifest,
            code_commit="b" * 40,
            pipeline_version="integration-test",
            case_id="CASE-9999",
        )
        release_ids.append(service_release_id)

        rerun_provider_id, rerun_provider_rows = load_part_b_provider(
            database_url,
            provider_manifest,
            code_commit="c" * 40,
            pipeline_version="integration-test",
        )
        rerun_service_id, rerun_service_rows = load_part_b_provider_service(
            database_url,
            service_manifest,
            code_commit="d" * 40,
            pipeline_version="integration-test",
        )

        assert (provider_rows, service_rows, rerun_provider_rows, rerun_service_rows) == (
            1,
            1,
            1,
            1,
        )
        assert rerun_provider_id == provider_release_id
        assert rerun_service_id == service_release_id
        with psycopg.connect(database_url) as connection:
            provider = connection.execute(
                """
                select total_services, asthma_pct, average_risk_score
                from claims.part_b_provider
                where source_release_id = %s
                """,
                (provider_release_id,),
            ).fetchone()
            assert provider == (Decimal("456.750"), Decimal("44.4"), Decimal("1.23456"))
            service = connection.execute(
                """
                select hcpcs_code, place_of_service, total_services,
                       average_medicare_payment_amount
                from claims.part_b_provider_service
                where source_release_id = %s
                """,
                (service_release_id,),
            ).fetchone()
            assert service == (
                "TSTAB",
                "O",
                Decimal("13.750"),
                Decimal("27.644615385"),
            )
            for release_id in release_ids:
                assert (
                    connection.execute(
                        "select count(*) from metadata.ingestion_run "
                        "where source_release_id = %s and status = 'succeeded'",
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

        original_manifest = provider_manifest.read_bytes()
        changed_manifest = json.loads(original_manifest)
        changed_manifest["validation"]["notes"].append("Changed after the recorded load.")
        provider_manifest.write_text(json.dumps(changed_manifest), encoding="utf-8")
        with pytest.raises(ValueError, match="previously loaded manifest hash"):
            load_part_b_provider(
                database_url,
                provider_manifest,
                code_commit="e" * 40,
                pipeline_version="integration-test",
            )
    finally:
        _delete_test_releases(database_url, release_ids)


@pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="Set DRLF_TEST_DATABASE_URL to an explicitly disposable PostgreSQL database",
)
def test_part_b_scan_requires_every_requested_code_and_retains_low_volume_cells(
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
    npi = "1234567890"
    singleton_npi = "2234567890"
    provider_inventory = _inventory(
        repository,
        slug=f"part-b-provider-scan-{token}",
        version_uuid=str(uuid4()),
        npi=npi,
        row=[_provider_row(npi), _provider_row(singleton_npi)],
        filters={"Rndrng_Prvdr_Type": "Allergy/ Immunology"},
    )
    low_volume_service = _service_row(npi)
    low_volume_service["Tot_Benes"] = "13"
    singleton_service = _service_row(singleton_npi)
    singleton_service.update(
        {"Tot_Benes": "20", "Tot_Srvcs": "20.750", "Tot_Bene_Day_Srvcs": "20.250"}
    )
    service_inventory = _inventory(
        repository,
        slug=f"part-b-provider-service-scan-{token}",
        version_uuid=str(uuid4()),
        npi=npi,
        row=[low_volume_service, singleton_service],
        filters={
            "HCPCS_Cd": "TSTAB",
            "Rndrng_Prvdr_Type": "Allergy/ Immunology",
        },
    )
    provider_manifest = repository / f"research/data-manifests/provider-scan-{token}.json"
    service_manifest = repository / f"research/data-manifests/service-scan-{token}.json"
    narrowed_inventory = _inventory(
        repository,
        slug=f"part-b-provider-service-narrowed-scan-{token}",
        version_uuid=str(uuid4()),
        npi="3234567890",
        row=_service_row("3234567890"),
        filters={
            "HCPCS_Cd": "TSTAB",
            "Rndrng_Prvdr_Type": "Allergy/ Immunology",
            "Rndrng_Prvdr_State_Abrvtn": "IL",
        },
    )
    narrowed_manifest = repository / f"research/data-manifests/narrowed-scan-{token}.json"
    build_part_b_provider_api_manifest(
        provider_inventory,
        provider_manifest,
        data_year=2024,
        modified_at="2026-05-21",
        documentation_snapshots=[],
    )
    build_part_b_provider_service_api_manifest(
        service_inventory,
        service_manifest,
        data_year=2024,
        modified_at="2026-05-21",
        documentation_snapshots=[],
    )
    build_part_b_provider_service_api_manifest(
        narrowed_inventory,
        narrowed_manifest,
        data_year=2024,
        modified_at="2026-05-21",
        documentation_snapshots=[],
    )

    release_ids: list[int] = []
    try:
        provider_release_id, _ = load_part_b_provider(
            database_url,
            provider_manifest,
            code_commit="a" * 40,
            pipeline_version="scan-integration-test",
        )
        release_ids.append(provider_release_id)
        service_release_id, _ = load_part_b_provider_service(
            database_url,
            service_manifest,
            code_commit="b" * 40,
            pipeline_version="scan-integration-test",
        )
        release_ids.append(service_release_id)

        query = SCAN_QUERY.read_text(encoding="utf-8")
        with psycopg.connect(database_url) as connection:
            filters = connection.execute(
                """
                select retrieval_filters
                from metadata.source_release
                where source_release_id = %s
                """,
                (service_release_id,),
            ).fetchone()[0]
            assert filters == {
                "HCPCS_Cd": "TSTAB",
                "Rndrng_Prvdr_Type": "Allergy/ Immunology",
            }

            result = connection.execute(
                query,
                _scan_parameters(release_ids, hcpcs_codes="{TSTAB}"),
            )
            columns = [column.name for column in result.description]
            rows = {
                row[columns.index("rendering_npi")]: dict(zip(columns, row, strict=True))
                for row in result.fetchall()
            }
            values = rows[npi]
            assert values["tested_beneficiaries"] == 13
            assert values["peer_evaluation_status"] == "insufficient-volume-descriptive"
            assert values["triage_route"] == "insufficient-volume"
            assert values["peer_count"] is None
            assert values["comparable_years"] == 0
            assert values["reach_outlier_flag"] is False
            assert values["repeat_day_intensity_flag"] is False
            assert values["unit_intensity_flag"] is False

            singleton = rows[singleton_npi]
            assert singleton["tested_beneficiaries"] == 20
            assert singleton["peer_evaluation_status"] == "insufficient-peer-descriptive"
            assert singleton["peer_count"] == 1
            assert singleton["reach_scale_source"] == "degenerate"
            assert singleton["repeat_days_scale_source"] == "degenerate"
            assert singleton["units_per_day_scale_source"] == "degenerate"
            assert singleton["reach_robust_z"] is None
            assert singleton["repeat_days_robust_z"] is None
            assert singleton["units_per_day_robust_z"] is None

        with psycopg.connect(database_url, autocommit=True) as connection:
            with pytest.raises(
                psycopg.errors.InvalidTextRepresentation,
                match="2024/TSTAD=0",
            ):
                connection.execute(
                    query,
                    _scan_parameters(release_ids, hcpcs_codes="{TSTAB,TSTAD}"),
                ).fetchall()

            narrowed_release_id, _ = load_part_b_provider_service(
                database_url,
                narrowed_manifest,
                code_commit="c" * 40,
                pipeline_version="scan-integration-test",
            )
            release_ids.append(narrowed_release_id)
            with pytest.raises(
                psycopg.errors.InvalidTextRepresentation,
                match="unexpected narrowed releases",
            ):
                connection.execute(
                    query,
                    _scan_parameters(release_ids, hcpcs_codes="{TSTAB}"),
                ).fetchall()
    finally:
        _delete_test_releases(database_url, release_ids)
