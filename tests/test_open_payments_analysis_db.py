from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from drlf.config import get_settings

RUN_DB_TESTS = os.environ.get("DRLF_RUN_DB_TESTS") == "1"
pytestmark = pytest.mark.skipif(
    not RUN_DB_TESTS,
    reason="set DRLF_RUN_DB_TESTS=1 to run local PostgreSQL fixture tests",
)

QUERY_DIR = Path("sql/analysis/open_payments")
ORGANIZATION_ID = "test-paying-entity"


@pytest.fixture
def open_payments_fixture() -> Iterator[tuple[psycopg.Connection, dict[int, int]]]:
    connection = psycopg.connect(get_settings().database_url.get_secret_value())
    token = uuid4().hex
    try:
        dataset_row = connection.execute(
            """
            select dataset_id
            from metadata.source_dataset
            where source = 'Centers for Medicare & Medicaid Services Open Payments'
              and slug = 'open-payments-general-payments'
            """
        ).fetchone()
        if dataset_row is None:
            dataset_id = connection.execute(
                """
                insert into metadata.source_dataset (source, slug, name, landing_page)
                values (
                    'Centers for Medicare & Medicaid Services Open Payments',
                    'open-payments-general-payments',
                    'Open Payments General Payment fixture',
                    'https://openpaymentsdata.cms.gov/'
                )
                returning dataset_id
                """
            ).fetchone()[0]
        else:
            dataset_id = dataset_row[0]

        releases: dict[int, int] = {}
        for ordinal, year in enumerate((2023, 2024, 2025), start=1):
            release_id = connection.execute(
                """
                insert into metadata.source_release (
                    dataset_id, data_year, version_id, accessed_at, observed_at,
                    population, aggregation_keys, suppression, exclusions, status,
                    manifest_path, manifest_sha256
                )
                values (
                    %s, %s, %s, %s, %s, 'Synthetic test cohort', '["record_id"]'::jsonb,
                    'Synthetic test cohort', '[]'::jsonb, 'loaded', %s, %s
                )
                returning source_release_id
                """,
                (
                    dataset_id,
                    year,
                    f"fixture-{token}-{year}",
                    date(2026, 9, 2),
                    datetime(2026, 9, 2, ordinal, tzinfo=UTC),
                    f"research/data-manifests/fixture-{token}-{year}.json",
                    f"{ordinal}{token}".encode().hex()[:64].ljust(64, "0"),
                ),
            ).fetchone()[0]
            releases[year] = release_id

        rows = [
            (2023, "2023-npi-a", "1111111111", None, None, "Consulting Fee", "100.00"),
            (2023, "2023-hospital-a", None, None, "HOSPITAL-A", "Consulting Fee", "200.00"),
            (2023, "2023-hospital-b", None, None, "HOSPITAL-A", "Consulting Fee", "25.00"),
            (2023, "2023-record", None, None, None, "Consulting Fee", "300.00"),
            (2024, "2024-npi-a", "1111111111", None, None, "Consulting Fee", "150.00"),
            (2024, "2024-npi-b", "2222222222", None, None, "Consulting Fee", "250.00"),
            (2024, "2024-hospital", None, None, "HOSPITAL-A", "Consulting Fee", "220.00"),
            (2024, "2024-record", None, None, None, "Consulting Fee", "350.00"),
            (2025, "2025-npi-a", "1111111111", None, None, "Food and Beverage", "50.00"),
        ]
        for year, record_id, npi, profile_id, hospital_id, nature, amount in rows:
            recipient_type = (
                "Covered Recipient Teaching Hospital"
                if hospital_id is not None
                else "Covered Recipient Physician"
            )
            hospital_name = "Example Teaching Hospital" if hospital_id is not None else None
            connection.execute(
                """
                insert into relationships.open_payments_general (
                    source_release_id, program_year, record_id, covered_recipient_type,
                    covered_recipient_profile_id, covered_recipient_npi, teaching_hospital_id,
                    teaching_hospital_name, paying_entity_id, paying_entity_name,
                    total_amount_usd, payment_date, number_of_payments, nature_of_payment
                )
                values (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, 'Test Paying Entity', %s, %s, 1, %s
                )
                """,
                (
                    releases[year],
                    year,
                    record_id,
                    recipient_type,
                    profile_id,
                    npi,
                    hospital_id,
                    hospital_name,
                    ORGANIZATION_ID,
                    amount,
                    date(year, 1, 1),
                    nature,
                ),
            )

        yield connection, releases
    finally:
        connection.rollback()
        connection.close()


def _query(
    connection: psycopg.Connection,
    filename: str,
    parameters: dict[str, str],
) -> list[dict[str, object]]:
    sql = (QUERY_DIR / filename).read_text(encoding="utf-8")
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(sql, parameters)
        return list(cursor.fetchall())


