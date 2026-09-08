from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from drlf import cli
from drlf.analysis.open_payments_organization import (
    ANALYSIS_SPECIFICATIONS,
    INCOMPLETE_FILENAME,
    SUCCESS_FILENAME,
    OrganizationAnalysisArtifact,
    OrganizationAnalysisResult,
    OrganizationSourceRelease,
    _validate_release_manifest,
    run_open_payments_organization_analysis,
)


def _repository(tmp_path: Path) -> tuple[Path, Path]:
    case_directory = tmp_path / "research" / "cases" / "CASE-9999-example"
    case_directory.mkdir(parents=True)
    for query_filename in {item.query_filename for item in ANALYSIS_SPECIFICATIONS}:
        query_path = tmp_path / "sql" / "analysis" / "open_payments" / query_filename
        query_path.parent.mkdir(parents=True, exist_ok=True)
        query_path.write_text("select 1\n", encoding="utf-8")
    return case_directory, case_directory / "analysis" / "open-payments-example"


def _release_validator(
    _database_url: str,
    *,
    source_release_ids: tuple[int, ...],
    years: tuple[int, ...],
    **_kwargs: Any,
) -> tuple[OrganizationSourceRelease, ...]:
    return tuple(
        OrganizationSourceRelease(
            source_release_id=release_id,
            data_year=year,
            manifest_path=f"research/data-manifests/release-{release_id}.json",
            manifest_sha256=f"{release_id:064x}",
        )
        for release_id, year in zip(source_release_ids, years, strict=True)
    )


def test_batch_runs_four_outputs_with_one_commit_and_shared_lineage(tmp_path: Path) -> None:
    _case_directory, output_dir = _repository(tmp_path)
    commit_calls: list[Path] = []
    runner_calls: list[dict[str, Any]] = []

    def commit_resolver(root: Path) -> str:
        commit_calls.append(root)
        assert not output_dir.exists()
        return "a" * 40

    def analysis_runner(
        _database_url: str,
        query_path: Path,
        output_path: Path,
        **kwargs: Any,
    ) -> tuple[int, int, str]:
        runner_calls.append(
            {"query_path": query_path, "output_path": output_path, **kwargs}
        )
        output_path.write_text("value\n1\n", encoding="utf-8", newline="\n")
        return (
            len(runner_calls),
            len(runner_calls) * 10,
            sha256(output_path.read_bytes()).hexdigest(),
        )

    result = run_open_payments_organization_analysis(
        "unused",
        output_dir,
        case_id="CASE-9999",
        organization_id=" SYNTHETIC-OPEN-PAYMENTS-ORG ",
        from_year=2023,
        to_year=2024,
        source_release_ids=[18, 17],
        payment_nature=" Consulting Fee ",
        repository_root=tmp_path,
        commit_resolver=commit_resolver,
        release_validator=_release_validator,
        analysis_runner=analysis_runner,
    )

    assert commit_calls == [tmp_path.resolve()]
    assert result.code_commit == "a" * 40
    assert result.source_release_ids == (17, 18)
    assert [item.path.name for item in result.artifacts] == [
        "open-payments-organization-annual-summary.csv",
        "open-payments-organization-recipient-panel.csv",
        "open-payments-organization-roster-all.csv",
        "open-payments-organization-roster-npi-only.csv",
    ]
    assert [item.analysis_run_id for item in result.artifacts] == [1, 2, 3, 4]
    assert [item.rows for item in result.artifacts] == [10, 20, 30, 40]
    assert not (output_dir / INCOMPLETE_FILENAME).exists()
    assert result.manifest_path == output_dir / SUCCESS_FILENAME
    bundle = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert bundle["status"] == "succeeded"
    assert bundle["code_commit"] == "a" * 40
    assert [item["source_release_id"] for item in bundle["source_releases"]] == [17, 18]
    assert [item["analysis_run_id"] for item in bundle["artifacts"]] == [1, 2, 3, 4]
    assert bundle["artifacts"][2]["parameters"]["identifier_scope"] == "all"
    assert bundle["artifacts"][0]["query_sha256"] == sha256(
        runner_calls[0]["query_path"].read_bytes()
    ).hexdigest()

    assert all(call["code_commit"] == "a" * 40 for call in runner_calls)
    assert all(call["source_release_ids"] == [17, 18] for call in runner_calls)
    assert all("source_release_ids" not in call["parameters"] for call in runner_calls)
    assert runner_calls[0]["parameters"] == {
        "organization_id": "SYNTHETIC-OPEN-PAYMENTS-ORG",
        "from_year": "2023",
        "to_year": "2024",
    }
    assert runner_calls[1]["parameters"] == runner_calls[0]["parameters"]
    assert runner_calls[2]["parameters"]["identifier_scope"] == "all"
    assert runner_calls[3]["parameters"]["identifier_scope"] == "npi-only"
    assert runner_calls[2]["parameters"]["payment_nature"] == "Consulting Fee"
    assert runner_calls[2]["query_path"].name == "organization_roster_transitions.sql"
    assert runner_calls[3]["query_path"].name == "organization_roster_transitions.sql"


