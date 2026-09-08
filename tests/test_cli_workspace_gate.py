from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from drlf import cli
from drlf.workspace_lifecycle import DISTRIBUTION_MARKER


def _write_public_marker(root: Path) -> None:
    marker = root / DISTRIBUTION_MARKER
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "distribution_kind": "public-baseline",
                "product": "DRLF",
            }
        ),
        encoding="utf-8",
    )


def test_every_cli_command_has_exactly_one_workspace_classification() -> None:
    registered = {command.name for command in cli.app.registered_commands}

    assert cli.PUBLIC_BASELINE_COMMANDS.isdisjoint(cli.RESEARCH_ROOT_COMMANDS)
    assert cli.PUBLIC_BASELINE_COMMANDS | cli.RESEARCH_ROOT_COMMANDS == registered


def test_real_data_command_is_blocked_in_public_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_public_marker(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli.app, ["part-d-first-pass"])

    assert result.exit_code == 2
    normalized = " ".join(result.output.split())
    assert "disabled in a public DRLF baseline" in normalized
    assert "separate private research workspace" in normalized


def test_unclassified_command_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_public_marker(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "RESEARCH_ROOT_COMMANDS",
        cli.RESEARCH_ROOT_COMMANDS - {"part-d-first-pass"},
    )

    result = CliRunner().invoke(cli.app, ["part-d-first-pass"])

    assert result.exit_code == 2
    normalized = " ".join(result.output.split())
    assert "classification; refusing to run" in normalized


def test_public_safe_command_help_remains_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_public_marker(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli.app, ["onboarding-demo", "--help"])

    assert result.exit_code == 0, result.output
    normalized = " ".join(result.output.split())
    assert "offline, deterministic synthetic Part B screening example" in normalized
