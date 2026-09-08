from __future__ import annotations

import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from drlf.analysis.dmepos_shortlist import (
    SCAN_COLUMNS,
    SCREEN_ALGORITHM,
    SCREEN_ALGORITHM_VERSION,
)
from drlf.config import get_settings
from drlf.database import apply_migrations

QUERY_PATH = Path("sql/analysis/dmepos/supplier_summary_screen.sql")
MIGRATIONS = Path("sql/migrations")
PINNED_VERSION_IDS = {
    2022: "471f9c0d-12c4-4601-a547-559dcfa2d0e2",
    2023: "7bb52a09-9eba-43f9-b7ec-57f65fbbc86c",
    2024: "4c6cfc68-a149-4bfe-b934-db2b92d0f360",
}


def _query() -> str:
    return QUERY_PATH.read_text(encoding="utf-8")


def test_supplier_screen_pins_source_year_and_resource_contract() -> None:
    query = _query()

    assert "%(source_release_ids)s::bigint[]" in query
    assert "%(data_years)s::smallint[]" in query
    assert "array[2022, 2023, 2024]::smallint[]" in query
    assert "latest_year = 2024" in query
    assert "minimum_panel_beneficiaries = 100" in query
    assert "minimum_peer_count = 100" in query
    assert "minimum_annual_payment = 100000" in query
    assert "minimum_benchmark_exposure = 100000" in query
    assert "tail_percentile_threshold = 0.975" in query
    assert "full_percentile_threshold = 0.99" in query
    assert "minimum_robust_z = 3.5" in query
    assert "minimum_ratio = 2" in query
    assert "medicare-durable-medical-equipment-devices-supplies-by-supplier" in query
    assert "Centers for Medicare & Medicaid Services" in query
    assert "471f9c0d-12c4-4601-a547-559dcfa2d0e2" in query
    assert "7bb52a09-9eba-43f9-b7ec-57f65fbbc86c" in query
    assert "4c6cfc68-a149-4bfe-b934-db2b92d0f360" in query
    assert "join claims.dmepos_supplier as supplier" in query
    assert "select supplier.*" not in query
    assert "duplicate year/NPI grain" in query
    assert "from validated_releases as release" in query
    assert "where not exists (" in query
    assert ">= 'Infinity'::numeric" in query
    assert f"'{SCREEN_ALGORITHM}'::text as screen_algorithm" in query
    assert f"'{SCREEN_ALGORITHM_VERSION}'::text as screen_algorithm_version" in query


def test_supplier_screen_uses_prespecified_peer_strata_and_broad_categories() -> None:
    query = _query()

    for peer_key in (
        "data_year",
        "entity_code",
        "supplier_specialty_description",
        "dominant_broad_category",
        "beneficiary_volume_band",
    ):
        assert peer_key in query
    for band in ("100-249", "250-499", "500-999", "1000-4999", "5000+"):
        assert f"'{band}'" in query
    assert ">= 0.80 then 'DME'" in query
    assert ">= 0.80 then 'POS'" in query
    assert ">= 0.80 then 'Drug'" in query
    assert "else 'mixed'" in query
    assert "then 'unclassified'" in query
    assert "+ coalesce(supplier.pos_medicare_payment_amount, 0)" in query
    assert "+ coalesce(supplier.drug_medicare_payment_amount, 0)" in query
    assert "then 'none'" in query
    assert "as dme_payment_share" in query
    assert "as pos_payment_share" in query
    assert "as drug_payment_share" in query
    assert "nullif(btrim(supplier.entity_code), '') is not null" in query
    assert "nullif(btrim(supplier.supplier_specialty_description), '') is not null" in query
    assert "nullif(btrim(supplier.supplier_specialty_source), '') is not null" in query
    assert "[unknown]" not in query


def test_supplier_screen_publishes_robust_statistics_for_all_four_lanes() -> None:
    query = _query()

    for metric in (
        "standardized_payment_per_beneficiary",
        "raw_payment_per_beneficiary",
        "claims_per_beneficiary",
        "services_per_beneficiary",
    ):
        assert metric in query
    assert "percentile_cont(0.25)" in query
    assert "percentile_cont(0.50)" in query
    assert "percentile_cont(0.75)" in query
    assert "cume_dist() over" in query
    assert "as median_ratio" in query
    assert "as median_absolute_delta" in query
    assert "as robust_z" in query
    assert "0.67448975::numeric" in query
    assert "1.3489795::numeric" in query
    for suffix in (
        "peer_median",
        "peer_q75",
        "peer_iqr",
        "peer_mad",
        "empirical_percentile",
        "median_ratio",
        "median_absolute_delta",
        "robust_z",
        "scale_source",
    ):
        assert f"standardized_payment_{suffix}" in query
        assert f"raw_payment_{suffix}" in query
        assert f"claims_{suffix}" in query
        assert f"services_{suffix}" in query


