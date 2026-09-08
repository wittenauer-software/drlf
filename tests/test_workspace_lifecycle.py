from __future__ import annotations

import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest
from typer.testing import CliRunner

from drlf import cli
from drlf.workspace_lifecycle import (
    DISTRIBUTION_MARKER,
    INCOMPLETE_UPGRADE,
    INSTALLED_BASELINE,
    WORKSPACE_MARKER,
    WorkspaceLifecycleError,
    apply_baseline_upgrade,
    bootstrap_private_workspace,
    finalize_private_workspace,
    inspect_private_workspace,
    inspect_research_root_identity,
    load_source_baseline_manifest,
    plan_baseline_upgrade,
    recover_baseline_upgrade,
    require_case_research_root,
    write_upgrade_plan,
)


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout.strip()


def _write_baseline(
    parent: Path,
    name: str,
    version: str,
    files: dict[str, bytes],
) -> tuple[Path, Path, str]:
    root = parent / name
    root.mkdir()
    distribution_marker = root / DISTRIBUTION_MARKER
    distribution_marker.parent.mkdir(parents=True)
    distribution_marker.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "distribution_kind": "public-baseline",
                "product": "DRLF",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    records: list[dict[str, object]] = []
    for relative, content in sorted(files.items()):
        path = root / Path(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        records.append(
            {
                "path": relative,
                "bytes": len(content),
                "sha256": sha256(content).hexdigest(),
                "mode": "100644",
            }
        )
    document = {
        "schema_version": 1,
        "baseline_version": version,
        "source_revision": f"synthetic-{version}",
        "files": records,
    }
    manifest = root / "baseline-manifest.json"
    manifest.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return root, manifest, sha256(manifest.read_bytes()).hexdigest()


def _bootstrap(
    source: Path,
    manifest: Path,
    digest: str,
    destination: Path,
    version: str = "1.0.0",
):
    return bootstrap_private_workspace(
        source,
        manifest,
        destination,
        expected_version=version,
        expected_manifest_sha256=digest,
        git_author_name="Synthetic Researcher",
        git_author_email="synthetic@example.invalid",
        compose_project_name="synthetic-private-workspace",
        postgres_port=55432,
        max_paths=100,
        max_file_bytes=1024 * 1024,
        max_total_bytes=4 * 1024 * 1024,
    )


def _finalize(workspace: Path):
    return finalize_private_workspace(
        workspace,
        confirm_private_filesystem=True,
        confirm_private_sync=True,
        confirm_private_backups=True,
        confirm_no_public_remote=True,
    )


def _plan(
    workspace: Path,
    source: Path,
    manifest: Path,
    digest: str,
    version: str,
    *,
    retain_removed: bool = False,
):
    return plan_baseline_upgrade(
        workspace,
        source,
        manifest,
        expected_version=version,
        expected_manifest_sha256=digest,
        retain_removed=retain_removed,
        max_paths=100,
        max_file_bytes=1024 * 1024,
        max_total_bytes=4 * 1024 * 1024,
    )


def _apply(
    workspace: Path,
    source: Path,
    manifest: Path,
    digest: str,
    version: str,
    plan_path: Path,
    plan_digest: str,
    *,
    approve_skill_changes: bool = False,
):
    return apply_baseline_upgrade(
        workspace,
        source,
        manifest,
        expected_version=version,
        expected_manifest_sha256=digest,
        approved=True,
        approved_plan_path=plan_path,
        approved_plan_sha256=plan_digest,
        skill_changes_approved=approve_skill_changes,
        max_paths=100,
        max_file_bytes=1024 * 1024,
        max_total_bytes=4 * 1024 * 1024,
    )


@pytest.mark.parametrize(
    "invalid_index",
    [
        "schema_version: 1\nnamespace: local\nlearnings: []\n",
        "schema_version: 1\nnamespace: LOCAL-KNOWLEDGE\norigin: public-baseline\nlearnings: []\n",
        "schema_version: 1\nnamespace: LOCAL-KNOWLEDGE\norigin: private-workspace\nlearnings: {}\n",
        "not: [valid YAML",
    ],
)
def test_invalid_local_index_blocks_readiness_and_finalization_without_rewrite(
    tmp_path: Path, invalid_index: str
) -> None:
    source, manifest, digest = _write_baseline(
        tmp_path, "source", "1.0.0", {"AGENTS.md": b"# Synthetic policy\n"}
    )
    workspace = tmp_path / "workspace"
    _bootstrap(source, manifest, digest, workspace)
    index = workspace / "research/knowledge/local/index.yaml"
    index.write_text(invalid_index, encoding="utf-8")
    before = index.read_bytes()
    marker_before = (workspace / WORKSPACE_MARKER).read_bytes()

    report = inspect_private_workspace(workspace)
    assert report.ready is False
    assert next(c for c in report.checks if c.name == "workspace-knowledge-index").status == "fail"
    with pytest.raises(WorkspaceLifecycleError, match="local index requires"):
        _finalize(workspace)
    assert index.read_bytes() == before
    assert (workspace / WORKSPACE_MARKER).read_bytes() == marker_before

    # Readiness also checks the contract after an otherwise valid finalization.
    index.write_text(
        "schema_version: 1\nnamespace: LOCAL-KNOWLEDGE\n"
        "origin: private-workspace\nlearnings: []\n",
        encoding="utf-8",
    )
    _finalize(workspace)
    index.write_bytes(before)
    assert inspect_private_workspace(workspace).ready is False
    with pytest.raises(WorkspaceLifecycleError, match="not finalized and ready"):
        require_case_research_root(workspace)


def test_complete_public_bootstrap_passes_its_shipped_knowledge_validator(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1]
    manifest = source / "baseline-manifest.json"
    workspace = tmp_path / "fresh-public-baseline"
    bootstrap_private_workspace(
        source,
        manifest,
        workspace,
        expected_version="0.1.0",
        expected_manifest_sha256=sha256(manifest.read_bytes()).hexdigest(),
        git_author_name="Synthetic Researcher",
        git_author_email="synthetic@example.invalid",
        compose_project_name="synthetic-learning-contract",
        postgres_port=55432,
    )
    validator = workspace / (
        ".agents/skills/synthesize-research-learnings/scripts/validate_knowledge.py"
    )
    result = subprocess.run(
        [sys.executable, str(validator)], cwd=workspace, capture_output=True,
        text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    index = workspace / "research/knowledge/local/index.yaml"
    index.write_text("schema_version: 1\nnamespace: local\nlearnings: []\n", encoding="utf-8")
    rejected = subprocess.run(
        [sys.executable, str(validator)], cwd=workspace, capture_output=True,
        text=True, timeout=30,
    )
    assert rejected.returncode != 0
    assert "knowledge index namespace" in rejected.stdout + rejected.stderr


def test_case_research_root_identity_fails_closed(tmp_path: Path) -> None:
    assert inspect_research_root_identity(tmp_path).status == "fail"
    with pytest.raises(WorkspaceLifecycleError, match="unknown DRLF root identity"):
        require_case_research_root(tmp_path)

    public_marker = tmp_path / DISTRIBUTION_MARKER
    public_marker.parent.mkdir(parents=True)
    public_marker.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "distribution_kind": "public-baseline",
                "product": "DRLF",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(WorkspaceLifecycleError, match="disabled in a public DRLF baseline"):
        require_case_research_root(tmp_path)
    identity = inspect_research_root_identity(tmp_path)
    assert identity.status == "warn"
    assert "only synthetic work" in identity.detail


def test_bootstrap_requires_external_manifest_digest_and_finalizes_fresh_history(
    tmp_path: Path,
) -> None:
    source, manifest, digest = _write_baseline(
        tmp_path,
        "Public Baseline",
        "1.0.0",
        {
            "AGENTS.md": b"# Synthetic public instructions\n",
            "compose.yaml": b"services: {}\n",
            "pyproject.toml": b"[project]\nname='synthetic-baseline'\nversion='1.0.0'\n",
        },
    )
    workspace = tmp_path / "Private Research Workspace"

    with pytest.raises(WorkspaceLifecycleError, match="expected SHA-256"):
        bootstrap_private_workspace(
            source,
            manifest,
            workspace,
            expected_version="1.0.0",
            expected_manifest_sha256="0" * 64,
            git_author_name="Synthetic Researcher",
            git_author_email="synthetic@example.invalid",
            compose_project_name="synthetic-private-workspace",
            postgres_port=55432,
        )
    assert not workspace.exists()

    created = _bootstrap(source, manifest, digest, workspace)
    before_finalize = inspect_private_workspace(workspace)
    repeated_before_finalize = _bootstrap(source, manifest, digest, workspace)
    assert created.status == "created"
    assert repeated_before_finalize.status == "already_current"
    assert before_finalize.ready is False
    assert (workspace / ".git").is_dir()
    assert _git(workspace, "remote") == ""
    environment = (workspace / ".env").read_text(encoding="utf-8")
    assert "COMPOSE_PROJECT_NAME=synthetic-private-workspace" in environment
    assert "POSTGRES_PORT=55432" in environment
    assert "replace-with" not in environment
    assert "postgresql://drlf:" in environment

    finalized = _finalize(workspace)
    after_finalize = inspect_private_workspace(workspace)
    assert require_case_research_root(workspace) == "private-research-workspace"
    repeated = _bootstrap(source, manifest, digest, workspace)
    repeated_finalize = _finalize(workspace)

    assert finalized.status == "finalized"
    assert len(_git(workspace, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 1
    assert after_finalize.ready is True
    assert repeated.status == "already_current"
    assert repeated_finalize.status == "already_finalized"
    assert repeated_finalize.commit == finalized.commit


def test_upgrade_is_three_way_digest_bound_and_preserves_local_namespaces(
    tmp_path: Path,
) -> None:
    source_one, manifest_one, digest_one = _write_baseline(
        tmp_path,
        "baseline-one",
        "1.0.0",
        {"AGENTS.md": b"version one\n", "docs/method.md": b"stable\n"},
    )
    workspace = tmp_path / "private-workspace"
    _bootstrap(source_one, manifest_one, digest_one, workspace)
    _finalize(workspace)
    local_case = workspace / "research/cases/CASE-9001-local/notes.md"
    local_case.parent.mkdir(parents=True)
    local_case.write_text("local case\n", encoding="utf-8")
    local_learning = workspace / "research/knowledge/local/cards/LOCAL-0001.md"
    local_learning.write_text("local learning\n", encoding="utf-8")
    _git(workspace, "add", "--all")
    _git(workspace, "commit", "--no-gpg-sign", "-m", "Add local records")

    source_two, manifest_two, digest_two = _write_baseline(
        tmp_path,
        "baseline-two",
        "1.1.0",
        {
            "AGENTS.md": b"version two\n",
            "docs/method.md": b"stable\n",
            "docs/new.md": b"new baseline file\n",
        },
    )
    plan, _new = _plan(workspace, source_two, manifest_two, digest_two, "1.1.0")
    plan_path = tmp_path / "reviewed-plan.json"
    plan_digest = write_upgrade_plan(plan, plan_path)

    assert plan.blocked is False
    assert {(item.path, item.action) for item in plan.actions} == {
        ("AGENTS.md", "replace"),
        ("docs/method.md", "none"),
        ("docs/new.md", "add"),
    }
    with pytest.raises(WorkspaceLifecycleError, match="expected SHA-256"):
        _apply(
            workspace,
            source_two,
            manifest_two,
            digest_two,
            "1.1.0",
            plan_path,
            "0" * 64,
        )
    assert (workspace / "AGENTS.md").read_bytes() == b"version one\n"

    result = _apply(
        workspace,
        source_two,
        manifest_two,
        digest_two,
        "1.1.0",
        plan_path,
        plan_digest,
    )

    assert result.status == "upgraded"
    assert result.added == 1
    assert result.replaced == 1
    assert (workspace / "AGENTS.md").read_bytes() == b"version two\n"
    assert (workspace / "docs/new.md").read_bytes() == b"new baseline file\n"
    assert local_case.read_text(encoding="utf-8") == "local case\n"
    assert local_learning.read_text(encoding="utf-8") == "local learning\n"
    assert (
        json.loads((workspace / WORKSPACE_MARKER).read_text(encoding="utf-8"))["baseline_version"]
        == "1.1.0"
    )


def test_upgrade_conflict_and_skill_change_fail_before_mutation(tmp_path: Path) -> None:
    source_one, manifest_one, digest_one = _write_baseline(
        tmp_path,
        "baseline-one",
        "1.0.0",
        {
            "AGENTS.md": b"version one\n",
            ".agents/skills/example/SKILL.md": b"skill one\n",
        },
    )
    workspace = tmp_path / "private-workspace"
    _bootstrap(source_one, manifest_one, digest_one, workspace)
    _finalize(workspace)

    (workspace / "AGENTS.md").write_bytes(b"local edit\n")
    _git(workspace, "add", "AGENTS.md")
    _git(workspace, "commit", "--no-gpg-sign", "-m", "Record local divergence")
    source_two, manifest_two, digest_two = _write_baseline(
        tmp_path,
        "baseline-two",
        "1.1.0",
        {
            "AGENTS.md": b"upstream edit\n",
            ".agents/skills/example/SKILL.md": b"skill two\n",
        },
    )
    conflict, _new = _plan(workspace, source_two, manifest_two, digest_two, "1.1.0")
    assert conflict.blocked is True
    assert next(item for item in conflict.actions if item.path == "AGENTS.md").classification == (
        "concurrent-change"
    )
    assert (workspace / "AGENTS.md").read_bytes() == b"local edit\n"

    (workspace / "AGENTS.md").write_bytes(b"version one\n")
    _git(workspace, "add", "AGENTS.md")
    _git(workspace, "commit", "--no-gpg-sign", "-m", "Resolve local divergence")
    skill_plan, _new = _plan(workspace, source_two, manifest_two, digest_two, "1.1.0")
    plan_path = tmp_path / "skill-plan.json"
    plan_digest = write_upgrade_plan(skill_plan, plan_path)
    assert skill_plan.requires_skill_approval is True

    with pytest.raises(WorkspaceLifecycleError, match="skill-change approval"):
        _apply(
            workspace,
            source_two,
            manifest_two,
            digest_two,
            "1.1.0",
            plan_path,
            plan_digest,
        )
    assert (workspace / ".agents/skills/example/SKILL.md").read_bytes() == b"skill one\n"


def test_failed_upgrade_is_recoverable_and_recovery_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_one, manifest_one, digest_one = _write_baseline(
        tmp_path,
        "baseline-one",
        "1.0.0",
        {"AGENTS.md": b"one\n", "docs/a.md": b"a-one\n"},
    )
    workspace = tmp_path / "private-workspace"
    _bootstrap(source_one, manifest_one, digest_one, workspace)
    _finalize(workspace)
    source_two, manifest_two, digest_two = _write_baseline(
        tmp_path,
        "baseline-two",
        "1.1.0",
        {
            "AGENTS.md": b"two\n",
            "docs/a.md": b"a-two\n",
            "docs/new.md": b"new\n",
        },
    )
    plan, _new = _plan(workspace, source_two, manifest_two, digest_two, "1.1.0")
    plan_path = tmp_path / "recovery-plan.json"
    plan_digest = write_upgrade_plan(plan, plan_path)

    import drlf.workspace_lifecycle as lifecycle

    original_install = lifecycle._install_staged_file
    calls = 0

    def fail_once(staged: Path, target: Path, mode: str, *, root: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic interrupted replacement")
        original_install(staged, target, mode, root=root)

    monkeypatch.setattr(lifecycle, "_install_staged_file", fail_once)
    with pytest.raises(WorkspaceLifecycleError, match="workspace-recover"):
        _apply(
            workspace,
            source_two,
            manifest_two,
            digest_two,
            "1.1.0",
            plan_path,
            plan_digest,
        )

    assert (workspace / INCOMPLETE_UPGRADE).is_file()
    assert inspect_private_workspace(workspace).ready is False
    recovered = recover_baseline_upgrade(workspace)
    repeated = recover_baseline_upgrade(workspace)

    assert recovered.status == "recovered"
    assert repeated.status == "nothing_to_recover"
    assert (workspace / "AGENTS.md").read_bytes() == b"one\n"
    assert (workspace / "docs/a.md").read_bytes() == b"a-one\n"
    assert not (workspace / "docs/new.md").exists()
    assert (
        json.loads((workspace / INSTALLED_BASELINE).read_text(encoding="utf-8"))["baseline_version"]
        == "1.0.0"
    )
    assert inspect_private_workspace(workspace).ready is True


@pytest.mark.parametrize(
    "relative_path",
    [
        "research/cases/CASE-9001.md",
        "research/knowledge/local/card.md",
        "data/example.csv",
        "CON.txt",
        "docs/trailing.",
        "docs\\windows-separator.md",
    ],
)
def test_baseline_manifest_rejects_local_or_nonportable_paths(
    tmp_path: Path, relative_path: str
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    document = {
        "schema_version": 1,
        "baseline_version": "1.0.0",
        "source_revision": "synthetic",
        "files": [
            {
                "path": relative_path,
                "bytes": 0,
                "sha256": sha256(b"").hexdigest(),
                "mode": "100644",
            }
        ],
    }
    manifest = source / "baseline.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    digest = sha256(manifest.read_bytes()).hexdigest()

    with pytest.raises(WorkspaceLifecycleError):
        load_source_baseline_manifest(
            source,
            manifest,
            expected_version="1.0.0",
            expected_manifest_sha256=digest,
        )


def test_workspace_doctor_cli_reports_managed_workspace_state(tmp_path: Path) -> None:
    source, manifest, digest = _write_baseline(
        tmp_path,
        "baseline",
        "1.0.0",
        {"AGENTS.md": b"instructions\n"},
    )
    workspace = tmp_path / "private-workspace"
    _bootstrap(source, manifest, digest, workspace)
    _finalize(workspace)

    result = CliRunner().invoke(
        cli.app,
        ["workspace-doctor", "--root", str(workspace), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ready"] is True
    assert any(check["name"] == "workspace-boundary" for check in payload["checks"])
