from __future__ import annotations

import csv
from collections.abc import Callable
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from drlf.analysis.dmepos_code_freeze import (
    DISCOVERY_ALGORITHM,
    DISCOVERY_ALGORITHM_VERSION,
    DISCOVERY_COLUMNS,
    FROZEN_COLUMNS,
    MATERIALITY_FREEZE_ALGORITHM,
    MATERIALITY_FREEZE_ALGORITHM_VERSION,
    MATERIALITY_FROZEN_COLUMNS,
    MATERIALITY_SELECTION_MODE,
    MONETARY_CAVEAT,
    freeze_dmepos_materiality_codes,
    freeze_dmepos_service_codes,
)

QUERY_PATH = Path("sql/analysis/dmepos/candidate_service_code_discovery.sql")
NPIS = ("1111111111", "2222222222")
RELEASES = ("41", "42")


def _row(
    *,
    npi: str,
    code: str,
    payment: int,
    rank: int,
    prior: int,
    after: int,
    cohort_payment: int,
    top: bool,
    cohort: bool,
    coverage: bool,
    maximum_selected_codes: int = 25,
    payment_coverage: str = "0.90909090909090909091",
) -> dict[str, str]:
    prior_share = str(prior / 1000).rstrip("0").rstrip(".") if prior else "0"
    after_share = str(after / 1000).rstrip("0").rstrip(".")
    reasons = [
        reason
        for selected, reason in (
            (top, "candidate-top-n"),
            (cohort, "cohort-payment-floor"),
            (coverage, "candidate-coverage-target"),
        )
        if selected
    ]
    values: dict[str, str] = {
        "code_discovery_algorithm": DISCOVERY_ALGORITHM,
        "code_discovery_algorithm_version": DISCOVERY_ALGORITHM_VERSION,
        "focus_year": "2024",
        "candidate_npis_parameter": "{1111111111,2222222222}",
        "source_release_ids_parameter": "{41,42}",
        "top_codes_per_candidate": "1",
        "min_cohort_code_payment": "500",
        "target_candidate_visible_payment_share": "0.9",
        "min_visible_summary_payment_coverage": "0.8",
        "maximum_selected_codes": str(maximum_selected_codes),
        "supplier_summary_source_release_id": "41",
        "supplier_service_source_release_id": "42",
        "supplier_npi": npi,
        "entity_code": "O",
        "hcpcs_code": code,
        "hcpcs_description": f"Fixture {code}",
        "rental_cell_count": "1",
        "rental_indicators": "N",
        "nonunique_cell_claim_count_sum": str(payment // 10),
        "total_services": str(payment // 2),
        "reconstructed_submitted_charge": str(payment * 2),
        "reconstructed_medicare_allowed_amount": str(payment * 1.2),
        "reconstructed_medicare_payment_amount": str(payment),
        "reconstructed_medicare_standardized_payment_amount": str(payment * 0.99),
        "summary_total_submitted_charge": "2200",
        "summary_total_medicare_allowed_amount": "1320",
        "summary_total_medicare_payment_amount": "1100",
        "summary_total_medicare_standardized_payment_amount": "1089",
        "visible_detail_reconstructed_submitted_charge": "2000",
        "visible_detail_reconstructed_medicare_allowed_amount": "1200",
        "visible_detail_reconstructed_medicare_payment_amount": "1000",
        "visible_detail_reconstructed_standardized_payment_amount": "990",
        "visible_summary_submitted_charge_coverage": payment_coverage,
        "visible_summary_allowed_amount_coverage": payment_coverage,
        "visible_summary_payment_coverage": payment_coverage,
        "visible_summary_standardized_payment_coverage": payment_coverage,
        "candidate_payment_rank": str(rank),
        "candidate_prior_cumulative_visible_payment": str(prior),
        "candidate_after_cumulative_visible_payment": str(after),
        "candidate_prior_cumulative_visible_payment_share": prior_share,
        "candidate_after_cumulative_visible_payment_share": after_share,
        "cohort_reconstructed_medicare_payment_amount": str(cohort_payment),
        "cohort_reconstructed_medicare_standardized_payment_amount": str(cohort_payment * 0.99),
        "selected_by_candidate_top_n": str(top),
        "selected_by_cohort_payment": str(cohort),
        "selected_by_candidate_coverage": str(coverage),
        "selected_for_code_freeze": str(bool(reasons)),
        "selection_reason": "|".join(reasons),
        "monetary_caveat": MONETARY_CAVEAT,
    }
    assert tuple(values) == DISCOVERY_COLUMNS
    return values


def _rows(*, maximum_selected_codes: int = 25) -> list[dict[str, str]]:
    return [
        _row(
            npi=NPIS[0],
            code="A1000",
            payment=600,
            rank=1,
            prior=0,
            after=600,
            cohort_payment=700,
            top=True,
            cohort=True,
            coverage=True,
            maximum_selected_codes=maximum_selected_codes,
        ),
        _row(
            npi=NPIS[0],
            code="B2000",
            payment=300,
            rank=2,
            prior=600,
            after=900,
            cohort_payment=1000,
            top=False,
            cohort=True,
            coverage=True,
            maximum_selected_codes=maximum_selected_codes,
        ),
        _row(
            npi=NPIS[0],
            code="C3000",
            payment=100,
            rank=3,
            prior=900,
            after=1000,
            cohort_payment=300,
            top=False,
            cohort=False,
            coverage=False,
            maximum_selected_codes=maximum_selected_codes,
        ),
        _row(
            npi=NPIS[1],
            code="B2000",
            payment=700,
            rank=1,
            prior=0,
            after=700,
            cohort_payment=1000,
            top=True,
            cohort=True,
            coverage=True,
            maximum_selected_codes=maximum_selected_codes,
        ),
        _row(
            npi=NPIS[1],
            code="C3000",
            payment=200,
            rank=2,
            prior=700,
            after=900,
            cohort_payment=300,
            top=False,
            cohort=False,
            coverage=True,
            maximum_selected_codes=maximum_selected_codes,
        ),
        _row(
            npi=NPIS[1],
            code="A1000",
            payment=100,
            rank=3,
            prior=900,
            after=1000,
            cohort_payment=700,
            top=False,
            cohort=True,
            coverage=False,
            maximum_selected_codes=maximum_selected_codes,
        ),
    ]


def _rows_with_target_share(target: Decimal) -> list[dict[str, str]]:
    rows = _rows()
    for row in rows:
        row["target_candidate_visible_payment_share"] = str(target)
        coverage = Decimal(row["candidate_prior_cumulative_visible_payment_share"]) < target
        row["selected_by_candidate_coverage"] = str(coverage)
        reasons = [
            reason
            for selected, reason in (
                (row["selected_by_candidate_top_n"] == "True", "candidate-top-n"),
                (row["selected_by_cohort_payment"] == "True", "cohort-payment-floor"),
                (coverage, "candidate-coverage-target"),
            )
            if selected
        ]
        row["selected_for_code_freeze"] = str(bool(reasons))
        row["selection_reason"] = "|".join(reasons)
    return rows


def _write(
    path: Path,
    rows: list[dict[str, str]],
    columns: tuple[str, ...] = DISCOVERY_COLUMNS,
) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as input_file:
        return list(csv.DictReader(input_file))


def test_freeze_validates_complete_decomposition_and_writes_sorted_union(tmp_path: Path) -> None:
    input_path = tmp_path / "registered.csv"
    output_path = tmp_path / "codes.csv"
    _write(input_path, list(reversed(_rows())))

    code_count, input_rows, output_hash = freeze_dmepos_service_codes(
        input_path,
        output_path,
        focus_year=2024,
        top_codes_per_candidate=1,
        min_cohort_code_payment=500,
        target_candidate_visible_payment_share=Decimal("0.9"),
        min_visible_summary_payment_coverage=Decimal("0.8"),
    )

    assert (code_count, input_rows) == (3, 6)
    assert output_hash == sha256(output_path.read_bytes()).hexdigest()
    frozen = _read(output_path)
    assert tuple(frozen[0]) == FROZEN_COLUMNS
    assert [row["hcpcs_code"] for row in frozen] == ["A1000", "B2000", "C3000"]
    assert {row["selected_code_count"] for row in frozen} == {"3"}
    assert {row["discovery_input_rows"] for row in frozen} == {"6"}
    assert {row["discovery_input_bytes"] for row in frozen} == {str(input_path.stat().st_size)}
    assert {row["discovery_input_sha256"] for row in frozen} == {
        sha256(input_path.read_bytes()).hexdigest()
    }
    assert {row["max_input_rows"] for row in frozen} == {"10000"}
    assert {row["max_output_bytes"] for row in frozen} == {str(1024 * 1024)}
    assert frozen[0]["top_n_candidate_npis"] == "{1111111111}"
    assert frozen[1]["top_n_candidate_npis"] == "{2222222222}"
    assert frozen[2]["coverage_target_candidate_npis"] == "{2222222222}"
    assert frozen[2]["source_candidate_rows"] == "2"


def test_materiality_freeze_is_complete_distinct_and_deterministic(tmp_path: Path) -> None:
    rows = _rows_with_target_share(Decimal("0.7"))
    assert all(
        row["selected_for_code_freeze"] == "False" for row in rows if row["hcpcs_code"] == "C3000"
    )
    input_path = tmp_path / "registered.csv"
    output_path = tmp_path / "materiality.csv"
    _write(input_path, list(reversed(rows)))

    code_count, input_rows, output_hash = freeze_dmepos_materiality_codes(
        input_path,
        output_path,
        focus_year=2024,
        observed_payment_threshold=Decimal("200"),
        top_codes_per_candidate=1,
        min_cohort_code_payment=500,
        target_candidate_visible_payment_share=Decimal("0.7"),
        min_visible_summary_payment_coverage=Decimal("0.8"),
    )

    assert (code_count, input_rows) == (3, 6)
    assert output_hash == sha256(output_path.read_bytes()).hexdigest()
    frozen = _read(output_path)
    assert tuple(frozen[0]) == MATERIALITY_FROZEN_COLUMNS
    assert all(len(column.encode("utf-8")) <= 63 for column in MATERIALITY_FROZEN_COLUMNS)
    assert [row["hcpcs_code"] for row in frozen] == ["A1000", "B2000", "C3000"]
    assert {row["freeze_algorithm"] for row in frozen} == {MATERIALITY_FREEZE_ALGORITHM}
    assert {row["freeze_algorithm_version"] for row in frozen} == {
        MATERIALITY_FREEZE_ALGORITHM_VERSION
    }
    assert {row["selection_mode"] for row in frozen} == {MATERIALITY_SELECTION_MODE}
    assert {row["observed_payment_threshold"] for row in frozen} == {"200"}
    assert {row["candidate_npis_parameter"] for row in frozen} == {"{1111111111,2222222222}"}
    assert {row["source_release_ids_parameter"] for row in frozen} == {"{41,42}"}
    assert {row["supplier_summary_source_release_id"] for row in frozen} == {"41"}
    assert {row["supplier_service_source_release_id"] for row in frozen} == {"42"}
    assert {row["discovery_input_rows"] for row in frozen} == {"6"}
    assert {row["discovery_input_bytes"] for row in frozen} == {str(input_path.stat().st_size)}
    assert {row["discovery_input_sha256"] for row in frozen} == {
        sha256(input_path.read_bytes()).hexdigest()
    }
    assert {row["max_input_bytes"] for row in frozen} == {str(16 * 1024 * 1024)}
    assert {row["max_input_rows"] for row in frozen} == {"10000"}
    assert {row["max_output_bytes"] for row in frozen} == {str(1024 * 1024)}
    assert {row["max_materiality_codes"] for row in frozen} == {"100"}
    assert {row["selected_code_count"] for row in frozen} == {"3"}
    by_code = {row["hcpcs_code"]: row for row in frozen}
    assert by_code["A1000"]["qualifying_candidate_npis"] == "{1111111111}"
    assert by_code["B2000"]["qualifying_candidate_npis"] == ("{1111111111,2222222222}")
    assert by_code["B2000"]["maximum_candidate_reconstructed_medicare_payment_amount"] == "700"
    assert by_code["C3000"]["qualifying_candidate_npis"] == "{2222222222}"
    assert by_code["C3000"]["maximum_candidate_reconstructed_medicare_payment_amount"] == "200"
    assert by_code["C3000"]["source_candidate_rows"] == "2"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda rows: rows.append(dict(rows[0])), "repeats a candidate-NPI/HCPCS"),
        (lambda rows: rows.pop(), "omits part|cohort code totals"),
        (
            lambda rows: rows.__setitem__(0, {**rows[0], "focus_year": "2023"}),
            "handshake",
        ),
        (
            lambda rows: rows.__setitem__(
                0,
                {
                    **rows[0],
                    "supplier_summary_source_release_id": "42",
                    "supplier_service_source_release_id": "41",
                },
            ),
            "mixes source-release roles",
        ),
    ],
)
def test_materiality_freeze_fails_closed_on_invalid_discovery(
    tmp_path: Path,
    mutate: Callable[[list[dict[str, str]]], object],
    message: str,
) -> None:
    rows = _rows_with_target_share(Decimal("0.7"))
    mutate(rows)
    input_path = tmp_path / "invalid.csv"
    output_path = tmp_path / "must-not-exist.csv"
    _write(input_path, rows)

    with pytest.raises(ValueError, match=message):
        freeze_dmepos_materiality_codes(
            input_path,
            output_path,
            focus_year=2024,
            observed_payment_threshold=200,
            top_codes_per_candidate=1,
            min_cohort_code_payment=500,
            target_candidate_visible_payment_share=Decimal("0.7"),
        )
    assert not output_path.exists()