def test_supplier_screen_requires_materiality_payment_and_utilization_evidence() -> None:
    query = _query()

    assert "as raw_payment_benchmark_exposure_above_q75" in query
    assert "as standardized_payment_benchmark_exposure_above_q75" in query
    assert "as standardized_payment_full_outlier_flag" in query
    assert "as claims_full_outlier_flag" in query
    assert "as services_full_outlier_flag" in query
    assert "annual.standardized_payment_full_outlier_flag" in query
    assert "annual.claims_full_outlier_flag" in query
    assert "or annual.services_full_outlier_flag" in query
    assert "as candidate_tail_year" in query
    assert "as candidate_full_year" in query
    assert "Payment metrics alone cannot qualify" in query
    assert "not a causal counterfactual or recoverable amount" in query


def test_supplier_screen_temporal_route_is_three_year_and_latest_aware() -> None:
    query = _query()

    assert "count(*) filter" in query
    assert "where annual.candidate_tail_year" in query
    assert "where annual.candidate_full_year" in query
    assert "temporal.comparable_years = cardinality(settings.data_years)" in query
    assert "temporal.candidate_tail_years >= 2" in query
    assert "temporal.candidate_full_years >= 1" in query
    assert "temporal.maximum_observed_year = settings.latest_year" in query
    assert "then 'high-dollar-persistent'" in query
    assert "then 'high-dollar-emerging'" in query
    assert "then 'high-spend-context'" in query


def test_supplier_screen_emits_unrounded_machine_values_used_by_flags() -> None:
    query = _query()

    assert "round(" not in query.casefold()
    assert "100 * classified.standardized_payment_empirical_percentile::numeric" in query
    assert "classified.raw_payment_benchmark_exposure_above_q75," in query
    assert "classified.standardized_payment_benchmark_exposure_above_q75," in query


def test_supplier_screen_full_output_excludes_direct_identity_fields() -> None:
    query = _query()

    for identity_column in (
        "supplier_last_org_name",
        "supplier_first_name",
        "supplier_middle_initial",
        "supplier_credentials",
        "supplier_address_line1",
        "supplier_address_line2",
        "supplier_city",
        "supplier_state",
        "supplier_zip5",
    ):
        assert identity_column not in query
    assert "supplier_npi" in query


