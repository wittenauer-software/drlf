from __future__ import annotations

import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from drlf.analysis.dmepos_identity import (
    postgres_array,
)
from drlf.analysis.dmepos_shortlist import IDENTITY_COLUMNS
from drlf.config import get_settings
from drlf.database import apply_migrations

QUERY_PATH = Path("sql/analysis/dmepos/frozen_candidate_identities.sql")
MIGRATIONS = Path("sql/migrations")


def test_postgres_array_accepts_only_validated_numeric_identifiers() -> None:
    assert postgres_array([]) == "{}"
    assert postgres_array(["1111111111", "2222222222"]) == "{1111111111,2222222222}"
    assert postgres_array([41, 42]) == "{41,42}"
    with pytest.raises(ValueError, match="nonnumeric"):
        postgres_array(["1111111111,2222222222"])


def test_identity_query_is_exact_scope_validated_and_has_exact_output_contract() -> None:
    query = QUERY_PATH.read_text(encoding="utf-8")

    assert "%(candidate_npis)s::text[]" in query
    assert "%(candidate_source_release_ids)s::bigint[]" in query
    assert "%(source_release_ids)s::bigint[]" in query
    assert "maximum_candidate_rows = 10000" in query
    assert "expected_data_year = 2024" in query
    assert "4c6cfc68-a149-4bfe-b934-db2b92d0f360" in query
    assert "status is distinct from 'loaded'" in query
    assert "Centers for Medicare & Medicaid Services" in query
    assert "medicare-durable-medical-equipment-devices-supplies-by-supplier" in query
    assert "duplicate_candidates" in query
    assert "= (select count(*) from requested_candidates)" in query
    assert "supplier.source_release_id = candidate.source_release_id" in query
    assert "supplier.supplier_npi = candidate.supplier_npi" in query
    assert "supplier.data_year = settings.expected_data_year" in query
    assert "validation_anchor as materialized" in query
    assert "validated_output as materialized" in query
    assert "left join resolved_candidates as resolved on true" in query
    assert "supplier.*" not in query

    final_select = query.rsplit("select", maxsplit=1)[1].split("from validated_output", maxsplit=1)[
        0
    ]
    output_columns = {
        line.strip().removeprefix("output.").rstrip(",")
        for line in final_select.splitlines()
        if line.strip()
    }
    assert output_columns == IDENTITY_COLUMNS


@pytest.mark.skipif(
    os.environ.get("DRLF_RUN_DB_TESTS") != "1",
    reason="set DRLF_RUN_DB_TESTS=1 to run local PostgreSQL fixture tests",
)
def test_identity_query_executes_for_exact_pair_and_fails_on_unresolved_pair() -> None:
    database_url = os.environ.get(
        "DRLF_TEST_DATABASE_URL",
        get_settings().database_url.get_secret_value(),
    )
    apply_migrations(database_url, MIGRATIONS)
    token = uuid4().hex
    npi = str(7_000_000_000 + int(token[:8], 16) % 1_000_000_000)
    query = QUERY_PATH.read_text(encoding="utf-8")
    now = datetime.now(UTC)

    with psycopg.connect(database_url) as connection:
        dataset_id = connection.execute(
            """
            insert into metadata.source_dataset (source, slug, name)
            values (
                'Centers for Medicare & Medicaid Services',
                'medicare-durable-medical-equipment-devices-supplies-by-supplier',
                'DMEPOS identity fixture'
            )
            on conflict (source, slug) do update set name = excluded.name
            returning dataset_id
            """
        ).fetchone()[0]
        release_id = connection.execute(
            """
            insert into metadata.source_release (
                dataset_id, data_year, version_id, accessed_at, observed_at,
                population, status, manifest_path, manifest_sha256
            )
            values (
                %s, 2024, '4c6cfc68-a149-4bfe-b934-db2b92d0f360', %s, %s,
                'Synthetic identity fixture', 'loaded', %s, %s
            )
            returning source_release_id
            """,
            (
                dataset_id,
                now.date(),
                now,
                f"research/data-manifests/{token}.json",
                sha256(token.encode()).hexdigest(),
            ),
        ).fetchone()[0]
        connection.execute(
            """
            insert into claims.dmepos_supplier (
                source_release_id, data_year, supplier_npi,
                supplier_last_org_name, supplier_first_name, entity_code,
                supplier_city, supplier_state,
                supplier_specialty_description, supplier_specialty_source,
                total_hcpcs_codes, total_beneficiaries, total_claims, total_services,
                total_submitted_charge, total_medicare_allowed_amount,
                total_medicare_payment_amount,
                total_medicare_standardized_payment_amount
            )
            values (
                %s, 2024, %s, 'Fixture Supply', '', 'O', 'Austin', 'TX',
                'Medical Supply Company', 'fixture', 1, 100, 100, 100,
                1000, 900, 800, 790
            )
            """,
            (release_id, npi),
        )
        parameters = {
            "candidate_npis": postgres_array([npi]),
            "candidate_source_release_ids": postgres_array([release_id]),
            "expected_data_year": "2024",
            "frozen_shortlist_sha256": "a" * 64,
            "maximum_candidate_rows": "10000",
            "source_release_ids": postgres_array([release_id]),
        }

        result = connection.execute(query, parameters)
        assert tuple(column.name for column in result.description) == (
            "data_year",
            "supplier_npi",
            "source_release_id",
            "supplier_last_org_name",
            "supplier_first_name",
            "supplier_city",
            "supplier_state",
        )
        assert result.fetchone() == (
            2024,
            npi,
            release_id,
            "Fixture Supply",
            "",
            "Austin",
            "TX",
        )

        unresolved = {
            **parameters,
            "candidate_npis": postgres_array([str(int(npi) + 1)]),
        }
        with pytest.raises(psycopg.errors.DivisionByZero):
            with connection.transaction():
                connection.execute(query, unresolved).fetchall()

        empty = {
            **parameters,
            "candidate_npis": "{}",
            "candidate_source_release_ids": "{}",
            "source_release_ids": "{}",
        }
        assert connection.execute(query, empty).fetchall() == []
        connection.rollback()
