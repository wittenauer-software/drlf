from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from drlf.database import apply_migrations

PEER_QUERY_PATH = Path("sql/analysis/dmepos/supplier_service_code_peer_review.sql")
MARKET_QUERY_PATH = Path("sql/analysis/dmepos/supplier_service_code_market_context.sql")
STATE_MARKET_QUERY_PATH = Path(
    "sql/analysis/dmepos/supplier_service_code_market_context_by_state.sql"
)
QUERY_PATHS = (PEER_QUERY_PATH, MARKET_QUERY_PATH, STATE_MARKET_QUERY_PATH)
MIGRATIONS = Path("sql/migrations")


def _query(path: Path) -> str:
    return path.read_text(encoding="utf-8").lower()


@pytest.mark.parametrize("path", QUERY_PATHS)
def test_service_analysis_binds_bounded_reusable_parameters(path: Path) -> None:
    query = _query(path)

    for parameter in (
        "source_release_ids",
        "data_years",
        "candidate_npis",
        "hcpcs_codes",
        "min_peer_count",
    ):
        assert f"%({parameter})s" in query
    assert "cardinality(settings.data_years) between 1 and 3" in query
    assert "cardinality(settings.candidate_npis) between 1 and 100" in query
    assert "cardinality(settings.hcpcs_codes) between 1 and 100" in query
    assert "settings.minimum_peer_count between 1 and 100000" in query
    assert "supplier_npi !~ '^[0-9]{10}$'" in query
    assert "hcpcs_code !~ '^[a-z0-9]{5}$'" in query


@pytest.mark.parametrize("path", QUERY_PATHS)
def test_service_analysis_pins_and_audits_both_release_types(path: Path) -> None:
    query = _query(path)

    for version_id in (
        "471f9c0d-12c4-4601-a547-559dcfa2d0e2",
        "7bb52a09-9eba-43f9-b7ec-57f65fbbc86c",
        "4c6cfc68-a149-4bfe-b934-db2b92d0f360",
        "dad703df-2e52-4ac6-a26f-51ecbf463d77",
        "44235fd3-6cf9-487f-928d-12998cd84071",
        "1dc6718a-e44b-403a-a735-ce01a6233822",
    ):
        assert version_id in query
    assert "centers for medicare & medicaid services" in query
    assert "medicare-durable-medical-equipment-devices-supplies-by-supplier'" in query
    assert "medicare-durable-medical-equipment-devices-supplies-by-supplier-and-service'" in query
    assert "release.status = 'loaded'" in query
    assert "release.manifest_path is not null" in query
    assert "release.manifest_sha256 ~ '^[0-9a-f]{64}$'" in query
    assert "'[\"suplr_npi\"]'::jsonb" in query
    assert '\'["suplr_npi","hcpcs_cd","suplr_rentl_ind"]\'::jsonb' in query
    assert "supplier_release_count = 1" in query
    assert "supplier_service_release_count = 1" in query
    assert "2 * (select count(*) from requested_years)" in query


@pytest.mark.parametrize("path", QUERY_PATHS)
def test_service_analysis_requires_exact_complete_filter_shape(path: Path) -> None:
    query = _query(path)

    assert "expected_service_filter" in query
    assert "'hcpcs_cd', jsonb_agg(target.hcpcs_code order by target.hcpcs_code)" in query
    assert "release.supplier_retrieval_filters <> '{}'::jsonb" in query
    assert "<> expected.retrieval_filters" in query
    assert "one-key requested hcpcs code" in query
    assert "<@" not in query


@pytest.mark.parametrize("path", QUERY_PATHS)
def test_service_analysis_fails_closed_on_load_and_grain_integrity(path: Path) -> None:
    query = _query(path)

    assert "metadata.ingestion_run as run" in query
    assert "run.status = 'succeeded'" in query
    assert "run.loaded_rows = (" in query
    assert "duplicate_service_cells" in query
    assert "having count(*) > 1" in query
    assert "validation_anchor as materialized" in query
    assert "left join" in query
    # Keep invalid-cast error branches dependent on runtime state so PostgreSQL
    # cannot constant-fold them while planning a valid query.
    assert query.count("coalesce(valid::text, 'null')") >= 2


