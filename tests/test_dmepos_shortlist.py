from __future__ import annotations

import csv
from hashlib import sha256
from pathlib import Path

import pytest
from typer.testing import CliRunner

from drlf import cli
from drlf.analysis.dmepos_shortlist import (
    ANONYMOUS_OUTPUT_COLUMNS,
    DEFAULT_MAX_INPUT_BYTES,
    DEFAULT_MAX_INPUT_ROWS,
    IDENTIFIED_OUTPUT_COLUMNS,
    IDENTITY_COLUMNS,
    PROHIBITED_IDENTITY_COLUMNS,
    REQUIRED_COLUMNS,
    SCAN_COLUMNS,
    SCREEN_ALGORITHM,
    SCREEN_ALGORITHM_COLUMNS,
    SCREEN_ALGORITHM_VERSION,
    SCREEN_LABEL,
    generate_dmepos_anonymous_shortlist,
    join_dmepos_shortlist_identities,
    read_dmepos_anonymous_candidate_npis,
    read_dmepos_anonymous_candidate_scope,
)


def _set_metric(
    row: dict[str, str],
    prefix: str,
    *,
    percentile: str,
    ratio: str,
    robust_z: str,
) -> None:
    row.update(
        {
            f"{prefix}_per_beneficiary": "2500.123456789",
            f"{prefix}_peer_median": "1000.123456789",
            f"{prefix}_peer_q75": "1200.123456789",
            f"{prefix}_peer_iqr": "400.123456789",
            f"{prefix}_peer_mad": "200.123456789",
            f"{prefix}_empirical_percentile": percentile,
            f"{prefix}_median_ratio": ratio,
            f"{prefix}_median_absolute_delta": "1500.000000001",
            f"{prefix}_robust_z": robust_z,
            f"{prefix}_scale_source": "mad",
        }
    )


def _row(
    *,
    year: int,
    npi: str,
    candidate: bool,
    temporal: bool,
    tail_years: int,
    full_years: int,
    high_spend: bool | None = None,
    **overrides: str,
) -> dict[str, str]:
    high_spend = candidate if high_spend is None else high_spend
    total_payment = "500000.123456" if high_spend else "50000.123456"
    standardized_total = "480000.123456" if high_spend else "48000.123456"
    row = {column: "" for column in REQUIRED_COLUMNS}
    if temporal:
        route = "high-dollar-persistent"
    elif year == 2024 and candidate:
        route = "high-dollar-emerging"
    elif high_spend:
        route = "high-spend-context"
    else:
        route = "not-qualified"
    row.update(
        {
            "screen_algorithm": SCREEN_ALGORITHM,
            "screen_algorithm_version": SCREEN_ALGORITHM_VERSION,
            "data_year": str(year),
            "supplier_npi": npi,
            "entity_code": "O",
            "supplier_specialty_description": "Medical Supply Company with Orthotist",
            "supplier_specialty_source": "CMS specialty",
            "dominant_broad_category": "DME",
            "beneficiary_volume_band": "1000-4999",
            "dme_payment_share": "0.9000000001",
            "pos_payment_share": "0.0500000001",
            "drug_payment_share": "0.0499999998",
            "source_release_id": str(year - 2000),
            "total_hcpcs_codes": "20",
            "total_beneficiaries": "1000",
            "total_claims": "2500",
            "total_services": "2600.123456789",
            "total_submitted_charge": "900000.123456",
            "total_medicare_allowed_amount": "600000.123456",
            "total_medicare_payment_amount": total_payment,
            "total_medicare_standardized_payment_amount": standardized_total,
            "raw_payment_benchmark_exposure_above_q75": (
                "150000.0000001" if candidate else "10000.0000001"
            ),
            "standardized_payment_benchmark_exposure_above_q75": (
                "140000.0000001" if candidate else "9000.0000001"
            ),
            "peer_count": "120",
            "peer_evaluation_status": "scoreable",
            "raw_payment_full_outlier_flag": str(candidate),
            "standardized_payment_full_outlier_flag": str(candidate),
            "claims_full_outlier_flag": str(candidate),
            "services_full_outlier_flag": "False",
            "standardized_payment_tail_flag": str(candidate),
            "utilization_tail_flag": str(candidate),
            "magnitude_gates_pass": str(candidate),
            "candidate_tail_year": str(candidate),
            "candidate_full_year": str(candidate),
            "comparable_years": "3",
            "candidate_tail_years": str(tail_years),
            "candidate_full_years": str(full_years),
            "latest_year_present": "True",
            "high_priority_temporal_flag": str(temporal),
            "trajectory_route": route,
            "requested_data_years": "2022;2023;2024",
            "latest_year_parameter": "2024",
            "minimum_panel_beneficiaries": "100",
            "minimum_peer_count": "100",
            "minimum_annual_payment": "100000",
            "minimum_benchmark_exposure": "100000.000",
            "tail_percentile_threshold": "97.500000000",
            "full_percentile_threshold": "99.000000000",
            "minimum_robust_z": "3.500000",
            "minimum_ratio": "2.000",
            "category_caveat": "Category caveat.",
            "monetary_caveat": "Not supplier income or loss.",
            "benchmark_caveat": "Q75 is a prioritization benchmark.",
            "route_caveat": "Payment alone cannot qualify.",
        }
    )
    for prefix in ("standardized_payment", "raw_payment", "claims"):
        _set_metric(
            row,
            prefix,
            percentile="99.0000000001" if candidate else "50.0000000001",
            ratio="2.0000000001" if candidate else "1.0000000001",
            robust_z="3.5000000001" if candidate else "0.0000000001",
        )
    _set_metric(
        row,
        "services",
        percentile="50.0000000001",
        ratio="1.0000000001",
        robust_z="0.0000000001",
    )
    row.update(overrides)
    return row


