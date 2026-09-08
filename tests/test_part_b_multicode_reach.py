from __future__ import annotations

import csv
from hashlib import sha256
from pathlib import Path

import pytest
from typer.testing import CliRunner

from drlf import cli
from drlf.analysis.part_b_multicode_reach import (
    OUTPUT_COLUMNS,
    REQUIRED_COLUMNS,
    SCREEN_CAVEAT,
    generate_part_b_multicode_reach_screen,
)
from drlf.cli import app


@pytest.fixture(autouse=True)
def _authorize_cli_unit_test_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli,
        "require_research_root",
        lambda *_args, **_kwargs: "private-system-of-record",
    )


def _row(**overrides: str) -> dict[str, str]:
    row = {column: "" for column in REQUIRED_COLUMNS}
    row.update(
        {
            "data_year": "2024",
            "rendering_npi": "1000000001",
            "provider_type": "Allergy/ Immunology",
            "entity_code": "I",
            "place_of_service": "O",
            "peer_evaluation_status": "scoreable",
            "provider_last_org_name": "Example",
            "provider_first_name": "Allergist",
            "provider_city": "Springfield",
            "provider_state": "IL",
            "hcpcs_code": "TSTAA",
            "hcpcs_description": "Synthetic test service A",
            "provider_total_beneficiaries": "200",
            "tested_beneficiaries": "100",
            "peer_count": "150",
            "panel_penetration": "0.5",
            "days_per_tested_beneficiary": "2.0",
            "reach_empirical_percentile": "95",
            "reconstructed_medicare_payment_amount": "300.00",
        }
    )
    row.update(overrides)
    return row


