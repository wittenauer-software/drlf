import re
from pathlib import Path

QUERY_PATH = (
    Path(__file__).resolve().parents[1]
    / "sql"
    / "analysis"
    / "part_b"
    / "provider_service_scan.sql"
)


def _query() -> str:
    return QUERY_PATH.read_text(encoding="utf-8").lower()


def test_scan_binds_and_audits_both_part_b_release_types() -> None:
    query = _query()

    assert "unnest(%(source_release_ids)s::bigint[])" in query
    assert "release.status = 'loaded'" in query
    assert "medicare-physician-other-practitioners-by-provider'" in query
    assert "medicare-physician-other-practitioners-by-provider-and-service'" in query
    assert "join claims.part_b_provider as provider" in query
    assert "join claims.part_b_provider_service as service" in query
    assert "provider.source_release_id = year.provider_release_id" in query
    assert "service.source_release_id = any(provider.service_release_ids)" in query
    assert "service_release_count >= 1" in query
    assert "release.retrieval_filters" in query
    assert "requested_service_cells" in query
    assert "service_selection_coverage" in query
    assert "covering every requested year/hcpcs cell" in query
    assert "duplicate_service_cells" in query
    assert "having count(*) > 1" in query
    assert "first duplicate=" in query


def test_scan_preserves_cell_grain_and_safe_minimums() -> None:
    query = _query()

    assert "100::bigint as minimum_panel_beneficiaries" in query
    assert "11::bigint as minimum_descriptive_beneficiaries" in query
    assert "20::bigint as minimum_tested_beneficiaries" in query
    assert "100::bigint as minimum_peer_count" in query
    assert "service.total_beneficiaries::numeric" in query
    assert "/ nullif(provider.provider_total_beneficiaries, 0)" in query
    assert "service.total_beneficiary_day_services::numeric" in query
    assert "/ nullif(service.total_beneficiaries, 0)" in query
    assert "service.total_services::numeric" in query
    assert "/ nullif(service.total_beneficiary_day_services, 0)" in query
    assert "sum(service.total_beneficiaries)" not in query
    assert "sum(distinct" not in query
    assert "do not sum tested beneficiaries" in query


def test_scan_uses_robust_peer_statistics_and_route_flags() -> None:
    query = _query()

    assert "percentile_cont(0.50)" in query
    assert "cume_dist() over" in query
    assert "broad_peer_count" in query
    assert "broader-peer-tail-descriptive" in query
    assert "0.67448975" in query
    assert "1.3489795" in query
    assert "0.99::numeric as tail_percentile_threshold" in query
    assert "3.5::numeric as robust_z_threshold" in query
    assert "reach_outlier_flag" in query
    assert "repeat_day_intensity_flag" in query
    assert "unit_intensity_flag" in query
    assert "insufficient-peer-descriptive" in query
    assert "then 'insufficient-peer'" in query
    assert "insufficient-volume-descriptive" in query
    assert "then 'insufficient-volume'" in query
    assert "11 through 19 beneficiaries are retained" in query
    assert "coalesce(" in query
    assert "then 'reach-and-intensity'" in query
    assert "fraud_score" not in query
    assert "peer_evaluation_status = 'scoreable'" in query
    assert "0.975::numeric as temporal_tail_percentile_threshold" in query
    assert "reach_temporally_confirmed" in query
    assert "repeat_days_temporally_confirmed" in query
    assert "unit_temporally_confirmed" in query
    assert "temporal_triage_route" in query
    assert "and comparable_years >= 3" in query


def test_scan_reconstructs_distinct_monetary_measures() -> None:
    query = _query()

    assert "reconstructed_medicare_allowed_amount" in query
    assert "reconstructed_medicare_payment_amount" in query
    assert "reconstructed_medicare_standardized_amount" in query
    assert "they are not personal income, loss, or damages" in query


def test_scan_has_no_literal_percent_signs_outside_named_parameters() -> None:
    query = _query()

    without_named_parameters = re.sub(r"%\([a-z_]+\)s", "", query)
    assert "%" not in without_named_parameters