def _write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("x", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as input_file:
        return list(csv.DictReader(input_file))


def _identity(year: int, npi: str, name: str, *, source_release_id: int = 24) -> dict[str, str]:
    return {
        "data_year": str(year),
        "supplier_npi": npi,
        "source_release_id": str(source_release_id),
        "supplier_last_org_name": name,
        "supplier_first_name": "",
        "supplier_city": "Austin",
        "supplier_state": "TX",
    }


def _candidate_rows() -> tuple[list[dict[str, str]], str, str]:
    persistent = "1000000001"
    emerging = "1000000002"
    rows = [
        *[
            _row(
                year=year,
                npi=persistent,
                candidate=True,
                temporal=True,
                tail_years=3,
                full_years=3,
            )
            for year in (2022, 2023, 2024)
        ],
        *[
            _row(
                year=year,
                npi=emerging,
                candidate=year == 2024,
                temporal=False,
                tail_years=1,
                full_years=1,
            )
            for year in (2022, 2023, 2024)
        ],
    ]
    rows[1]["minimum_annual_payment"] = "100000.00"
    return rows, persistent, emerging


def test_two_stage_shortlist_freezes_candidates_before_exact_identity_join(
    tmp_path: Path,
) -> None:
    rows, persistent, emerging = _candidate_rows()
    scan = tmp_path / "scan.csv"
    anonymous = tmp_path / "anonymous.csv"
    anonymous_copy = tmp_path / "anonymous-copy.csv"
    identities = tmp_path / "identities.csv"
    identified = tmp_path / "identified.csv"
    _write_csv(scan, rows, list(SCAN_COLUMNS))

    result = generate_dmepos_anonymous_shortlist(scan, anonymous)
    repeated = generate_dmepos_anonymous_shortlist(scan, anonymous_copy)

    assert result[:3] == (2, 1, 1)
    assert result[3] == sha256(anonymous.read_bytes()).hexdigest()
    assert repeated == result
    assert anonymous_copy.read_bytes() == anonymous.read_bytes()
    anonymous_rows = _read_csv(anonymous)
    assert tuple(anonymous_rows[0]) == ANONYMOUS_OUTPUT_COLUMNS
    assert anonymous_rows[0]["screen_label"] == SCREEN_LABEL
    assert not (set(SCREEN_ALGORITHM_COLUMNS) & set(ANONYMOUS_OUTPUT_COLUMNS))
    assert [row["supplier_npi"] for row in anonymous_rows] == [persistent, emerging]
    assert [row["selection_reason"] for row in anonymous_rows] == [
        "high-dollar-persistent",
        "high-dollar-emerging",
    ]
    assert not (set(ANONYMOUS_OUTPUT_COLUMNS) & PROHIBITED_IDENTITY_COLUMNS)

    scope = read_dmepos_anonymous_candidate_npis(anonymous)
    assert scope == (
        [persistent, emerging],
        result[3],
        anonymous.stat().st_size,
        2,
    )
    pair_scope = read_dmepos_anonymous_candidate_scope(anonymous)
    assert pair_scope == (
        [(persistent, 24), (emerging, 24)],
        result[3],
        anonymous.stat().st_size,
        2,
    )
    _write_csv(
        identities,
        [_identity(2024, emerging, "Emerging Supply"), _identity(2024, persistent, "Stable DME")],
        sorted(IDENTITY_COLUMNS),
    )
    identified_count, identified_hash = join_dmepos_shortlist_identities(
        anonymous, identities, identified
    )

    assert identified_count == 2
    assert identified_hash == sha256(identified.read_bytes()).hexdigest()
    identified_rows = _read_csv(identified)
    assert tuple(identified_rows[0]) == IDENTIFIED_OUTPUT_COLUMNS
    assert [row["supplier_npi"] for row in identified_rows] == [persistent, emerging]
    assert identified_rows[0]["supplier_name"] == "Stable DME"
    assert identified_rows[1]["supplier_name"] == "Emerging Supply"
    assert identified_rows[0]["anonymous_shortlist_sha256"] == result[3]
    assert identified_rows[0]["identity_scope_requirement"] == (
        "exactly frozen 2024 candidate NPI/source-release pairs"
    )
    assert "recoverable amounts" in identified_rows[0]["screen_caveat"]


def test_anonymous_reader_keeps_frozen_v1_artifacts_readable(tmp_path: Path) -> None:
    rows, persistent, emerging = _candidate_rows()
    scan = tmp_path / "scan.csv"
    anonymous = tmp_path / "anonymous.csv"
    legacy = tmp_path / "legacy-v1.csv"
    _write_csv(scan, rows, list(SCAN_COLUMNS))
    generate_dmepos_anonymous_shortlist(scan, anonymous)
    legacy_rows = _read_csv(anonymous)
    for row in legacy_rows:
        row["screen_label"] = "dmepos-supplier-summary-materiality-v1"
    _write_csv(legacy, legacy_rows, list(ANONYMOUS_OUTPUT_COLUMNS))

    assert read_dmepos_anonymous_candidate_npis(legacy)[0] == [persistent, emerging]


def test_shortlist_requires_exact_v2_algorithm_contract_before_labeling(
    tmp_path: Path,
) -> None:
    rows, _, _ = _candidate_rows()

    for field, invalid_value in (
        ("screen_algorithm", "dmepos-supplier-summary-materiality-v1"),
        ("screen_algorithm_version", "1"),
        ("screen_algorithm_version", " 2"),
    ):
        invalid_rows = [dict(row) for row in rows]
        for row in invalid_rows:
            row[field] = invalid_value
        invalid_scan = tmp_path / f"invalid-{field}-{invalid_value.strip()}.csv"
        invalid_output = tmp_path / f"invalid-{field}-{invalid_value.strip()}-out.csv"
        _write_csv(invalid_scan, invalid_rows, list(SCAN_COLUMNS))

        with pytest.raises(ValueError, match="algorithm/version contract"):
            generate_dmepos_anonymous_shortlist(invalid_scan, invalid_output)
        assert not invalid_output.exists()

    legacy_columns = [column for column in SCAN_COLUMNS if column not in SCREEN_ALGORITHM_COLUMNS]
    legacy_rows = [{column: row[column] for column in legacy_columns} for row in rows]
    legacy_scan = tmp_path / "legacy-screen-without-handshake.csv"
    _write_csv(legacy_scan, legacy_rows, legacy_columns)
    with pytest.raises(ValueError, match="unexpected column contract"):
        generate_dmepos_anonymous_shortlist(legacy_scan, tmp_path / "legacy-out.csv")


def test_payment_only_context_is_not_revealed_and_empty_run_retains_metadata(
    tmp_path: Path,
) -> None:
    npi = "1000000003"
    rows = [
        _row(
            year=year,
            npi=npi,
            candidate=False,
            temporal=False,
            tail_years=0,
            full_years=0,
            high_spend=True,
        )
        for year in (2022, 2023, 2024)
    ]
    assert {row["trajectory_route"] for row in rows} == {"high-spend-context"}
    scan = tmp_path / "scan.csv"
    anonymous = tmp_path / "anonymous.csv"
    identities = tmp_path / "identities.csv"
    identified = tmp_path / "identified.csv"
    _write_csv(scan, rows, list(SCAN_COLUMNS))
    _write_csv(identities, [], sorted(IDENTITY_COLUMNS))

    result = generate_dmepos_anonymous_shortlist(scan, anonymous)

    assert result[:3] == (0, 0, 0)
    anonymous_rows = _read_csv(anonymous)
    assert len(anonymous_rows) == 1
    assert anonymous_rows[0]["record_type"] == "run-metadata"
    assert anonymous_rows[0]["minimum_benchmark_exposure"] == "100000"
    assert read_dmepos_anonymous_candidate_npis(anonymous)[0] == []

    join_result = join_dmepos_shortlist_identities(anonymous, identities, identified)
    assert join_result[0] == 0
    identified_rows = _read_csv(identified)
    assert len(identified_rows) == 1
    assert identified_rows[0]["record_type"] == "run-metadata"
    assert identified_rows[0]["supplier_name"] == ""


def test_shortlist_rejects_duplicates_mixed_settings_and_broad_identity_map(
    tmp_path: Path,
) -> None:
    rows, persistent, emerging = _candidate_rows()

    duplicate_scan = tmp_path / "duplicate.csv"
    _write_csv(duplicate_scan, [*rows, rows[-1]], list(SCAN_COLUMNS))
    with pytest.raises(ValueError, match="duplicate year/NPI"):
        generate_dmepos_anonymous_shortlist(duplicate_scan, tmp_path / "duplicate-out.csv")

    mixed = [dict(row) for row in rows]
    mixed[1]["minimum_annual_payment"] = "200000"
    mixed_scan = tmp_path / "mixed.csv"
    _write_csv(mixed_scan, mixed, list(SCAN_COLUMNS))
    with pytest.raises(ValueError, match="prospective 2022-2024"):
        generate_dmepos_anonymous_shortlist(mixed_scan, tmp_path / "mixed-out.csv")

    valid_scan = tmp_path / "valid.csv"
    anonymous = tmp_path / "anonymous.csv"
    identities = tmp_path / "identities.csv"
    _write_csv(valid_scan, rows, list(SCAN_COLUMNS))
    generate_dmepos_anonymous_shortlist(valid_scan, anonymous)
    _write_csv(
        identities,
        [
            _identity(2024, persistent, "Candidate"),
            _identity(2024, emerging, "Emerging"),
            _identity(2024, "1000000999", "Extra"),
        ],
        sorted(IDENTITY_COLUMNS),
    )
    with pytest.raises(ValueError, match="exactly the frozen candidate NPI/source-release pairs"):
        join_dmepos_shortlist_identities(anonymous, identities, tmp_path / "identity-out.csv")

    wrong_release_identities = tmp_path / "wrong-release-identities.csv"
    _write_csv(
        wrong_release_identities,
        [
            _identity(2024, persistent, "Candidate", source_release_id=999),
            _identity(2024, emerging, "Emerging"),
        ],
        sorted(IDENTITY_COLUMNS),
    )
    with pytest.raises(ValueError, match="source release does not match"):
        join_dmepos_shortlist_identities(
            anonymous,
            wrong_release_identities,
            tmp_path / "wrong-release-out.csv",
        )

    mixed_release_scope = tmp_path / "mixed-release-scope.csv"
    anonymous_rows = _read_csv(anonymous)
    anonymous_rows[1]["source_release_id"] = "999"
    _write_csv(mixed_release_scope, anonymous_rows, list(ANONYMOUS_OUTPUT_COLUMNS))
    with pytest.raises(ValueError, match="exactly one 2024 source release"):
        read_dmepos_anonymous_candidate_scope(mixed_release_scope)

    extra_column_identities = tmp_path / "extra-column-identities.csv"
    _write_csv(
        extra_column_identities,
        [_identity(2024, persistent, "Candidate"), _identity(2024, emerging, "Emerging")],
        [*sorted(IDENTITY_COLUMNS), "supplier_address_line1"],
    )
    with pytest.raises(ValueError, match="unexpected columns"):
        join_dmepos_shortlist_identities(
            anonymous,
            extra_column_identities,
            tmp_path / "extra-column-out.csv",
        )


def test_shortlist_rejects_invalid_values_caps_routes_and_overwrite(tmp_path: Path) -> None:
    rows, _, _ = _candidate_rows()
    invalid_scan = tmp_path / "invalid.csv"
    invalid_rows = [dict(row) for row in rows]
    invalid_rows[0]["total_medicare_payment_amount"] = "NaN"
    _write_csv(invalid_scan, invalid_rows, list(SCAN_COLUMNS))
    with pytest.raises(ValueError, match="invalid number"):
        generate_dmepos_anonymous_shortlist(invalid_scan, tmp_path / "invalid-out.csv")

    invalid_route_scan = tmp_path / "invalid-route.csv"
    invalid_route_rows = [dict(row) for row in rows]
    invalid_route_rows[0]["trajectory_route"] = "not-qualified"
    _write_csv(invalid_route_scan, invalid_route_rows, list(SCAN_COLUMNS))
    with pytest.raises(ValueError, match="trajectory route is inconsistent"):
        generate_dmepos_anonymous_shortlist(invalid_route_scan, tmp_path / "invalid-route-out.csv")

    valid_scan = tmp_path / "valid.csv"
    _write_csv(valid_scan, rows, list(SCAN_COLUMNS))
    with pytest.raises(ValueError, match="maximum is 1 bytes"):
        generate_dmepos_anonymous_shortlist(
            valid_scan,
            tmp_path / "cap-out.csv",
            max_input_bytes=1,
        )

    existing = tmp_path / "existing.csv"
    existing.write_text("preserve me", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Refusing to replace"):
        generate_dmepos_anonymous_shortlist(valid_scan, existing)
    assert existing.read_text(encoding="utf-8") == "preserve me"
    assert DEFAULT_MAX_INPUT_BYTES == 64 * 1024 * 1024
    assert DEFAULT_MAX_INPUT_ROWS == 100_000


def test_shortlist_rejects_extra_columns_mixed_releases_and_candidate_overflow(
    tmp_path: Path,
) -> None:
    rows, _, _ = _candidate_rows()

    extra_column_scan = tmp_path / "extra-column.csv"
    _write_csv(extra_column_scan, rows, [*SCAN_COLUMNS, "supplier_display_name"])
    with pytest.raises(ValueError, match="unexpected column contract"):
        generate_dmepos_anonymous_shortlist(
            extra_column_scan,
            tmp_path / "extra-column-out.csv",
        )

    mixed_release_rows = [dict(row) for row in rows]
    mixed_release_rows[-1]["source_release_id"] = "999"
    mixed_release_scan = tmp_path / "mixed-release.csv"
    _write_csv(mixed_release_scan, mixed_release_rows, list(SCAN_COLUMNS))
    with pytest.raises(ValueError, match="mixed source releases for 2024"):
        generate_dmepos_anonymous_shortlist(
            mixed_release_scan,
            tmp_path / "mixed-release-out.csv",
        )

    valid_scan = tmp_path / "valid-for-output-cap.csv"
    _write_csv(valid_scan, rows, list(SCAN_COLUMNS))
    with pytest.raises(ValueError, match="maximum of 1 candidate rows"):
        generate_dmepos_anonymous_shortlist(
            valid_scan,
            tmp_path / "candidate-cap-out.csv",
            max_output_rows=1,
        )


def test_anonymous_reader_revalidates_candidate_run_signature(tmp_path: Path) -> None:
    rows, _, _ = _candidate_rows()
    scan = tmp_path / "scan.csv"
    anonymous = tmp_path / "anonymous.csv"
    altered = tmp_path / "altered-anonymous.csv"
    _write_csv(scan, rows, list(SCAN_COLUMNS))
    generate_dmepos_anonymous_shortlist(scan, anonymous)
    altered_rows = _read_csv(anonymous)
    altered_rows[0]["minimum_annual_payment"] = "99999"
    _write_csv(altered, altered_rows, list(ANONYMOUS_OUTPUT_COLUMNS))

    with pytest.raises(ValueError, match="prospective 2022-2024"):
        read_dmepos_anonymous_candidate_npis(altered)


def test_shortlist_cli_honors_explicit_caps_and_preserves_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "require_research_root", lambda *_a, **_kw: "private-system-of-record")
    rows, _, _ = _candidate_rows()
    scan = tmp_path / "scan.csv"
    expected = tmp_path / "expected.csv"
    actual = tmp_path / "actual.csv"
    _write_csv(scan, rows, list(SCAN_COLUMNS))
    generate_dmepos_anonymous_shortlist(
        scan,
        expected,
        max_input_bytes=scan.stat().st_size,
        max_input_rows=len(rows),
        max_output_bytes=1024 * 1024,
        max_output_rows=2,
    )
    limits = [
        "--max-input-bytes",
        str(scan.stat().st_size),
        "--max-input-rows",
        str(len(rows)),
        "--max-output-bytes",
        str(1024 * 1024),
        "--max-output-rows",
        "2",
    ]
    runner = CliRunner()
    for index in (1, 3, 5, 7):
        too_small = limits.copy()
        too_small[index] = "1" if index == 5 else str(int(too_small[index]) - 1)
        rejected = runner.invoke(
            cli.app, ["dmepos-anonymous-shortlist", str(scan), str(actual), *too_small]
        )
        assert rejected.exit_code == 2, rejected.output
        assert not actual.exists()
    accepted = runner.invoke(
        cli.app, ["dmepos-anonymous-shortlist", str(scan), str(actual), *limits]
    )
    assert accepted.exit_code == 0, accepted.output
    assert actual.read_bytes() == expected.read_bytes()
    assert "2 DMEPOS candidates (1 persistent, 1 emerging" in accepted.output
    for invalid in ("0", "-1"):
        rejected = runner.invoke(
            cli.app,
            [
                "dmepos-anonymous-shortlist",
                str(scan),
                str(tmp_path / "invalid.csv"),
                "--max-input-bytes",
                invalid,
            ],
        )
        assert rejected.exit_code == 2
        assert not (tmp_path / "invalid.csv").exists()
