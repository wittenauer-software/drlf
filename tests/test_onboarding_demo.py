from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from drlf import onboarding_demo
from drlf.onboarding_demo import run_synthetic_part_b_demo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = PROJECT_ROOT / "examples" / "synthetic-part-b-screen" / "scan.csv"
EXPECTED_INPUT_SHA256 = "6680b16dbe10035eb153fd732eb62fcd41e187706ca7d45544555bde883b8f8e"
EXPECTED_SHORTLIST_SHA256 = "b6e93c1908334e31687cbf3934e2c65f82a718f470ba43d9429a5debd36998c4"
EXPECTED_MANIFEST_SHA256 = "4ccc0c422bcf49cc8e318707efef9d1b11bb2c16ccadf53ad4bdfe2c9ed2a31b"


def test_synthetic_demo_is_deterministic_and_auditable(tmp_path: Path) -> None:
    first = run_synthetic_part_b_demo(FIXTURE_PATH, tmp_path / "first")
    second = run_synthetic_part_b_demo(FIXTURE_PATH, tmp_path / "second")

    assert first.selected_rows == 3
    assert first.input_sha256 == second.input_sha256
    assert first.shortlist_sha256 == second.shortlist_sha256
    assert first.manifest_sha256 == second.manifest_sha256
    assert first.input_sha256 == EXPECTED_INPUT_SHA256
    assert first.shortlist_sha256 == EXPECTED_SHORTLIST_SHA256
    assert first.manifest_sha256 == EXPECTED_MANIFEST_SHA256

    with first.shortlist_path.open(encoding="utf-8", newline="") as input_file:
        shortlist = list(csv.DictReader(input_file))
    assert [row["rendering_npi"] for row in shortlist] == [
        "SYNTHETIC-ALPHA",
        "SYNTHETIC-BETA",
        "SYNTHETIC-DELTA",
    ]
    assert [row["selection_priority"] for row in shortlist] == ["1", "4", "7"]
    assert shortlist[0]["selection_reasons"] == (
        "temporal-reach;temporal-repeat;latest-full-reach;latest-full-repeat"
    )
    assert shortlist[-1]["selection_reasons"] == (
        "broader-peer-reach-provisional;broader-peer-repeat-provisional"
    )

    manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    assert manifest["demo"]["pinned_fixture_verified"] is True
    assert manifest["demo"]["input_assurance"] == "pinned-committed-fixture"
    assert manifest["input"] == {
        "bytes": FIXTURE_PATH.stat().st_size,
        "capture": "bounded-single-read-snapshot",
        "data_years": [2023, 2024],
        "file": "scan.csv",
        "latest_year": 2024,
        "latest_year_rows": 4,
        "path": "examples/synthetic-part-b-screen/scan.csv",
        "rows": 5,
        "sha256": first.input_sha256,
    }
    assert manifest["outputs"]["shortlist"]["rows"] == 3
    assert manifest["outputs"]["shortlist"]["sha256"] == first.shortlist_sha256
    assert manifest["method"]["source_file"] == "part_b_shortlist.py"
    assert len(manifest["method"]["source_sha256_lf"]) == 64
    assert "fully synthetic teaching data" in manifest["demo"]["notice"]
    assert "not findings" in manifest["method"]["interpretation"]


def test_synthetic_demo_refuses_overwrite(tmp_path: Path) -> None:
    output_dir = tmp_path / "demo"
    run_synthetic_part_b_demo(FIXTURE_PATH, output_dir)

    with pytest.raises(FileExistsError, match="Refusing to replace"):
        run_synthetic_part_b_demo(FIXTURE_PATH, output_dir)


def test_synthetic_demo_rejects_unpinned_marker_valid_input_by_default(
    tmp_path: Path,
) -> None:
    custom_input = tmp_path / "custom.csv"
    custom_input.write_text(
        FIXTURE_PATH.read_text(encoding="utf-8").replace("6400.00", "6401.00", 1),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not match the pinned committed fixture"):
        run_synthetic_part_b_demo(custom_input, tmp_path / "output")


def test_synthetic_demo_labels_explicit_unpinned_input_as_operator_asserted(
    tmp_path: Path,
) -> None:
    custom_input = tmp_path / "custom.csv"
    custom_input.write_text(
        FIXTURE_PATH.read_text(encoding="utf-8").replace("6400.00", "6401.00", 1),
        encoding="utf-8",
    )

    result = run_synthetic_part_b_demo(
        custom_input,
        tmp_path / "output",
        allow_unpinned_input=True,
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert manifest["demo"]["pinned_fixture_verified"] is False
    assert manifest["demo"]["input_assurance"] == "operator-asserted-unpinned-input"
    assert "does not independently establish" in manifest["demo"]["notice"]
    assert manifest["input"]["path"] == str(custom_input.resolve())


def test_direct_main_does_not_overstate_unpinned_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    custom_input = tmp_path / "operator-reviewed.csv"
    custom_input.write_text(
        FIXTURE_PATH.read_text(encoding="utf-8").replace("6400.00", "6401.00", 1),
        encoding="utf-8",
    )

    exit_code = onboarding_demo.main(
        [
            str(tmp_path / "output"),
            "--input",
            str(custom_input),
            "--allow-unpinned-input",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Wrote 3 shortlist rows" in output
    assert "synthetic shortlist rows" not in output


def test_synthetic_demo_analyzes_the_validated_snapshot_when_source_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changing_input = tmp_path / "changing.csv"
    changing_input.write_bytes(FIXTURE_PATH.read_bytes())
    generate = onboarding_demo.part_b_shortlist.generate_part_b_shortlist

    def change_original_then_generate(snapshot_path: Path, output_path: Path):
        assert snapshot_path.resolve() != changing_input.resolve()
        changing_input.write_text("real_or_unreviewed_data,could_now_be_here\n", encoding="utf-8")
        return generate(snapshot_path, output_path)

    monkeypatch.setattr(
        onboarding_demo.part_b_shortlist,
        "generate_part_b_shortlist",
        change_original_then_generate,
    )

    result = run_synthetic_part_b_demo(changing_input, tmp_path / "output")
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert result.input_sha256 == EXPECTED_INPUT_SHA256
    assert result.shortlist_sha256 == EXPECTED_SHORTLIST_SHA256
    assert manifest["demo"]["pinned_fixture_verified"] is True
    assert manifest["input"]["capture"] == "bounded-single-read-snapshot"


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("synthetic_record", "not_the_marker", "missing 'synthetic_record'"),
        ("true,2023", "false,2023", "not explicitly marked synthetic"),
        ("SYNTHETIC-ALPHA", "1234567890", "non-synthetic identifier"),
    ],
)
def test_synthetic_demo_rejects_input_without_synthetic_guards(
    tmp_path: Path,
    old: str,
    new: str,
    message: str,
) -> None:
    unsafe_input = tmp_path / "unsafe.csv"
    unsafe_input.write_text(
        FIXTURE_PATH.read_text(encoding="utf-8").replace(old, new, 1),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        run_synthetic_part_b_demo(unsafe_input, tmp_path / "output")