def test_code_peer_review_uses_exact_cohort_excluded_known_beneficiary_peers() -> None:
    query = _query(PEER_QUERY_PATH)

    assert "join target_codes as target" in query
    assert "where service.candidate_cohort_flag" in query
    assert "and output.supplier_npi is not null" in query
    for peer_key in (
        "data_year",
        "hcpcs_code",
        "supplier_rental_indicator",
        "entity_code",
    ):
        assert peer_key in query
    assert "where not service.candidate_cohort_flag" in query
    assert "and service.known_beneficiary_flag" in query
    for metric in (
        "services_per_beneficiary",
        "medicare_payment_per_beneficiary",
        "standardized_payment_per_beneficiary",
    ):
        assert metric in query
    assert "percentile_cont(0.25)" in query
    assert "percentile_cont(0.50)" in query
    assert "percentile_cont(0.75)" in query
    assert "as peer_iqr" in query
    assert "as peer_mad" in query
    assert "as cohort_excluded_high_rank" in query
    assert "as cohort_excluded_empirical_percentile" in query


def test_code_peer_review_encodes_robust_exact_cell_qualification() -> None:
    query = _query(PEER_QUERY_PATH)

    assert "service.total_claims::numeric" in query
    for metric in (
        "claims_per_beneficiary",
        "services_per_beneficiary",
        "medicare_payment_per_beneficiary",
        "standardized_payment_per_beneficiary",
    ):
        assert metric in query
    for statistic in (
        "cohort_excluded_median_ratio",
        "cohort_excluded_median_absolute_delta",
        "cohort_excluded_robust_z",
        "cohort_excluded_scale_source",
    ):
        assert statistic in query
    assert "0.67448975::numeric" in query
    assert "1.3489795::numeric" in query
    assert "0.000000000001::numeric as robust_scale_relative_tolerance" in query
    assert "* greatest(1::numeric, abs(ranked.peer_median))" in query
    assert "else 'degenerate'" in query

    for constant in (
        "100000::numeric as minimum_observed_raw_payment",
        "100000::numeric as minimum_q75_benchmark_exposure",
        "0.975::numeric as tail_percentile_threshold",
        "0.99::numeric as full_percentile_threshold",
        "3.5::numeric as minimum_robust_z",
        "2::numeric as minimum_median_ratio",
        "as temporal_focus_year",
    ):
        assert constant in query
    assert "'dmepos-supplier-service-exact-cell'::text as exact_cell_algorithm" in query
    assert "'1'::text as exact_cell_algorithm_version" in query

    for lane in ("claims", "services", "raw_payment", "standardized_payment"):
        assert f"as {lane}_tail_flag" in query
        assert f"as {lane}_full_outlier_flag" in query
    assert "as claims_robust_tail_flag" in query
    assert "as services_robust_tail_flag" in query
    assert "as utilization_robust_tail_flag" in query
    assert "as utilization_full_outlier_flag" in query
    for qualification in (
        "observed_raw_payment_magnitude_flag",
        "raw_payment_q75_exposure_magnitude_flag",
        "standardized_q75_exposure_magnitude_flag",
        "exact_cell_magnitude_gates_pass",
        "annual_exact_cell_tail_qualified",
        "annual_exact_cell_full_qualified",
        "temporal_exact_cell_tail_qualified",
        "temporal_exact_cell_full_qualified",
    ):
        assert qualification in query

    assert "partition by\n            annual.supplier_npi," in query
    assert "annual.hcpcs_code," in query
    assert "annual.supplier_rental_indicator," in query
    assert "annual.entity_code" in query
    assert "annual.data_year = settings.temporal_focus_year" in query
    assert "annual.data_year < settings.temporal_focus_year" in query
    assert "and flagged.utilization_robust_tail_flag" in query
    assert "and flagged.utilization_full_outlier_flag" in query
    assert "and flagged.standardized_payment_tail_flag" not in query
    assert "and flagged.standardized_payment_full_outlier_flag" not in query
    assert "sum(total_claims)" not in query
    assert "sum(total_beneficiaries)" not in query


def test_code_peer_review_output_aliases_fit_postgresql_limit() -> None:
    query = _query(PEER_QUERY_PATH)
    aliases = {token.split()[1] for token in re.findall(r"\bas\s+[a-z_][a-z0-9_]*", query)}

    assert aliases
    assert max(len(alias.encode("utf-8")) for alias in aliases) <= 63


def test_code_peer_review_guards_scoring_and_preserves_suppression() -> None:
    query = _query(PEER_QUERY_PATH)

    assert "then 'unknown-beneficiary-descriptive'" in query
    assert "then 'insufficient-peer-descriptive'" in query
    assert "else 'scoreable'" in query
    assert "coalesce(peer.cohort_excluded_peer_count, 0)" in query
    assert ">= settings.minimum_peer_count" in query
    assert "as raw_payment_benchmark_exposure_above_q75" in query
    assert "as standardized_payment_benchmark_exposure_above_q75" in query
    assert "greatest(" in query
    assert "sum(total_beneficiaries)" not in query
    assert "beneficiary and claim counts must not be summed across cells" in query