def test_materiality_freeze_enforces_schema_caps_threshold_and_no_overwrite(
    tmp_path: Path,
) -> None:
    rows = _rows_with_target_share(Decimal("0.7"))
    input_path = tmp_path / "valid.csv"
    _write(input_path, rows)
    settings = {
        "focus_year": 2024,
        "observed_payment_threshold": 200,
        "top_codes_per_candidate": 1,
        "min_cohort_code_payment": 500,
        "target_candidate_visible_payment_share": Decimal("0.7"),
    }

    bad_schema = tmp_path / "bad-schema.csv"
    bad_columns = DISCOVERY_COLUMNS[:-1]
    _write(
        bad_schema,
        [{field: row[field] for field in bad_columns} for row in rows],
        bad_columns,
    )
    with pytest.raises(ValueError, match="column contract"):
        freeze_dmepos_materiality_codes(
            bad_schema,
            tmp_path / "bad-schema-output.csv",
            **settings,
        )

    for cap_name, cap_value, message in (
        ("max_input_bytes", 1, "maximum is 1"),
        ("max_input_rows", 5, "exceeds 5 data rows"),
        ("max_output_bytes", 1, "would exceed 1 bytes"),
        ("max_materiality_codes", 2, "3 materiality-complete codes; maximum is 2"),
    ):
        output_path = tmp_path / f"{cap_name}.csv"
        with pytest.raises(ValueError, match=message):
            freeze_dmepos_materiality_codes(
                input_path,
                output_path,
                **settings,
                **{cap_name: cap_value},
            )
        assert not output_path.exists()

    with pytest.raises(ValueError, match="threshold must be finite and positive"):
        freeze_dmepos_materiality_codes(
            input_path,
            tmp_path / "zero-threshold.csv",
            **{**settings, "observed_payment_threshold": 0},
        )

    existing = tmp_path / "existing.csv"
    existing.write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Refusing to replace"):
        freeze_dmepos_materiality_codes(input_path, existing, **settings)
    assert existing.read_text(encoding="utf-8") == "preserve"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda rows: rows.__setitem__(0, {**rows[0], "focus_year": "2023"}),
            "handshake",
        ),
        (
            lambda rows: rows.__setitem__(0, {**rows[0], "selection_reason": "wrong"}),
            "selection reason",
        ),
        (
            lambda rows: rows.__setitem__(
                0,
                {**rows[0], "visible_summary_payment_coverage": "0.7"},
            ),
            "coverage floor",
        ),
        (
            lambda rows: rows.pop(2),
            "complete candidate ranking|omits part",
        ),
    ],
)
def test_freeze_fails_closed_without_creating_output(
    tmp_path: Path,
    mutate: Callable[[list[dict[str, str]]], object],
    message: str,
) -> None:
    rows = _rows()
    mutate(rows)
    input_path = tmp_path / "invalid.csv"
    output_path = tmp_path / "must-not-exist.csv"
    _write(input_path, rows)

    with pytest.raises(ValueError, match=message):
        freeze_dmepos_service_codes(
            input_path,
            output_path,
            focus_year=2024,
            top_codes_per_candidate=1,
            min_cohort_code_payment=500,
        )
    assert not output_path.exists()