@pytest.mark.parametrize(
    ("filename", "additional_parameters"),
    [
        ("organization_annual_summary.sql", {}),
        ("organization_recipient_panel.sql", {}),
        (
            "organization_roster_transitions.sql",
            {"payment_nature": "Consulting Fee", "identifier_scope": "all"},
        ),
    ],
)
def test_every_analysis_rejects_missing_source_release_year(
    open_payments_fixture: tuple[psycopg.Connection, dict[int, int]],
    filename: str,
    additional_parameters: dict[str, str],
) -> None:
    connection, releases = open_payments_fixture
    parameters = {
        "source_release_ids": f"{{{releases[2023]}}}",
        "organization_id": ORGANIZATION_ID,
        "from_year": "2023",
        "to_year": "2024",
        **additional_parameters,
    }

    with pytest.raises(
        psycopg.errors.InvalidTextRepresentation,
        match="Open Payments source-release coverage failed",
    ):
        _query(connection, filename, parameters)


def test_annual_and_panel_preserve_hospital_and_record_only_identity(
    open_payments_fixture: tuple[psycopg.Connection, dict[int, int]],
) -> None:
    connection, releases = open_payments_fixture
    parameters = {
        "source_release_ids": f"{{{releases[2023]}}}",
        "organization_id": ORGANIZATION_ID,
        "from_year": "2023",
        "to_year": "2023",
    }

    annual = _query(connection, "organization_annual_summary.sql", parameters)[0]
    panel = _query(connection, "organization_recipient_panel.sql", parameters)

    assert annual["distinct_recipient_identifier_keys"] == 3
    assert annual["teaching_hospital_identity_recipient_count"] == 1
    assert annual["teaching_hospital_reported_transfer_amount_usd"] == 225
    assert annual["record_only_recipient_count"] == 1
    assert annual["record_only_reported_transfer_amount_usd"] == 300
    hospital = next(row for row in panel if row["identifier_quality"] == "teaching-hospital-id")
    assert hospital["recipient_identifier_key"] == "hospital:HOSPITAL-A"
    assert hospital["reported_records"] == 2
    assert hospital["reported_transfer_amount_usd"] == 225


def test_structurally_absent_category_amounts_are_zero(
    open_payments_fixture: tuple[psycopg.Connection, dict[int, int]],
) -> None:
    connection, releases = open_payments_fixture
    parameters = {
        "source_release_ids": f"{{{releases[2025]}}}",
        "organization_id": ORGANIZATION_ID,
        "from_year": "2025",
        "to_year": "2025",
    }

    annual = _query(connection, "organization_annual_summary.sql", parameters)[0]
    panel = _query(connection, "organization_recipient_panel.sql", parameters)[0]

    assert annual["consulting_fee_records"] == 0
    assert annual["consulting_fee_reported_transfer_amount_usd"] == 0
    assert annual["third_party_reported_transfer_amount_usd"] == 0
    assert panel["consulting_fee_reported_transfer_amount_usd"] == 0
    assert panel["third_party_reported_transfer_amount_usd"] == 0


def test_identifier_scope_recomputes_roster_metrics_before_windows(
    open_payments_fixture: tuple[psycopg.Connection, dict[int, int]],
) -> None:
    connection, releases = open_payments_fixture
    base_parameters = {
        "source_release_ids": f"{{{releases[2023]},{releases[2024]}}}",
        "organization_id": ORGANIZATION_ID,
        "from_year": "2023",
        "to_year": "2024",
        "payment_nature": "Consulting Fee",
    }

    all_rows = _query(
        connection,
        "organization_roster_transitions.sql",
        {**base_parameters, "identifier_scope": "all"},
    )
    npi_rows = _query(
        connection,
        "organization_roster_transitions.sql",
        {**base_parameters, "identifier_scope": "npi-only"},
    )

    assert {
        (
            row["previous_roster_size"],
            row["current_roster_size"],
            row["retained_recipient_count"],
            row["added_recipient_count"],
            row["exited_recipient_count"],
            row["roster_jaccard_overlap_pct"],
        )
        for row in all_rows
    } == {(3, 4, 2, 2, 1, 40)}
    assert {
        (
            row["previous_roster_size"],
            row["current_roster_size"],
            row["retained_recipient_count"],
            row["added_recipient_count"],
            row["exited_recipient_count"],
            row["roster_jaccard_overlap_pct"],
        )
        for row in npi_rows
    } == {(1, 2, 1, 1, 0, 50)}
    assert all(row["identifier_quality"] == "npi" for row in npi_rows)