def test_code_peer_review_adds_cohort_excluded_supplier_state_sensitivity() -> None:
    query = _query(PEER_QUERY_PATH)

    assert "'supplier-address-state'::text" in query
    assert "upper(coalesce(service.supplier_state, ''))" in query
    assert "peer.peer_scope = candidate.peer_scope" in query
    assert "peer.peer_geography = candidate.peer_geography" in query
    assert "state_cohort_excluded_peer_count" in query
    assert "state_peer_evaluation_status" in query
    assert "insufficient-state-peer-descriptive" in query
    assert "state_services_cohort_excluded_high_rank" in query
    assert "state_services_cohort_excluded_empirical_percentile" in query
    assert "state_raw_payment_benchmark_exposure_above_q75" in query
    assert "state_standardized_payment_benchmark_exposure_above_q75" in query
    assert "supplier address is not beneficiary or furnishing geography" in query


def test_code_peer_review_reconciles_only_selected_visible_codes() -> None:
    query = _query(PEER_QUERY_PATH)

    assert "candidate_selected_portfolios" in query
    assert "summary_total_hcpcs_codes" in query
    assert "selected_visible_medicare_payment_coverage" in query
    assert "unrepresented_medicare_payment_amount" in query
    assert "selected_top_cell_payment_share" in query
    assert "selected_top_three_cell_payment_share" in query
    assert "selected_visible_payment_hhi" in query
    assert "only the requested retained hcpcs universe" in query
    assert "candidate_cell_visible_national_payment_share" in query
    assert "candidate_cell_visible_national_standardized_payment_share" in query
    assert "source_address_florida_visible_payment_share" in query
    assert "source_address_florida_visible_standardized_payment_share" in query
    assert "candidate_cohort_visible_payment_share" in query
    assert "candidate_cohort_visible_standardized_payment_share" in query
    assert "fraud_score" not in query


def test_code_market_context_has_requested_visible_aggregate_contract() -> None:
    query = _query(MARKET_QUERY_PATH)

    for field in (
        "total_visible_cells",
        "known_beneficiary_cells",
        "cohort_excluded_known_beneficiary_peer_cells",
        "suppressed_or_unknown_beneficiary_cells",
        "total_visible_services",
        "reconstructed_visible_medicare_payment_amount",
        "reconstructed_visible_standardized_payment_amount",
        "source_address_florida_visible_payment_share",
        "candidate_cohort_visible_payment_share",
        "top_supplier_visible_payment_share",
        "visible_payment_hhi",
        "known_beneficiary_services_median",
        "known_beneficiary_services_q75",
        "known_beneficiary_medicare_payment_median",
        "known_beneficiary_medicare_payment_q75",
        "known_beneficiary_standardized_payment_median",
        "known_beneficiary_standardized_payment_q75",
    ):
        assert field in query
    assert "and not candidate_cohort_flag" in query
    assert "when market.cohort_excluded_known_beneficiary_peer_cells" in query
    assert "insufficient-cohort-excluded-peer-cells-descriptive" in query
    assert "group by" in query
    assert "data_year," in query
    assert "hcpcs_code," in query
    assert "supplier_rental_indicator" in query
    assert "sum(total_beneficiaries)" not in query
    assert "visible published supplier-service cells" in query
    assert "supplier state is a source-published address assertion" in query


def test_state_market_context_is_parameterized_and_exactly_stratified() -> None:
    query = _query(STATE_MARKET_QUERY_PATH)

    assert "%(source_address_states)s" in query
    assert "cardinality(settings.source_address_states) between 1 and 100" in query
    assert "source_address_state !~ '^[a-z]{2}$'" in query
    assert "upper(btrim(metrics.supplier_state))" in query
    assert "source_address_florida" not in query
    assert "upper(supplier_state) = 'fl'" not in query
    for exact_key in (
        "data_year",
        "hcpcs_code",
        "supplier_rental_indicator",
        "entity_code",
        "source_address_state",
    ):
        assert exact_key in query
    assert "national_cohort_excluded_known_beneficiary_peer_cells" in query
    assert "state_cohort_excluded_known_beneficiary_peer_cells" in query
    assert query.count("and not candidate_cohort_flag") >= 7
    assert "sum(total_beneficiaries)" not in query
    assert "sum(metrics.total_beneficiaries)" not in query
    assert "supplier-address-state sensitivity" in query
    assert "supplier state is a source-published address assertion" in query