def test_freeze_rejects_union_overflow_caps_bad_schema_and_replacement(tmp_path: Path) -> None:
    overflow_input = tmp_path / "overflow.csv"
    _write(overflow_input, _rows(maximum_selected_codes=2))
    with pytest.raises(ValueError, match="selected 3 codes; maximum is 2"):
        freeze_dmepos_service_codes(
            overflow_input,
            tmp_path / "overflow-output.csv",
            focus_year=2024,
            top_codes_per_candidate=1,
            min_cohort_code_payment=500,
            max_selected_codes=2,
        )

    input_path = tmp_path / "valid.csv"
    _write(input_path, _rows())
    with pytest.raises(ValueError, match="maximum is 1"):
        freeze_dmepos_service_codes(
            input_path,
            tmp_path / "byte-cap.csv",
            focus_year=2024,
            max_input_bytes=1,
        )

    bad_schema = tmp_path / "bad-schema.csv"
    bad_columns = DISCOVERY_COLUMNS[:-1]
    _write(
        bad_schema,
        [{field: row[field] for field in bad_columns} for row in _rows()],
        bad_columns,
    )
    with pytest.raises(ValueError, match="column contract"):
        freeze_dmepos_service_codes(bad_schema, tmp_path / "schema-output.csv", focus_year=2024)

    existing = tmp_path / "existing.csv"
    existing.write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Refusing to replace"):
        freeze_dmepos_service_codes(input_path, existing, focus_year=2024)
    assert existing.read_text(encoding="utf-8") == "preserve"