def _write_input(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("x", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=sorted(REQUIRED_COLUMNS),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _qualifying_rows() -> list[dict[str, str]]:
    return [
        _row(
            data_year="2022",
            reach_empirical_percentile="80",
            reconstructed_medicare_payment_amount="100.00",
        ),
        _row(
            data_year="2023",
            reach_empirical_percentile="95",
            reconstructed_medicare_payment_amount="200.00",
        ),
        _row(),
        _row(
            data_year="2022",
            hcpcs_code="TSTAD",
            hcpcs_description="Synthetic test service D",
            reach_empirical_percentile="91",
            reconstructed_medicare_payment_amount="10.00",
        ),
        _row(
            data_year="2023",
            hcpcs_code="TSTAD",
            hcpcs_description="Synthetic test service D",
            reach_empirical_percentile="92",
            reconstructed_medicare_payment_amount="20.00",
        ),
        _row(
            hcpcs_code="TSTAD",
            hcpcs_description="Synthetic test service D",
            reach_empirical_percentile="93",
            provider_total_beneficiaries="180",
            tested_beneficiaries="81",
            panel_penetration="0.45",
            days_per_tested_beneficiary="1.75",
            reconstructed_medicare_payment_amount="30.00",
        ),
        # Reaches the threshold in two earlier years but not in the latest year.
        _row(data_year="2022", hcpcs_code="TSTAB", reach_empirical_percentile="99"),
        _row(data_year="2023", hcpcs_code="TSTAB", reach_empirical_percentile="99"),
        _row(hcpcs_code="TSTAB", reach_empirical_percentile="89"),
        # A different POS is a different stable group and has only one qualifying code.
        _row(place_of_service="F", reach_empirical_percentile="99"),
        _row(data_year="2023", place_of_service="F", reach_empirical_percentile="99"),
        # Blank percentiles are valid unscored rows but cannot qualify.
        _row(hcpcs_code="TSTAC", reach_empirical_percentile=""),
    ]


def test_multicode_screen_selects_stable_groups_and_is_deterministic(tmp_path: Path) -> None:
    first_input = tmp_path / "scan-first.csv"
    second_input = tmp_path / "scan-second.csv"
    first_output = tmp_path / "screen-first.csv"
    second_output = tmp_path / "screen-second.csv"
    rows = _qualifying_rows()
    _write_input(first_input, rows)
    _write_input(second_input, list(reversed(rows)))

    first_count, first_groups, first_npis, first_hash = generate_part_b_multicode_reach_screen(
        first_input,
        first_output,
        reach_percentile_threshold="90.0",
        minimum_tail_years=2,
        minimum_qualifying_codes=2,
    )
    second_count, second_groups, second_npis, second_hash = generate_part_b_multicode_reach_screen(
        second_input,
        second_output,
        reach_percentile_threshold="90",
        minimum_tail_years=2,
        minimum_qualifying_codes=2,
    )

    assert first_count == second_count == 2
    assert first_groups == second_groups == 1
    assert first_npis == second_npis == 1
    assert first_hash == sha256(first_output.read_bytes()).hexdigest()
    assert second_hash == sha256(second_output.read_bytes()).hexdigest()
    assert first_hash != second_hash
    with first_output.open(encoding="utf-8", newline="") as input_file:
        output_rows = list(csv.DictReader(input_file))
    with second_output.open(encoding="utf-8", newline="") as input_file:
        reverse_input_rows = list(csv.DictReader(input_file))
    assert output_rows[0]["input_sha256"] == sha256(first_input.read_bytes()).hexdigest()
    assert reverse_input_rows[0]["input_sha256"] == sha256(second_input.read_bytes()).hexdigest()
    for row in [*output_rows, *reverse_input_rows]:
        row["input_sha256"] = ""
    assert output_rows == reverse_input_rows
    assert tuple(output_rows[0]) == OUTPUT_COLUMNS
    assert output_rows[0]["record_type"] == "candidate-code"
    assert [row["hcpcs_code"] for row in output_rows] == ["TSTAA", "TSTAD"]
    assert output_rows[0]["qualifying_code_count"] == "2"
    assert output_rows[0]["observed_years"] == "2022;2023;2024"
    assert output_rows[0]["reach_tail_years"] == "2023;2024"
    assert output_rows[0]["minimum_observed_reach_empirical_percentile"] == "80"
    assert output_rows[0]["minimum_tail_reach_empirical_percentile"] == "95"
    assert output_rows[0]["latest_reach_empirical_percentile"] == "95"
    assert output_rows[0]["minimum_tail_peer_count"] == "150"
    assert output_rows[0]["latest_peer_count"] == "150"
    assert output_rows[0]["latest_provider_total_beneficiaries"] == "200"
    assert output_rows[0]["latest_tested_beneficiaries"] == "100"
    assert output_rows[0]["latest_panel_penetration"] == "0.5"
    assert output_rows[0]["latest_days_per_tested_beneficiary"] == "2"
    assert output_rows[0]["code_reconstructed_medicare_payment_amount_observed_years"] == "600"
    assert output_rows[0]["reach_percentile_threshold"] == "90"
    assert output_rows[0]["minimum_tail_years"] == "2"
    assert output_rows[0]["minimum_qualifying_codes"] == "2"
    assert output_rows[0]["eligibility_requirement"] == "peer_evaluation_status=scoreable"
    assert output_rows[0]["input_rows"] == str(len(rows))
    assert output_rows[0]["max_input_bytes"] == str(32 * 1024 * 1024)
    assert output_rows[0]["max_input_rows"] == "25000"
    assert output_rows[0]["max_output_bytes"] == str(32 * 1024 * 1024)
    assert int(output_rows[0]["input_bytes"]) == first_input.stat().st_size
    assert output_rows[0]["latest_data_year"] == "2024"
    assert output_rows[0]["screen_caveat"] == SCREEN_CAVEAT


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"panel_penetration": "NaN"}, "non-finite number"),
        ({"reach_empirical_percentile": "101"}, "out-of-range"),
        ({"provider_total_beneficiaries": "99", "tested_beneficiaries": "100"}, "greater"),
    ],
)
def test_multicode_screen_rejects_invalid_numeric_values(
    tmp_path: Path, override: dict[str, str], message: str
) -> None:
    input_path = tmp_path / "invalid.csv"
    _write_input(input_path, [_row(**override)])

    with pytest.raises(ValueError, match=message):
        generate_part_b_multicode_reach_screen(
            input_path,
            tmp_path / "unused.csv",
            reach_percentile_threshold="90",
            minimum_tail_years=1,
            minimum_qualifying_codes=1,
        )