@pytest.mark.skipif(
    os.environ.get("DRLF_RUN_DB_TESTS") != "1"
    or not os.environ.get("DRLF_TEST_DATABASE_URL"),
    reason=(
        "set DRLF_RUN_DB_TESTS=1 and DRLF_TEST_DATABASE_URL "
        "to run the disposable PostgreSQL fixture"
    ),
)
def test_code_peer_review_state_sensitivity_executes_without_peer_leakage() -> None:
    database_url = os.environ["DRLF_TEST_DATABASE_URL"]
    apply_migrations(database_url, MIGRATIONS)
    token = uuid4().hex
    now = datetime.now(UTC)
    query = PEER_QUERY_PATH.read_text(encoding="utf-8")
    state_market_query = STATE_MARKET_QUERY_PATH.read_text(encoding="utf-8")
    npi_base = 6_000_000_000 + int(token[:7], 16) % 100_000_000
    candidate_one, candidate_two, candidate_no_state, candidate_unknown_benes = (
        str(npi_base + offset) for offset in range(1, 5)
    )
    florida_peer_one, florida_peer_two, texas_peer_one, texas_peer_two = (
        str(npi_base + offset) for offset in range(101, 105)
    )
    candidates = [
        candidate_one,
        candidate_two,
        candidate_no_state,
        candidate_unknown_benes,
    ]

    with psycopg.connect(database_url) as connection:
        try:
            dataset_ids: dict[str, int] = {}
            for slug, name in (
                (
                    "medicare-durable-medical-equipment-devices-supplies-by-supplier",
                    "Synthetic DMEPOS supplier peer fixture",
                ),
                (
                    "medicare-durable-medical-equipment-devices-supplies-by-supplier-and-service",
                    "Synthetic DMEPOS supplier-service peer fixture",
                ),
            ):
                dataset_ids[slug] = connection.execute(
                    """
                    insert into metadata.source_dataset (source, slug, name)
                    values ('Centers for Medicare & Medicaid Services', %s, %s)
                    on conflict (source, slug) do update set name = excluded.name
                    returning dataset_id
                    """,
                    (slug, name),
                ).fetchone()[0]

            release_specs = (
                (
                    "supplier",
                    "medicare-durable-medical-equipment-devices-supplies-by-supplier",
                    "4c6cfc68-a149-4bfe-b934-db2b92d0f360",
                    '["Suplr_NPI"]',
                    "{}",
                ),
                (
                    "service",
                    "medicare-durable-medical-equipment-devices-supplies-by-supplier-and-service",
                    "1dc6718a-e44b-403a-a735-ce01a6233822",
                    '["Suplr_NPI","HCPCS_Cd","Suplr_Rentl_Ind"]',
                    '{"HCPCS_Cd":["A6010","A6021"]}',
                ),
            )
            release_ids: dict[str, int] = {}
            for label, slug, version_id, aggregation_keys, retrieval_filters in release_specs:
                release_ids[label] = connection.execute(
                    """
                    insert into metadata.source_release (
                        dataset_id, data_year, version_id, accessed_at, observed_at,
                        population, aggregation_keys, status, manifest_path,
                        manifest_sha256, retrieval_filters
                    )
                    values (
                        %s, 2024, %s, %s, %s, 'Synthetic peer-regression fixture',
                        %s::jsonb, 'loaded', %s, %s, %s::jsonb
                    )
                    returning source_release_id
                    """,
                    (
                        dataset_ids[slug],
                        version_id,
                        now.date(),
                        now,
                        aggregation_keys,
                        f"research/data-manifests/{token}-{label}.json",
                        sha256(f"{token}-{label}".encode()).hexdigest(),
                        retrieval_filters,
                    ),
                ).fetchone()[0]

            for candidate_npi in candidates:
                connection.execute(
                    """
                    insert into claims.dmepos_supplier (
                        source_release_id, data_year, supplier_npi, entity_code,
                        supplier_specialty_description, supplier_specialty_source,
                        total_hcpcs_codes, total_beneficiaries, total_claims,
                        total_services, total_submitted_charge,
                        total_medicare_allowed_amount, total_medicare_payment_amount,
                        total_medicare_standardized_payment_amount
                    )
                    values (
                        %s, 2024, %s, 'O', 'Synthetic DME supplier', 'fixture',
                        2, 100, 100, 500000, 500000, 500000, 500000, 500000
                    )
                    """,
                    (release_ids["supplier"], candidate_npi),
                )

            service_rows = (
                # Candidate two is deliberately larger than candidate one; both it and
                # every other candidate must be absent from every peer distribution.
                (candidate_one, "FL", "A6010", 100, 10_000, 2),
                (candidate_two, "FL", "A6010", 100, 200_000, 1),
                (candidate_no_state, None, "A6010", 100, 5_000, 1),
                (candidate_unknown_benes, "FL", "A6010", None, 5_000, 1),
                (florida_peer_one, "FL", "A6010", 100, 1_000, 1),
                (florida_peer_two, "FL", "A6010", 100, 2_000, 1),
                (texas_peer_one, "TX", "A6010", 100, 100_000, 1),
                # A6021 has enough national peers but only one Florida-address peer.
                (candidate_one, "FL", "A6021", 100, 10_000, 2),
                (florida_peer_one, "FL", "A6021", 100, 1_000, 1),
                (texas_peer_one, "TX", "A6021", 100, 2_000, 1),
                (texas_peer_two, "TX", "A6021", 100, 3_000, 1),
            )
            for supplier_npi, state, code, beneficiaries, services, payment in service_rows:
                connection.execute(
                    """
                    insert into claims.dmepos_supplier_service (
                        source_release_id, data_year, supplier_npi,
                        supplier_last_org_name, entity_code, supplier_city,
                        supplier_state, supplier_specialty_description,
                        supplier_specialty_source, rbcs_level, rbcs_id,
                        rbcs_description, hcpcs_code, hcpcs_description,
                        supplier_rental_indicator, total_beneficiaries, total_claims,
                        total_services, average_submitted_charge,
                        average_medicare_allowed_amount,
                        average_medicare_payment_amount,
                        average_medicare_standardized_amount
                    )
                    values (
                        %s, 2024, %s, 'Synthetic supplier', 'O', 'Fixture City',
                        %s, 'Medical Supply Company', 'fixture', 'DME', 'DME',
                        'Durable Medical Equipment', %s, 'Synthetic code', 'N',
                        %s, 100, %s, 3, 2.5, %s, %s
                    )
                    """,
                    (
                        release_ids["service"],
                        supplier_npi,
                        state,
                        code,
                        beneficiaries,
                        services,
                        payment,
                        payment,
                    ),
                )

            for release_id, loaded_rows in (
                (release_ids["supplier"], len(candidates)),
                (release_ids["service"], len(service_rows)),
            ):
                connection.execute(
                    """
                    insert into metadata.ingestion_run (
                        source_release_id, pipeline_version, code_commit,
                        completed_at, status, source_rows, loaded_rows, rejected_rows
                    )
                    values (%s, 'synthetic-peer-fixture', %s, %s, 'succeeded', %s, %s, 0)
                    """,
                    (release_id, "0" * 40, now, loaded_rows, loaded_rows),
                )

            parameters = {
                "source_release_ids": [
                    release_ids["supplier"],
                    release_ids["service"],
                ],
                "data_years": [2024],
                "candidate_npis": candidates,
                "hcpcs_codes": ["A6010", "A6021"],
                "min_peer_count": 2,
            }
            result = connection.execute(query, parameters)
            column_names = tuple(column.name for column in result.description)
            rows = [dict(zip(column_names, record, strict=True)) for record in result.fetchall()]
            by_candidate_code = {(row["supplier_npi"], row["hcpcs_code"]): row for row in rows}

            state_scoreable = by_candidate_code[(candidate_one, "A6010")]
            # These are the original national exact-peer results for [10, 20, 1000].
            # The added state scope must neither duplicate nor otherwise change them.
            assert state_scoreable["cohort_excluded_peer_count"] == 3
            assert state_scoreable["services_peer_median"] == Decimal("20")
            assert state_scoreable["services_peer_q75"] == Decimal("510")
            assert state_scoreable["services_cohort_excluded_high_rank"] == 2
            assert float(
                state_scoreable["services_cohort_excluded_empirical_percentile"]
            ) == pytest.approx(200 / 3)

            # Both higher-valued candidate two and all other candidate NPIs are
            # excluded; the Texas peer remains national but cannot enter Florida Q75.
            assert state_scoreable["state_peer_geography"] == "FL"
            assert state_scoreable["state_cohort_excluded_peer_count"] == 2
            assert state_scoreable["state_services_peer_q75"] == Decimal("17.5")
            assert state_scoreable["state_services_cohort_excluded_high_rank"] == 1
            assert state_scoreable[
                "state_services_cohort_excluded_empirical_percentile"
            ] == Decimal("100")
            assert state_scoreable["state_peer_evaluation_status"] == "scoreable"
            assert state_scoreable["state_raw_payment_benchmark_exposure_above_q75"] == Decimal(
                "18250.0"
            )

            insufficient = by_candidate_code[(candidate_one, "A6021")]
            assert insufficient["peer_evaluation_status"] == "scoreable"
            assert insufficient["cohort_excluded_peer_count"] == 3
            assert insufficient["state_cohort_excluded_peer_count"] == 1
            assert (
                insufficient["state_peer_evaluation_status"]
                == "insufficient-state-peer-descriptive"
            )
            assert insufficient["state_raw_payment_benchmark_exposure_above_q75"] is None
            assert insufficient["state_standardized_payment_benchmark_exposure_above_q75"] is None

            no_state = by_candidate_code[(candidate_no_state, "A6010")]
            assert no_state["state_peer_geography"] is None
            assert no_state["state_cohort_excluded_peer_count"] == 0
            assert no_state["state_peer_evaluation_status"] == "state-unavailable-descriptive"
            assert no_state["state_raw_payment_benchmark_exposure_above_q75"] is None
            assert no_state["state_standardized_payment_benchmark_exposure_above_q75"] is None

            unknown_beneficiaries = by_candidate_code[(candidate_unknown_benes, "A6010")]
            assert unknown_beneficiaries["state_cohort_excluded_peer_count"] == 2
            assert (
                unknown_beneficiaries["state_peer_evaluation_status"]
                == "unknown-beneficiary-descriptive"
            )
            assert unknown_beneficiaries["state_raw_payment_benchmark_exposure_above_q75"] is None
            assert (
                unknown_beneficiaries["state_standardized_payment_benchmark_exposure_above_q75"]
                is None
            )

            market_parameters = {
                **parameters,
                "source_address_states": ["FL", "TX", "CA"],
            }
            market_result = connection.execute(state_market_query, market_parameters)
            market_columns = tuple(column.name for column in market_result.description)
            market_rows = [
                dict(zip(market_columns, record, strict=True))
                for record in market_result.fetchall()
            ]
            by_code_state = {
                (row["hcpcs_code"], row["source_address_state"]): row for row in market_rows
            }

            # National values repeat across requested address-state sensitivities
            # at the exact year/code/rental/entity grain.
            assert len(market_rows) == 6
            florida_market = by_code_state[("A6010", "FL")]
            assert florida_market["entity_code"] == "O"
            assert florida_market["national_total_visible_cells"] == 7
            assert florida_market["national_cohort_excluded_known_beneficiary_peer_cells"] == 3
            assert florida_market["national_peer_services_median"] == Decimal("20")
            assert florida_market["national_peer_services_q75"] == Decimal("510")
            assert florida_market["state_total_visible_cells"] == 5
            assert florida_market["state_cohort_excluded_known_beneficiary_peer_cells"] == 2
            assert florida_market["state_peer_services_median"] == Decimal("15")
            assert florida_market["state_peer_services_q75"] == Decimal("17.5")
            assert florida_market["state_reconstructed_visible_medicare_payment"] == Decimal(
                "228000"
            )
            assert florida_market["state_candidate_cohort_visible_medicare_payment"] == Decimal(
                "225000"
            )

            # Requested states remain explicit even when no visible cell exists.
            california_market = by_code_state[("A6010", "CA")]
            assert california_market["national_total_visible_cells"] == 7
            assert california_market["state_total_visible_cells"] == 0
            assert california_market["state_reconstructed_visible_medicare_payment"] == 0
            assert california_market["state_visible_payment_share_of_national"] == 0
            assert (
                california_market["state_known_beneficiary_peer_status"]
                == "insufficient-cohort-excluded-peer-cells-descriptive"
            )
        finally:
            connection.rollback()