@pytest.mark.skipif(
    os.environ.get("DRLF_RUN_DB_TESTS") != "1",
    reason="set DRLF_RUN_DB_TESTS=1 to run local PostgreSQL fixture tests",
)
def test_supplier_screen_executes_and_rejects_weakened_parameters() -> None:
    database_url = os.environ.get(
        "DRLF_TEST_DATABASE_URL",
        get_settings().database_url.get_secret_value(),
    )
    apply_migrations(database_url, MIGRATIONS)
    token = uuid4().hex
    npi = str(8_000_000_000 + int(token[:8], 16) % 1_000_000_000)
    now = datetime.now(UTC)
    query = _query()

    with psycopg.connect(database_url) as connection:
        dataset_id = connection.execute(
            """
            insert into metadata.source_dataset (source, slug, name)
            values (%s, %s, %s)
            on conflict (source, slug) do update set name = excluded.name
            returning dataset_id
            """,
            (
                "Centers for Medicare & Medicaid Services",
                "medicare-durable-medical-equipment-devices-supplies-by-supplier",
                "DMEPOS SQL fixture",
            ),
        ).fetchone()[0]
        release_ids: list[int] = []
        for year in (2022, 2023, 2024):
            manifest_hash = sha256(f"{token}-{year}".encode()).hexdigest()
            release_id = connection.execute(
                """
                insert into metadata.source_release (
                    dataset_id, data_year, version_id, accessed_at, observed_at,
                    population, status, manifest_path, manifest_sha256
                )
                values (%s, %s, %s, %s, %s, 'Synthetic supplier cohort', 'loaded', %s, %s)
                returning source_release_id
                """,
                (
                    dataset_id,
                    year,
                    PINNED_VERSION_IDS[year],
                    now.date(),
                    now,
                    f"research/data-manifests/{token}-{year}.json",
                    manifest_hash,
                ),
            ).fetchone()[0]
            release_ids.append(release_id)
            connection.execute(
                """
                insert into claims.dmepos_supplier (
                    source_release_id, data_year, supplier_npi, entity_code,
                    supplier_specialty_description, supplier_specialty_source,
                    total_hcpcs_codes, total_beneficiaries, total_claims, total_services,
                    total_submitted_charge, total_medicare_allowed_amount,
                    total_medicare_payment_amount,
                    total_medicare_standardized_payment_amount,
                    dme_medicare_payment_amount,
                    dme_medicare_standardized_payment_amount,
                    pos_medicare_payment_amount,
                    pos_medicare_standardized_payment_amount,
                    drug_medicare_payment_amount,
                    drug_medicare_standardized_payment_amount
                )
                values (
                    %s, %s, %s, 'O', 'Synthetic DME supplier', 'fixture',
                    20, 1000, 1500, 1600, 500000, 350000, 300000, 290000,
                    270000, 261000, 15000, 14500, 15000, 14500
                )
                """,
                (release_id, year, npi),
            )
            if year == 2024:
                connection.execute(
                    """
                    insert into claims.dmepos_supplier (
                        source_release_id, data_year, supplier_npi, entity_code,
                        supplier_specialty_description, supplier_specialty_source,
                        total_hcpcs_codes, total_beneficiaries, total_claims, total_services,
                        total_submitted_charge, total_medicare_allowed_amount,
                        total_medicare_payment_amount,
                        total_medicare_standardized_payment_amount
                    )
                    values (
                        %s, %s, %s, 'O', 'Suppressed panel fixture', 'fixture',
                        1, null, 20, 20, 2000, 1500, 1000, 950
                    )
                    """,
                    (release_id, year, str(int(npi) + 1)),
                )
                connection.execute(
                    """
                    insert into claims.dmepos_supplier (
                        source_release_id, data_year, supplier_npi, entity_code,
                        supplier_specialty_description, supplier_specialty_source,
                        total_hcpcs_codes, total_beneficiaries, total_claims, total_services,
                        total_submitted_charge, total_medicare_allowed_amount,
                        total_medicare_payment_amount,
                        total_medicare_standardized_payment_amount
                    )
                    values (
                        %s, %s, %s, 'O', '   ', 'fixture',
                        1, 100, 20, 20, 2000, 1500, 1000, 950
                    )
                    """,
                    (release_id, year, str(int(npi) + 2)),
                )

        parameters = {
            "source_release_ids": release_ids,
            "data_years": [2022, 2023, 2024],
            "min_panel_beneficiaries": 100,
            "min_peer_count": 100,
            "min_annual_payment": "100000",
            "min_benchmark_exposure": "100000",
            "tail_percentile": "0.975",
            "full_percentile": "0.99",
            "min_robust_z": "3.5",
            "min_ratio": "2",
            "latest_year": 2024,
        }
        result = connection.execute(query, parameters)
        assert len(result.fetchall()) == 3
        assert tuple(column.name for column in result.description) == SCAN_COLUMNS

        with pytest.raises(psycopg.errors.CheckViolation):
            with connection.transaction():
                connection.execute(
                    """
                    insert into claims.dmepos_supplier (
                        source_release_id, data_year, supplier_npi, entity_code,
                        supplier_specialty_description, supplier_specialty_source,
                        total_hcpcs_codes, total_beneficiaries, total_claims, total_services,
                        total_submitted_charge, total_medicare_allowed_amount,
                        total_medicare_payment_amount,
                        total_medicare_standardized_payment_amount
                    )
                    values (
                        %s, 2024, %s, 'O', 'Nonfinite fixture', 'fixture',
                        1, 100, 100, 'Infinity'::numeric, 100, 100, 100, 100
                    )
                    """,
                    (release_ids[2], str(int(npi) + 3)),
                )

        with pytest.raises(psycopg.errors.DivisionByZero):
            with connection.transaction():
                connection.execute(
                    "delete from claims.dmepos_supplier where source_release_id = %s",
                    (release_ids[1],),
                )
                connection.execute(query, parameters).fetchall()

        weakened = {**parameters, "min_annual_payment": "99999"}
        with pytest.raises(psycopg.errors.DivisionByZero):
            with connection.transaction():
                connection.execute(query, weakened).fetchall()
        connection.rollback()
