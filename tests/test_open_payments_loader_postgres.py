from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from drlf.database import apply_migrations
from drlf.ingestion.open_payments_general import load_open_payments_general
from drlf.sources.open_payments import build_open_payments_sql_api_manifest

TEST_DATABASE_URL = os.environ.get("DRLF_TEST_DATABASE_URL")
MIGRATIONS = (Path(__file__).parents[1] / "sql" / "migrations").resolve()
SOURCE_SCHEMA = (
    Path(__file__).parents[1]
    / "research"
    / "data-manifests"
    / "source-manifest.schema.json"
).resolve()


def _row(*, record_id: str, amount: str = "125.00") -> dict[str, str]:
    return {
        "Program_Year": "2024",
        "Record_ID": record_id,
        "Covered_Recipient_Type": "Covered Recipient Physician",
        "Covered_Recipient_Profile_ID": "1001",
        "Covered_Recipient_NPI": "1234567890",
        "Covered_Recipient_First_Name": "TEST",
        "Covered_Recipient_Last_Name": "RECIPIENT",
        "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_ID": (
            "SYNTHETIC-OPEN-PAYMENTS-ORG"
        ),
        "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name": "Test Paying Entity",
        "Total_Amount_of_Payment_USDollars": amount,
        "Date_of_Payment": "03/05/2024",
        "Number_of_Payments_Included_in_Total_Amount": "1",
        "Nature_of_Payment_or_Transfer_of_Value": "Consulting Fee",
        "Related_Product_Indicator": "Yes",
        "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1": "First Product",
        "Associated_Drug_or_Biological_NDC_2": "00000-0000-00",
    }


def _build_manifest(
    *,
    token: str,
    row: dict[str, str],
    observed_at: datetime,
) -> tuple[Path, Path]:
    raw_path = Path(
        f"data/raw/cms/open-payments/general/2024/integration-{token}/response.json"
    )
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text(json.dumps([row]), encoding="utf-8")
    manifest_path = Path(f"research/data-manifests/integration-{token}.json")
    build_open_payments_sql_api_manifest(
        raw_path,
        manifest_path,
        dataset_name="Open Payments General Payment integration fixture",
        dataset_slug="open-payments-general-payments",
        program_year=2024,
        payment_category="general",
        dataset_id="e6b17c6a-2534-4207-a4a1-6746a14911ff",
        resource_id="93b512a7-7f65-539b-9e20-bbcc535750bd",
        request_url="https://openpaymentsdata.cms.gov/api/1/datastore/sql?query=integration",
        query=(
            "[SELECT * FROM 93b512a7-7f65-539b-9e20-bbcc535750bd]"
            "[WHERE applicable_manufacturer_or_applicable_gpo_making_payment_id "
            '= "SYNTHETIC-OPEN-PAYMENTS-ORG"]'
        ),
        exact_filters={
            "applicable_manufacturer_or_applicable_gpo_making_payment_id": (
                "SYNTHETIC-OPEN-PAYMENTS-ORG"
            )
        },
        observed_at=observed_at,
        landing_page=(
            "https://openpaymentsdata.cms.gov/dataset/"
            "e6b17c6a-2534-4207-a4a1-6746a14911ff"
        ),
    )
    return raw_path, manifest_path


def _delete_test_release(database_url: str, source_release_id: int) -> None:
    with psycopg.connect(database_url) as connection, connection.transaction():
        content_ids = [
            row[0]
            for row in connection.execute(
                """
                select content_artifact_id
                from metadata.source_release_file
                where source_release_id = %s
                """,
                (source_release_id,),
            ).fetchall()
        ]
        connection.execute(
            "delete from relationships.open_payments_general where source_release_id = %s",
            (source_release_id,),
        )
        connection.execute(
            "delete from metadata.ingestion_run where source_release_id = %s",
            (source_release_id,),
        )
        connection.execute(
            "delete from metadata.source_release_file where source_release_id = %s",
            (source_release_id,),
        )
        connection.execute(
            "delete from metadata.source_release where source_release_id = %s",
            (source_release_id,),
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
def test_loader_lineage_idempotence_hash_refusal_and_rollback(
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
    observed_at = datetime.now(UTC)
    _, manifest_path = _build_manifest(
        token=token,
        row=_row(record_id=f"valid-{token}"),
        observed_at=observed_at,
    )
    manifest_bytes = manifest_path.read_bytes()
    source_release_id: int | None = None
    try:
        source_release_id, loaded_rows = load_open_payments_general(
            database_url,
            manifest_path,
            code_commit="a" * 40,
            pipeline_version="integration-test",
            case_id="CASE-9999",
        )
        rerun_release_id, rerun_rows = load_open_payments_general(
            database_url,
            manifest_path,
            code_commit="b" * 40,
            pipeline_version="integration-test",
            case_id="CASE-9999",
        )

        assert (loaded_rows, rerun_rows) == (1, 1)
        assert rerun_release_id == source_release_id
        with psycopg.connect(database_url) as connection:
            release = connection.execute(
                """
                select btrim(manifest_sha256), status
                from metadata.source_release
                where source_release_id = %s
                """,
                (source_release_id,),
            ).fetchone()
            assert release == (sha256(manifest_bytes).hexdigest(), "loaded")
            assert connection.execute(
                """
                select count(*) from metadata.ingestion_run
                where source_release_id = %s and status = 'succeeded'
                """,
                (source_release_id,),
            ).fetchone()[0] == 2
            assert connection.execute(
                """
                select count(*) from metadata.ingestion_run_file
                where source_release_id = %s
                """,
                (source_release_id,),
            ).fetchone()[0] == 2
            product_slots = connection.execute(
                """
                select product_slots from relationships.open_payments_general
                where source_release_id = %s
                """,
                (source_release_id,),
            ).fetchone()[0]
            assert product_slots[0]["slot"] == 1
            assert product_slots[0]["name"] == "First Product"
            assert product_slots[1]["slot"] == 2
            assert product_slots[1]["ndc"] == "00000-0000-00"

        changed_manifest = json.loads(manifest_bytes)
        changed_manifest["validation"]["notes"].append("Changed after the recorded load.")
        manifest_path.write_text(json.dumps(changed_manifest), encoding="utf-8")
        with pytest.raises(ValueError, match="previously loaded manifest hash"):
            load_open_payments_general(
                database_url,
                manifest_path,
                code_commit="c" * 40,
                pipeline_version="integration-test",
            )
        manifest_path.write_bytes(manifest_bytes)

        invalid_token = uuid4().hex
        _, invalid_manifest_path = _build_manifest(
            token=invalid_token,
            row=_row(record_id=f"invalid-{invalid_token}", amount="-1.00"),
            observed_at=datetime.now(UTC),
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            load_open_payments_general(
                database_url,
                invalid_manifest_path,
                code_commit="d" * 40,
                pipeline_version="integration-test",
            )

        with psycopg.connect(database_url) as connection:
            invalid_relative_path = invalid_manifest_path.resolve().relative_to(
                repository.resolve()
            ).as_posix()
            assert connection.execute(
                "select count(*) from metadata.source_release where manifest_path = %s",
                (invalid_relative_path,),
            ).fetchone()[0] == 0
            assert connection.execute(
                """
                select count(*) from relationships.open_payments_general
                where source_release_id = %s
                """,
                (source_release_id,),
            ).fetchone()[0] == 1
    finally:
        if source_release_id is not None:
            _delete_test_release(database_url, source_release_id)