def test_candidate_code_sql_has_bounded_exact_scope_and_complete_output() -> None:
    query = QUERY_PATH.read_text(encoding="utf-8")
    lower = query.lower()

    for parameter in (
        "focus_year",
        "candidate_npis",
        "top_codes_per_candidate",
        "min_cohort_code_payment",
        "target_candidate_visible_payment_share",
        "min_visible_summary_payment_coverage",
        "max_selected_codes",
        "source_release_ids",
    ):
        assert f"%({parameter})s" in lower
    assert "cardinality(settings.candidate_npis) between 1 and 100" in lower
    assert "settings.top_codes_per_candidate between 1 and 25" in lower
    assert "settings.maximum_selected_codes between 1 and 100" in lower
    assert "count(*) between 1 and settings.maximum_selected_codes" in lower
    assert "left join evaluated_candidate_codes as evaluated on true" in lower
    assert "selected_for_code_freeze" in lower
    assert "nonunique_cell_claim_count_sum" in lower
    assert "sum(service.total_beneficiaries)" not in lower


def test_candidate_code_sql_pins_releases_filters_loads_and_reconciliation() -> None:
    query = QUERY_PATH.read_text(encoding="utf-8")
    lower = query.lower()

    for version_id in (
        "471f9c0d-12c4-4601-a547-559dcfa2d0e2",
        "7bb52a09-9eba-43f9-b7ec-57f65fbbc86c",
        "4c6cfc68-a149-4bfe-b934-db2b92d0f360",
        "dad703df-2e52-4ac6-a26f-51ecbf463d77",
        "44235fd3-6cf9-487f-928d-12998cd84071",
        "1dc6718a-e44b-403a-a735-ce01a6233822",
    ):
        assert version_id in lower
    assert "'suplr_npi', jsonb_agg(candidate.supplier_npi" in lower
    assert "release.supplier_retrieval_filters = '{}'::jsonb" in lower
    assert "release.supplier_service_retrieval_filters = expected.retrieval_filters" in lower
    assert "metadata.ingestion_run as run" in lower
    assert "run.status = 'succeeded'" in lower
    assert "run.loaded_rows = (" in lower
    assert "duplicate_service_cells" in lower
    assert "visible_summary_payment_coverage" in lower
    assert ">= settings.minimum_visible_summary_payment_coverage" in lower
    assert "validation_anchor as materialized" in lower
    assert lower.count("coalesce(valid::text, 'null')") >= 5


def test_candidate_code_sql_output_matches_freeze_input_contract() -> None:
    query = QUERY_PATH.read_text(encoding="utf-8")
    final_select = query.split("\nselect\n    output.code_discovery_algorithm", maxsplit=1)[1]
    final_select = (
        "output.code_discovery_algorithm" + final_select.split("from output_rows", maxsplit=1)[0]
    )
    output_columns = tuple(
        line.strip().removeprefix("output.").rstrip(",")
        for line in final_select.splitlines()
        if line.strip()
    )
    assert output_columns == DISCOVERY_COLUMNS
    assert all(len(column.encode("utf-8")) <= 63 for column in output_columns)