def test_batch_failure_retains_partial_outputs_and_incomplete_state(tmp_path: Path) -> None:
    _case_directory, output_dir = _repository(tmp_path)
    attempts = 0
    commit_calls = 0

    def commit_resolver(_root: Path) -> str:
        nonlocal commit_calls
        commit_calls += 1
        return "b" * 40

    def failing_runner(
        _database_url: str,
        _query_path: Path,
        output_path: Path,
        **_kwargs: Any,
    ) -> tuple[int, int, str]:
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise RuntimeError("deliberate batch failure")
        output_path.write_text("value\n1\n", encoding="utf-8", newline="\n")
        return attempts, 1, sha256(output_path.read_bytes()).hexdigest()

    with pytest.raises(RuntimeError, match="deliberate batch failure"):
        run_open_payments_organization_analysis(
            "unused",
            output_dir,
            case_id="CASE-9999",
            organization_id="SYNTHETIC-OPEN-PAYMENTS-ORG",
            from_year=2024,
            to_year=2024,
            source_release_ids=[17],
            payment_nature="Consulting Fee",
            repository_root=tmp_path,
            commit_resolver=commit_resolver,
            release_validator=_release_validator,
            analysis_runner=failing_runner,
        )

    state = json.loads((output_dir / INCOMPLETE_FILENAME).read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert state["error"] == "RuntimeError: deliberate batch failure"
    assert len(state["completed_analyses"]) == 1
    assert state["completed_analyses"][0]["analysis_run_id"] == 1
    assert (output_dir / "open-payments-organization-annual-summary.csv").is_file()
    assert not (output_dir / "open-payments-organization-recipient-panel.csv").exists()
    assert not (output_dir / SUCCESS_FILENAME).exists()
    assert attempts == 2
    assert commit_calls == 1

    with pytest.raises(FileExistsError, match="Refusing to replace"):
        run_open_payments_organization_analysis(
            "unused",
            output_dir,
            case_id="CASE-9999",
            organization_id="SYNTHETIC-OPEN-PAYMENTS-ORG",
            from_year=2024,
            to_year=2024,
            source_release_ids=[17],
            payment_nature="Consulting Fee",
            repository_root=tmp_path,
            commit_resolver=commit_resolver,
            release_validator=_release_validator,
            analysis_runner=failing_runner,
        )
    assert commit_calls == 1


def test_batch_validates_case_output_and_release_coverage_before_commit(tmp_path: Path) -> None:
    case_directory, output_dir = _repository(tmp_path)
    commit_calls = 0

    def commit_resolver(_root: Path) -> str:
        nonlocal commit_calls
        commit_calls += 1
        return "c" * 40

    with pytest.raises(ValueError, match="one source release ID per requested year"):
        run_open_payments_organization_analysis(
            "unused",
            output_dir,
            case_id="CASE-9999",
            organization_id="SYNTHETIC-OPEN-PAYMENTS-ORG",
            from_year=2023,
            to_year=2024,
            source_release_ids=[17],
            payment_nature="Consulting Fee",
            repository_root=tmp_path,
            commit_resolver=commit_resolver,
            release_validator=_release_validator,
        )

    with pytest.raises(ValueError, match="inside the CASE-9999 case workspace"):
        run_open_payments_organization_analysis(
            "unused",
            tmp_path / "outside",
            case_id="CASE-9999",
            organization_id="SYNTHETIC-OPEN-PAYMENTS-ORG",
            from_year=2024,
            to_year=2024,
            source_release_ids=[17],
            payment_nature="Consulting Fee",
            repository_root=tmp_path,
            commit_resolver=commit_resolver,
            release_validator=_release_validator,
        )

    existing_target = output_dir / "open-payments-organization-annual-summary.csv"
    existing_target.parent.mkdir(parents=True)
    existing_target.write_text("do not replace\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Refusing to replace"):
        run_open_payments_organization_analysis(
            "unused",
            output_dir,
            case_id="CASE-9999",
            organization_id="SYNTHETIC-OPEN-PAYMENTS-ORG",
            from_year=2024,
            to_year=2024,
            source_release_ids=[17],
            payment_nature="Consulting Fee",
            repository_root=tmp_path,
            commit_resolver=commit_resolver,
            release_validator=_release_validator,
        )

    assert case_directory.is_dir()
    assert commit_calls == 0


def test_release_manifest_requires_exact_organization_only_filter(tmp_path: Path) -> None:
    manifest_path = tmp_path / "research" / "data-manifests" / "open-payments.json"
    manifest_path.parent.mkdir(parents=True)
    manifest = {
        "status": "complete",
        "dataset": {
            "slug": "open-payments-general-payments",
            "data_year": 2024,
        },
        "retrieval": {
            "parameters": {
                "exact_filters": {
                    "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_ID": (
                        "SYNTHETIC-OPEN-PAYMENTS-ORG"
                    )
                }
            }
        },
        "validation": {"rows": 0},
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_hash = sha256(manifest_path.read_bytes()).hexdigest()

    release = _validate_release_manifest(
        tmp_path,
        source_release_id=17,
        data_year=2024,
        manifest_path=manifest_path.relative_to(tmp_path).as_posix(),
        manifest_sha256=manifest_hash,
        organization_id="SYNTHETIC-OPEN-PAYMENTS-ORG",
    )
    assert release.data_year == 2024

    manifest["retrieval"]["parameters"]["exact_filters"]["covered_recipient_npi"] = (
        "1234567890"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    narrowed_hash = sha256(manifest_path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="must use only the exact paying-entity filter"):
        _validate_release_manifest(
            tmp_path,
            source_release_id=17,
            data_year=2024,
            manifest_path=manifest_path.relative_to(tmp_path).as_posix(),
            manifest_sha256=narrowed_hash,
            organization_id="SYNTHETIC-OPEN-PAYMENTS-ORG",
        )


def test_release_manifest_rejects_hash_and_year_mismatches(tmp_path: Path) -> None:
    manifest_path = tmp_path / "research" / "data-manifests" / "open-payments.json"
    manifest_path.parent.mkdir(parents=True)
    manifest = {
        "status": "complete",
        "dataset": {
            "slug": "open-payments-general-payments",
            "data_year": 2023,
        },
        "retrieval": {
            "parameters": {
                "exact_filters": {
                    "applicable_manufacturer_or_applicable_gpo_making_payment_id": "other-org"
                }
            }
        },
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_relative = manifest_path.relative_to(tmp_path).as_posix()

    with pytest.raises(ValueError, match="hash does not match"):
        _validate_release_manifest(
            tmp_path,
            source_release_id=17,
            data_year=2024,
            manifest_path=manifest_relative,
            manifest_sha256="0" * 64,
            organization_id="SYNTHETIC-OPEN-PAYMENTS-ORG",
        )

    manifest_hash = sha256(manifest_path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="years do not match"):
        _validate_release_manifest(
            tmp_path,
            source_release_id=17,
            data_year=2024,
            manifest_path=manifest_relative,
            manifest_sha256=manifest_hash,
            organization_id="SYNTHETIC-OPEN-PAYMENTS-ORG",
        )


def test_cli_reports_every_batch_run_id_count_and_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, Any] = {}
    output_dir = Path("research/cases/CASE-9999-example/analysis/open-payments-example")
    artifacts = tuple(
        OrganizationAnalysisArtifact(
            name=specification.name,
            path=output_dir / specification.output_filename,
            query_path=Path("sql/analysis/open_payments") / specification.query_filename,
            query_sha256=f"{index + 10:064x}",
            parameters={"organization_id": "SYNTHETIC-OPEN-PAYMENTS-ORG"},
            analysis_run_id=index,
            rows=index * 5,
            sha256=f"{index:064x}",
        )
        for index, specification in enumerate(ANALYSIS_SPECIFICATIONS, start=1)
    )

    class _Secret:
        def get_secret_value(self) -> str:
            return "postgresql://unused"

    def fake_batch(database_url: str, output: Path, **kwargs: Any) -> OrganizationAnalysisResult:
        observed.update({"database_url": database_url, "output": output, **kwargs})
        return OrganizationAnalysisResult(
            output_dir=output,
            manifest_path=output / SUCCESS_FILENAME,
            code_commit="d" * 40,
            source_release_ids=(17, 18),
            artifacts=artifacts,
        )

    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: SimpleNamespace(database_url=_Secret()),
    )
    monkeypatch.setattr(
        cli,
        "require_research_root",
        lambda *_args, **_kwargs: "private-system-of-record",
    )
    monkeypatch.setattr(cli, "run_open_payments_organization_analysis", fake_batch)

    result = CliRunner().invoke(
        cli.app,
        [
            "open-payments-organization-analysis",
            str(output_dir),
            "--case-id",
            "CASE-9999",
            "--organization-id",
            "SYNTHETIC-OPEN-PAYMENTS-ORG",
            "--from-year",
            "2023",
            "--to-year",
            "2024",
            "--source-release-id",
            "18",
            "--source-release-id",
            "17",
            "--payment-nature",
            "Consulting Fee",
        ],
    )

    assert result.exit_code == 0, result.output
    assert observed["database_url"] == "postgresql://unused"
    assert observed["source_release_ids"] == [18, 17]
    assert observed["payment_nature"] == "Consulting Fee"
    assert "source releases 17, 18" in result.output
    assert "Analysis run 1 wrote 5 rows" in result.output
    assert "Analysis run 4 wrote 20 rows" in result.output
    assert f"{4:064x}" in result.output
