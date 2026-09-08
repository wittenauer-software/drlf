from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from drlf import cli


@pytest.fixture(autouse=True)
def _authorize_cli_unit_test_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli,
        "require_research_root",
        lambda *_args, **_kwargs: "private-system-of-record",
    )


def test_dmepos_service_code_freeze_cli_routes_defaults(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    input_path = tmp_path / "registered.csv"
    input_path.write_text("registered\n", encoding="utf-8")
    output_path = tmp_path / "frozen.csv"
    observed: dict[str, object] = {}

    def fake_freeze(input_file: Path, output_file: Path, **kwargs: object) -> tuple[int, int, str]:
        observed["call"] = (input_file, output_file, kwargs)
        return 7, 42, "a" * 64

    monkeypatch.setattr(cli, "freeze_dmepos_service_codes", fake_freeze)
    result = CliRunner().invoke(
        cli.app,
        [
            "dmepos-service-code-freeze",
            str(input_path),
            str(output_path),
            "--focus-year",
            "2024",
        ],
    )

    assert result.exit_code == 0, result.output
    assert observed["call"] == (
        input_path,
        output_path,
        {
            "focus_year": 2024,
            "top_codes_per_candidate": 3,
            "min_cohort_code_payment": Decimal("100000"),
            "target_candidate_visible_payment_share": Decimal("0.90"),
            "min_visible_summary_payment_coverage": Decimal("0.80"),
            "max_selected_codes": 25,
            "max_input_bytes": 16 * 1024 * 1024,
            "max_input_rows": 10_000,
            "max_output_bytes": 1024 * 1024,
        },
    )
    assert "Froze 7 DMEPOS HCPCS codes from 42 complete decomposition rows" in result.output


def test_dmepos_service_code_freeze_cli_rejects_bad_decimal(tmp_path: Path) -> None:
    input_path = tmp_path / "registered.csv"
    input_path.write_text("registered\n", encoding="utf-8")
    result = CliRunner().invoke(
        cli.app,
        [
            "dmepos-service-code-freeze",
            str(input_path),
            str(tmp_path / "frozen.csv"),
            "--focus-year",
            "2024",
            "--min-cohort-code-payment",
            "not-a-decimal",
        ],
    )

    assert result.exit_code == 2
    assert "must be decimals" in result.output


def test_dmepos_materiality_code_freeze_cli_routes_distinct_defaults(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    input_path = tmp_path / "registered.csv"
    input_path.write_text("registered\n", encoding="utf-8")
    output_path = tmp_path / "materiality.csv"
    observed: dict[str, object] = {}

    def fake_freeze(input_file: Path, output_file: Path, **kwargs: object) -> tuple[int, int, str]:
        observed["call"] = (input_file, output_file, kwargs)
        return 5, 42, "b" * 64

    monkeypatch.setattr(cli, "freeze_dmepos_materiality_codes", fake_freeze)
    result = CliRunner().invoke(
        cli.app,
        [
            "dmepos-materiality-code-freeze",
            str(input_path),
            str(output_path),
            "--focus-year",
            "2024",
        ],
    )

    assert result.exit_code == 0, result.output
    assert observed["call"] == (
        input_path,
        output_path,
        {
            "focus_year": 2024,
            "observed_payment_threshold": Decimal("100000"),
            "top_codes_per_candidate": 3,
            "min_cohort_code_payment": Decimal("100000"),
            "target_candidate_visible_payment_share": Decimal("0.90"),
            "min_visible_summary_payment_coverage": Decimal("0.80"),
            "max_selected_codes": 25,
            "max_materiality_codes": 100,
            "max_input_bytes": 16 * 1024 * 1024,
            "max_input_rows": 10_000,
            "max_output_bytes": 1024 * 1024,
        },
    )
    assert "Froze 5 materiality-complete DMEPOS HCPCS codes" in result.output


def test_dmepos_materiality_code_freeze_cli_rejects_bad_threshold(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "registered.csv"
    input_path.write_text("registered\n", encoding="utf-8")
    result = CliRunner().invoke(
        cli.app,
        [
            "dmepos-materiality-code-freeze",
            str(input_path),
            str(tmp_path / "frozen.csv"),
            "--focus-year",
            "2024",
            "--observed-payment-threshold",
            "not-a-decimal",
        ],
    )

    assert result.exit_code == 2
    assert "must be decimals" in result.output