@pytest.mark.skipif(
    os.environ.get("DRLF_RUN_DB_TESTS") != "1"
    or not os.environ.get("DRLF_TEST_DATABASE_URL"),
    reason=(
        "set DRLF_RUN_DB_TESTS=1 and DRLF_TEST_DATABASE_URL "
        "to run the disposable PostgreSQL fixture"
    ),
)
def test_code_peer_review_robust_and_temporal_qualification_executes() -> None:
    database_url = os.environ["DRLF_TEST_DATABASE_URL"]
    apply_migrations(database_url, MIGRATIONS)
    token = uuid4().hex
    now = datetime.now(UTC)
    query = PEER_QUERY_PATH.read_text(encoding="utf-8")
    npi_base = 7_000_000_000 + int(token[:7], 16) % 100_000_000
    candidate_npi = str(npi_base + 1)
    peer_npis = [str(npi_base + offset) for offset in range(101, 105)]

    with psycopg.connect(database_url) as connection:
        try:
            dataset_ids: dict[str, int] = {}
            for slug, name in (
                (
                    "medicare-durable-medical-equipment-devices-supplies-by-supplier",
                    "Synthetic DMEPOS supplier temporal fixture",
                ),
                (
                    "medicare-durable-medical-equipment-devices-supplies-by-supplier-and-service",
                    "Synthetic DMEPOS supplier-service temporal fixture",
                ),
            ):
                dataset_ids[slug] = connection.execute(
                    """
                    insert into metadata.source_dataset (source, slug, name)
                    values ('Centers for Medicare & Medicaid Services', %s, %s)
                    on conflict (source, slug) do update set name = excluded.name
                    returning dataset_id
                    """,
                    (slug, name),
                ).fetchone()[0]

            versions = {
                2023: (
                    "7bb52a09-9eba-43f9-b7ec-57f65fbbc86c",
                    "44235fd3-6cf9-487f-928d-12998cd84071",
                ),
                2024: (
                    "4c6cfc68-a149-4bfe-b934-db2b92d0f360",
                    "1dc6718a-e44b-403a-a735-ce01a6233822",
                ),
            }
            release_ids: dict[tuple[int, str], int] = {}
            for data_year, (supplier_version, service_version) in versions.items():
                for label, slug, version_id, aggregation_keys, retrieval_filters in (
                    (
                        "supplier",
                        "medicare-durable-medical-equipment-devices-supplies-by-supplier",
                        supplier_version,
                        '["Suplr_NPI"]',
                        "{}",
                    ),
                    (
                        "service",
                        "medicare-durable-medical-equipment-devices-supplies-by-supplier-and-service",
                        service_version,
                        '["Suplr_NPI","HCPCS_Cd","Suplr_Rentl_Ind"]',
                        '{"HCPCS_Cd":["E1390"]}',
                    ),
                ):
                    release_ids[(data_year, label)] = connection.execute(
                        """
                        insert into metadata.source_release (
                            dataset_id, data_year, version_id, accessed_at, observed_at,
                            population, aggregation_keys, status, manifest_path,
                            manifest_sha256, retrieval_filters
                        )
                        values (
                            %s, %s, %s, %s, %s, 'Synthetic robust temporal fixture',
                            %s::jsonb, 'loaded', %s, %s, %s::jsonb
                        )
                        returning source_release_id
                        """,
                        (
                            dataset_ids[slug],
                            data_year,
                            version_id,
                            now.date(),
                            now,
                            aggregation_keys,
                            f"research/data-manifests/{token}-{data_year}-{label}.json",
                            sha256(f"{token}-{data_year}-{label}".encode()).hexdigest(),
                            retrieval_filters,
                        ),
                    ).fetchone()[0]

                connection.execute(
                    """
                    insert into claims.dmepos_supplier (
                        source_release_id, data_year, supplier_npi, entity_code,
                        supplier_specialty_description, supplier_specialty_source,
                        total_hcpcs_codes, total_beneficiaries, total_claims,
                        total_services, total_submitted_charge,
                        total_medicare_allowed_amount, total_medicare_payment_amount,
                        total_medicare_standardized_payment_amount
                    )
                    values (
                        %s, %s, %s, 'O', 'Synthetic DME supplier', 'fixture',
                        1, 100, 1000, 10000, 350000, 325000, 300000, 400000
                    )
                    """,
                    (release_ids[(data_year, "supplier")], data_year, candidate_npi),
                )

                service_rows = [
                    (
                        candidate_npi,
                        100,
                        1000,
                        10000,
                        Decimal("30"),
                        Decimal("40"),
                    ),
                    *[
                        (peer_npi, 100, value, value, Decimal("10"), standardized)
                        for peer_npi, value, standardized in zip(
                            peer_npis,
                            (100, 100, 100, 200),
                            (Decimal("10"), Decimal("10"), Decimal("10"), Decimal("5")),
                            strict=True,
                        )
                    ],
                ]
                for (
                    supplier_npi,
                    beneficiaries,
                    claims,
                    services,
                    raw_payment,
                    standardized_payment,
                ) in service_rows:
                    connection.execute(
                        """
                        insert into claims.dmepos_supplier_service (
                            source_release_id, data_year, supplier_npi,
                            supplier_last_org_name, entity_code, supplier_city,
                            supplier_state, supplier_specialty_description,
                            supplier_specialty_source, rbcs_level, rbcs_id,
                            rbcs_description, hcpcs_code, hcpcs_description,
                            supplier_rental_indicator, total_beneficiaries, total_claims,
                            total_services, average_submitted_charge,
                            average_medicare_allowed_amount,
                            average_medicare_payment_amount,
                            average_medicare_standardized_amount
                        )
                        values (
                            %s, %s, %s, 'Synthetic supplier', 'O', 'Fixture City',
                            'TX', 'Medical Supply Company', 'fixture', 'DME', 'DME',
                            'Durable Medical Equipment', 'E1390', 'Synthetic oxygen code',
                            'Y', %s, %s, %s, 35, 32.5, %s, %s
                        )
                        """,
                        (
                            release_ids[(data_year, "service")],
                            data_year,
                            supplier_npi,
                            beneficiaries,
                            claims,
                            services,
                            raw_payment,
                            standardized_payment,
                        ),
                    )

                for label, loaded_rows in (("supplier", 1), ("service", 5)):
                    connection.execute(
                        """
                        insert into metadata.ingestion_run (
                            source_release_id, pipeline_version, code_commit,
                            completed_at, status, source_rows, loaded_rows,
                            rejected_rows
                        )
                        values (
                            %s, 'synthetic-temporal-fixture', %s, %s,
                            'succeeded', %s, %s, 0
                        )
                        """,
                        (
                            release_ids[(data_year, label)],
                            "0" * 40,
                            now,
                            loaded_rows,
                            loaded_rows,
                        ),
                    )

            parameters = {
                "source_release_ids": [
                    release_ids[(year, label)]
                    for year in (2023, 2024)
                    for label in ("supplier", "service")
                ],
                "data_years": [2023, 2024],
                "candidate_npis": [candidate_npi],
                "hcpcs_codes": ["E1390"],
                "min_peer_count": 4,
            }
            result = connection.execute(query, parameters)
            column_names = tuple(column.name for column in result.description)
            rows = [dict(zip(column_names, record, strict=True)) for record in result.fetchall()]

            assert len(rows) == 2
            assert len(column_names) == len(set(column_names))
            assert max(len(name.encode("utf-8")) for name in column_names) <= 63
            assert "standardized_payment_cohort_excluded_empirical_percentile" in column_names
            for row in rows:
                assert row["claims_per_beneficiary"] == Decimal("10")
                assert row["claims_peer_median"] == Decimal("1")
                assert row["claims_cohort_excluded_median_ratio"] == Decimal("10")
                assert row["claims_cohort_excluded_scale_source"] == "iqr-fallback"
                assert row["claims_cohort_excluded_robust_z"] > Decimal("3.5")
                assert row["services_cohort_excluded_scale_source"] == "iqr-fallback"
                assert row["standardized_payment_cohort_excluded_scale_source"] == "degenerate"
                assert row["standardized_payment_cohort_excluded_robust_z"] is None
                assert row["exact_cell_qualification_status"] == "scoreable"
                assert row["claims_tail_flag"] is True
                assert row["claims_robust_tail_flag"] is True
                assert row["utilization_robust_tail_flag"] is True
                assert row["claims_full_outlier_flag"] is True
                assert row["utilization_full_outlier_flag"] is True
                assert row["standardized_payment_tail_flag"] is True
                assert row["standardized_payment_full_outlier_flag"] is False
                assert row["observed_raw_payment_magnitude_flag"] is True
                assert row["raw_payment_q75_exposure_magnitude_flag"] is True
                assert row["standardized_q75_exposure_magnitude_flag"] is True
                assert row["exact_cell_magnitude_gates_pass"] is True
                assert row["annual_exact_cell_tail_qualified"] is True
                assert row["annual_exact_cell_full_qualified"] is True
                assert row["exact_cell_observed_year_count"] == 2
                assert row["exact_cell_focus_year_full_flag"] is True
                assert row["exact_cell_prior_year_full_flag"] is True
                assert row["temporal_exact_cell_tail_qualified"] is True
                assert row["temporal_exact_cell_full_qualified"] is True
                assert row["exact_cell_algorithm"] == "dmepos-supplier-service-exact-cell"
                assert row["exact_cell_algorithm_version"] == "1"
        finally:
            connection.rollback()
