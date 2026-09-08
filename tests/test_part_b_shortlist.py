from __future__ import annotations

import csv
from hashlib import sha256
from pathlib import Path

import pytest

from drlf.analysis.part_b_shortlist import (
    OUTPUT_COLUMNS,
    REQUIRED_COLUMNS,
    generate_part_b_shortlist,
)


def _row(**overrides: str) -> dict[str, str]:
    row = {column: "" for column in REQUIRED_COLUMNS}
    row.update(
        {
            "data_year": "2024",
            "rendering_npi": "1000000001",
            "hcpcs_code": "TSTAB",
            "place_of_service": "O",
            "provider_last_org_name": "Example",
            "provider_first_name": "Allergist",
            "provider_state": "IL",
            "panel_volume_band": "100-199",
            "provider_total_beneficiaries": "111",
            "tested_beneficiaries": "106",
            "panel_penetration": "0.954955",
            "days_per_tested_beneficiary": "1.1",
            "units_per_beneficiary_day": "1.0",
            "peer_evaluation_status": "scoreable",
            "peer_count": "120",
            "reach_empirical_percentile": "0.99",
            "repeat_days_empirical_percentile": "0.5",
            "units_per_day_empirical_percentile": "0.5",
            "reach_outlier_flag": "false",
            "repeat_day_intensity_flag": "false",
            "unit_intensity_flag": "false",
            "triage_route": "none",
            "comparable_years": "3",
            "reach_full_flag_years": "0",
            "repeat_days_full_flag_years": "0",
            "unit_full_flag_years": "0",
            "reach_temporally_confirmed": "false",
            "repeat_days_temporally_confirmed": "false",
            "unit_temporally_confirmed": "false",
            "temporal_triage_route": "not-confirmed",
            "broad_peer_count": "315",
            "broad_reach_empirical_percentile": "100",
            "broad_repeat_days_empirical_percentile": "50",
            "broader_peer_sensitivity_status": "not-applicable",
            "reconstructed_medicare_payment_amount": "5000",
        }
    )
    row.update(overrides)
    return row


def _write_input(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = sorted(REQUIRED_COLUMNS)
    with path.open("x", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def test_generate_part_b_shortlist_is_latest_year_and_rule_driven(tmp_path: Path) -> None:
    input_path = tmp_path / "scan.csv"
    output_path = tmp_path / "shortlist.csv"
    _write_input(
        input_path,
        [
            _row(
                data_year="2023",
                rendering_npi="1000000000",
                reach_temporally_confirmed="true",
            ),
            _row(
                rendering_npi="1000000002",
                broader_peer_sensitivity_status="broader-peer-tail-descriptive",
            ),
            _row(
                rendering_npi="1000000003",
                provider_first_name="Persistent",
                reach_temporally_confirmed="true",
                repeat_days_temporally_confirmed="true",
                panel_penetration="0.81",
            ),
            _row(
                rendering_npi="1000000005",
                provider_first_name="Current",
                reach_outlier_flag="true",
                panel_penetration="0.99",
            ),
        ],
    )

    row_count, artifact_hash = generate_part_b_shortlist(input_path, output_path)

    assert row_count == 3
    assert artifact_hash == sha256(output_path.read_bytes()).hexdigest()
    with output_path.open(encoding="utf-8", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    assert tuple(rows[0]) == OUTPUT_COLUMNS
    assert [row["rendering_npi"] for row in rows] == [
        "1000000003",
        "1000000005",
        "1000000002",
    ]
    assert rows[0]["selection_priority"] == "1"
    assert rows[0]["selection_reasons"] == "temporal-reach;temporal-repeat"
    assert rows[0]["provider_name"] == "Persistent Example"
    assert rows[2]["selection_priority"] == "7"
    assert rows[2]["selection_reasons"] == "broader-peer-reach-provisional"


def test_generate_part_b_shortlist_validates_input_and_refuses_overwrite(
    tmp_path: Path,
) -> None:
    incomplete = tmp_path / "incomplete.csv"
    incomplete.write_text("data_year,rendering_npi\n2024,1000000001\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing columns"):
        generate_part_b_shortlist(incomplete, tmp_path / "unused.csv")

    malformed = tmp_path / "malformed.csv"
    _write_input(malformed, [_row(reach_outlier_flag="maybe")])
    with pytest.raises(ValueError, match="invalid boolean"):
        generate_part_b_shortlist(malformed, tmp_path / "malformed-output.csv")

    complete = tmp_path / "complete.csv"
    output_path = tmp_path / "shortlist.csv"
    _write_input(complete, [_row()])
    output_path.write_text("already here", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Refusing to replace"):
        generate_part_b_shortlist(complete, output_path)