def test_multicode_screen_rejects_incomplete_duplicate_and_existing_output(
    tmp_path: Path,
) -> None:
    incomplete = tmp_path / "incomplete.csv"
    incomplete.write_text("data_year,rendering_npi\n2024,1000000001\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing columns"):
        generate_part_b_multicode_reach_screen(
            incomplete,
            tmp_path / "unused.csv",
            reach_percentile_threshold="90",
            minimum_tail_years=1,
            minimum_qualifying_codes=1,
        )

    duplicate = tmp_path / "duplicate.csv"
    _write_input(duplicate, [_row(), _row()])
    with pytest.raises(ValueError, match="duplicate year/NPI"):
        generate_part_b_multicode_reach_screen(
            duplicate,
            tmp_path / "duplicate-output.csv",
            reach_percentile_threshold="90",
            minimum_tail_years=1,
            minimum_qualifying_codes=1,
        )

    complete = tmp_path / "complete.csv"
    output = tmp_path / "screen.csv"
    _write_input(complete, [_row()])
    output.write_text("preserve me", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Refusing to replace"):
        generate_part_b_multicode_reach_screen(
            complete,
            output,
            reach_percentile_threshold="90",
            minimum_tail_years=1,
            minimum_qualifying_codes=1,
        )
    assert output.read_text(encoding="utf-8") == "preserve me"


def test_multicode_screen_does_not_promote_insufficient_peer_percentiles(tmp_path: Path) -> None:
    input_path = tmp_path / "scan.csv"
    output_path = tmp_path / "screen.csv"
    rows = _qualifying_rows()
    for row in rows:
        if row["hcpcs_code"] == "TSTAD":
            row["peer_evaluation_status"] = "insufficient-peer-descriptive"
    _write_input(input_path, rows)

    row_count, group_count, npi_count, _ = generate_part_b_multicode_reach_screen(
        input_path,
        output_path,
        reach_percentile_threshold="90",
        minimum_tail_years=2,
        minimum_qualifying_codes=2,
    )

    assert row_count == 0
    assert group_count == 0
    assert npi_count == 0
    [metadata] = csv.DictReader(output_path.open(encoding="utf-8", newline=""))
    assert metadata["record_type"] == "run-metadata"
    assert metadata["reach_percentile_threshold"] == "90"
    assert metadata["minimum_tail_years"] == "2"
    assert metadata["minimum_qualifying_codes"] == "2"
    assert metadata["input_sha256"] == sha256(input_path.read_bytes()).hexdigest()
    assert metadata["rendering_npi"] == ""


def test_multicode_screen_requires_every_counted_tail_year_to_be_scoreable(tmp_path: Path) -> None:
    input_path = tmp_path / "scan.csv"
    output_path = tmp_path / "screen.csv"
    rows = _qualifying_rows()
    for row in rows:
        if row["data_year"] == "2023":
            row["peer_evaluation_status"] = "insufficient-peer-descriptive"
    _write_input(input_path, rows)

    row_count, group_count, npi_count, _ = generate_part_b_multicode_reach_screen(
        input_path,
        output_path,
        reach_percentile_threshold="90",
        minimum_tail_years=3,
        minimum_qualifying_codes=2,
    )

    assert (row_count, group_count, npi_count) == (0, 0, 0)


def test_multicode_screen_enforces_input_resource_caps(tmp_path: Path) -> None:
    input_path = tmp_path / "scan.csv"
    _write_input(input_path, _qualifying_rows())

    with pytest.raises(ValueError, match="maximum is 1 bytes"):
        generate_part_b_multicode_reach_screen(
            input_path,
            tmp_path / "byte-output.csv",
            reach_percentile_threshold="90",
            minimum_tail_years=2,
            minimum_qualifying_codes=2,
            max_input_bytes=1,
        )

    with pytest.raises(ValueError, match="exceeds maximum of 1 data rows"):
        generate_part_b_multicode_reach_screen(
            input_path,
            tmp_path / "row-output.csv",
            reach_percentile_threshold="90",
            minimum_tail_years=2,
            minimum_qualifying_codes=2,
            max_input_rows=1,
        )


def test_multicode_screen_enforces_output_cap_and_removes_partial_file(tmp_path: Path) -> None:
    input_path = tmp_path / "scan.csv"
    output_path = tmp_path / "screen.csv"
    _write_input(input_path, _qualifying_rows())

    with pytest.raises(ValueError, match="output would exceed maximum of 1 bytes"):
        generate_part_b_multicode_reach_screen(
            input_path,
            output_path,
            reach_percentile_threshold="90",
            minimum_tail_years=2,
            minimum_qualifying_codes=2,
            max_output_bytes=1,
        )

    assert not output_path.exists()


def test_multicode_screen_rejects_invalid_hcpcs_and_duplicate_canonical_grain(
    tmp_path: Path,
) -> None:
    invalid = tmp_path / "invalid-hcpcs.csv"
    _write_input(invalid, [_row(hcpcs_code="TOO-LONG")])
    with pytest.raises(ValueError, match="invalid hcpcs_code"):
        generate_part_b_multicode_reach_screen(
            invalid,
            tmp_path / "invalid-output.csv",
            reach_percentile_threshold="90",
            minimum_tail_years=1,
            minimum_qualifying_codes=1,
        )


def test_multicode_screen_formats_extreme_decimal_exponents_without_expansion(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "scan.csv"
    output_path = tmp_path / "screen.csv"
    _write_input(input_path, _qualifying_rows())

    generate_part_b_multicode_reach_screen(
        input_path,
        output_path,
        reach_percentile_threshold="1e-100000000",
        minimum_tail_years=2,
        minimum_qualifying_codes=2,
    )

    assert output_path.stat().st_size < 100_000
    with output_path.open(encoding="utf-8", newline="") as input_file:
        [first, *_] = csv.DictReader(input_file)
    assert first["reach_percentile_threshold"] == "1E-100000000"

    duplicate = tmp_path / "descriptive-attribute-duplicate.csv"
    _write_input(
        duplicate,
        [
            _row(),
            _row(provider_type="Pulmonary Disease", entity_code="O"),
        ],
    )
    with pytest.raises(ValueError, match="duplicate year/NPI/HCPCS/POS grain"):
        generate_part_b_multicode_reach_screen(
            duplicate,
            tmp_path / "duplicate-output.csv",
            reach_percentile_threshold="90",
            minimum_tail_years=1,
            minimum_qualifying_codes=1,
        )


def test_multicode_screen_cli_requires_explicit_thresholds(tmp_path: Path) -> None:
    input_path = tmp_path / "scan.csv"
    output_path = tmp_path / "screen.csv"
    _write_input(input_path, _qualifying_rows())
    runner = CliRunner()

    base = ["part-b-multicode-reach-screen", str(input_path), str(output_path)]
    missing_threshold = runner.invoke(app, [*base, "--min-years", "2", "--min-codes", "2"])
    assert missing_threshold.exit_code == 2
    assert "--reach-percentile-threshold" in missing_threshold.output

    missing_years = runner.invoke(
        app,
        [*base, "--reach-percentile-threshold", "90", "--min-codes", "2"],
    )
    assert missing_years.exit_code == 2
    assert "--min-years" in missing_years.output

    missing_codes = runner.invoke(
        app,
        [*base, "--reach-percentile-threshold", "90", "--min-years", "2"],
    )
    assert missing_codes.exit_code == 2
    assert "--min-codes" in missing_codes.output

    result = runner.invoke(
        app,
        [
            *base,
            "--reach-percentile-threshold",
            "90",
            "--min-years",
            "2",
            "--min-codes",
            "2",
        ],
    )
    assert result.exit_code == 0
    assert "Wrote 2 exploratory code rows across 1 stable group and 1 unique NPI" in result.output
    assert output_path.exists()
