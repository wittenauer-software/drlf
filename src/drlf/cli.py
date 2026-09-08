import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, cast

import typer

from drlf.analysis import dmepos_shortlist as dmepos_shortlist_analysis
from drlf.analysis.dmepos_code_freeze import (
    DEFAULT_MAX_INPUT_BYTES as DEFAULT_DMEPOS_CODE_FREEZE_INPUT_BYTES,
)
from drlf.analysis.dmepos_code_freeze import (
    DEFAULT_MAX_INPUT_ROWS as DEFAULT_DMEPOS_CODE_FREEZE_INPUT_ROWS,
)
from drlf.analysis.dmepos_code_freeze import (
    DEFAULT_MAX_MATERIALITY_CODES,
    DEFAULT_MAX_SELECTED_CODES,
    DEFAULT_MIN_COHORT_CODE_PAYMENT,
    DEFAULT_MIN_VISIBLE_SUMMARY_PAYMENT_COVERAGE,
    DEFAULT_OBSERVED_PAYMENT_THRESHOLD,
    DEFAULT_TARGET_CANDIDATE_VISIBLE_PAYMENT_SHARE,
    DEFAULT_TOP_CODES_PER_CANDIDATE,
    freeze_dmepos_materiality_codes,
    freeze_dmepos_service_codes,
)
from drlf.analysis.dmepos_code_freeze import (
    DEFAULT_MAX_OUTPUT_BYTES as DEFAULT_DMEPOS_CODE_FREEZE_OUTPUT_BYTES,
)
from drlf.analysis.dmepos_identity import postgres_array
from drlf.analysis.dmepos_shortlist import (
    DEFAULT_MAX_ANONYMOUS_BYTES,
    DEFAULT_MAX_ANONYMOUS_ROWS,
    DEFAULT_MAX_IDENTITY_BYTES,
    DEFAULT_MAX_IDENTITY_ROWS,
    generate_dmepos_anonymous_shortlist,
    join_dmepos_shortlist_identities,
    read_dmepos_anonymous_candidate_scope,
)
from drlf.analysis.open_payments_organization import (
    INCOMPLETE_FILENAME,
    run_open_payments_organization_analysis,
)
from drlf.analysis.part_b_multicode_reach import (
    DEFAULT_MAX_INPUT_BYTES,
    DEFAULT_MAX_INPUT_ROWS,
    DEFAULT_MAX_OUTPUT_BYTES,
    generate_part_b_multicode_reach_screen,
)
from drlf.analysis.part_b_shortlist import generate_part_b_shortlist
from drlf.analysis.part_d_first_pass import clean_repository_commit, run_part_d_first_pass
from drlf.analysis.sql_runner import parse_parameters, run_sql_analysis
from drlf.case_scaffold import (
    TRIAGE_LANES,
    initialize_case,
    validate_case_workspaces,
)
from drlf.config import get_settings
from drlf.database import apply_migrations, check_connection
from drlf.ingestion.dmepos_supplier import load_dmepos_supplier
from drlf.ingestion.dmepos_supplier_service import load_dmepos_supplier_service
from drlf.ingestion.open_payments_general import load_open_payments_general
from drlf.ingestion.part_b_provider import load_part_b_provider
from drlf.ingestion.part_b_provider_service import load_part_b_provider_service
from drlf.ingestion.part_d_geography_drug import load_part_d_geography_drug
from drlf.ingestion.part_d_provider_drug import load_part_d_provider_drug
from drlf.onboarding_demo import DEFAULT_DEMO_INPUT, run_synthetic_part_b_demo
from drlf.public_release import PublicReleaseMode, run_public_release_check
from drlf.sources.cms_api import (
    ExactInFilter,
    download_exact_in_batches,
    download_filtered_pages,
    fetch_filtered_stats,
    parse_filter_arguments,
)
from drlf.sources.cms_catalog import fetch_dataset_version
from drlf.sources.cms_file import (
    download_cms_file,
    inspect_retained_cms_file,
    preflight_cms_file,
)
from drlf.sources.cms_provider_records_manifest import (
    build_cms_provider_records_api_manifest,
)
from drlf.sources.dmepos_manifest import (
    build_dmepos_supplier_manifest,
    get_dmepos_supplier_release,
)
from drlf.sources.dmepos_supplier_service_manifest import (
    build_dmepos_supplier_service_api_manifest,
)
from drlf.sources.nppes_manifest import build_nppes_api_manifest
from drlf.sources.open_payments import (
    MAX_TARGETED_SQL_RESPONSE_BYTES,
    PaymentCategory,
    build_open_payments_sql_api_manifest,
    build_open_payments_sql_query,
    build_open_payments_sql_url,
    download_open_payments_file,
    preflight_open_payments_file,
)
from drlf.sources.part_b_provider_manifest import build_part_b_provider_api_manifest
from drlf.sources.part_b_provider_service_manifest import (
    build_part_b_provider_service_api_manifest,
)
from drlf.sources.part_d_geography_manifest import (
    build_part_d_geography_api_manifest,
)
from drlf.sources.part_d_manifest import build_part_d_api_manifest
from drlf.workspace_lifecycle import (
    DEFAULT_MAX_BASELINE_FILE_BYTES,
    DEFAULT_MAX_BASELINE_PATHS,
    DEFAULT_MAX_BASELINE_TOTAL_BYTES,
    WORKSPACE_MARKER,
    WorkspaceLifecycleError,
    apply_baseline_upgrade,
    bootstrap_private_workspace,
    finalize_private_workspace,
    inspect_private_workspace,
    inspect_research_root_identity,
    plan_baseline_upgrade,
    recover_baseline_upgrade,
    require_case_research_root,
    require_research_root,
    write_upgrade_plan,
)
from drlf.workspace_status import (
    DATABASE_RELEASE_ROW_CAP,
    DATABASE_STATEMENT_TIMEOUT_SECONDS,
    build_doctor_report,
    build_release_list,
    inspect_source_status,
)

app = typer.Typer(no_args_is_help=True, help="Local healthcare research utilities.")

PUBLIC_BASELINE_COMMANDS = frozenset(
    {
        "case-init",  # Applies its explicit --root gate in the command body.
        "case-validate",
        "db-check",
        "db-migrate",
        "doctor",
        "onboarding-demo",
        "public-release-check",
        "release-list",
        "source-status",
        "workspace-bootstrap",
        "workspace-doctor",
        "workspace-finalize",
        "workspace-recover",
        "workspace-upgrade",
        "workspace-upgrade-plan",
    }
)

RESEARCH_ROOT_COMMANDS = frozenset(
    {
        "analysis-sql",
        "cms-api-extract",
        "cms-api-extract-in",
        "cms-api-extract-in-batches",
        "cms-api-stats",
        "cms-file-preflight",
        "cms-provider-records-api-manifest",
        "cms-version",
        "dmepos-anonymous-shortlist",
        "dmepos-candidate-identities",
        "dmepos-join-identities",
        "dmepos-materiality-code-freeze",
        "dmepos-service-code-freeze",
        "dmepos-supplier-acquire",
        "dmepos-supplier-load",
        "dmepos-supplier-service-api-manifest",
        "dmepos-supplier-service-load",
        "nppes-api-manifest",
        "open-payments-download",
        "open-payments-load",
        "open-payments-organization-analysis",
        "open-payments-preflight",
        "open-payments-sql-capture",
        "open-payments-sql-manifest",
        "part-b-multicode-reach-screen",
        "part-b-provider-api-manifest",
        "part-b-provider-load",
        "part-b-provider-service-api-manifest",
        "part-b-provider-service-load",
        "part-b-shortlist",
        "part-d-api-manifest",
        "part-d-first-pass",
        "part-d-geography-api-manifest",
        "part-d-geography-load",
        "part-d-load",
    }
)


@app.callback()
def enforce_research_root(ctx: typer.Context) -> None:
    """Require a verified research root for every real-data command."""
    command = ctx.invoked_subcommand
    if command is None or command in PUBLIC_BASELINE_COMMANDS:
        return
    if command not in RESEARCH_ROOT_COMMANDS:
        raise typer.BadParameter(
            f"Command {command!r} has no public/private workspace classification; refusing to run"
        )
    try:
        require_research_root(Path.cwd(), operation=command)
    except WorkspaceLifecycleError as error:
        raise typer.BadParameter(str(error)) from error


@app.command("public-release-check")
def public_release_check(
    mode: Annotated[
        PublicReleaseMode,
        typer.Argument(help="Candidate view: staging-filesystem or committed-tree"),
    ],
    target: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, help="Staging directory or public checkout"),
    ],
    policy: Annotated[
        Path,
        typer.Option(
            exists=True,
            dir_okay=False,
            readable=True,
            help="Strict versioned public-release policy (YAML or JSON)",
        ),
    ],
    approved_inventory: Annotated[
        Path,
        typer.Option(
            "--approved-inventory",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Exact reviewed candidate file/size/SHA-256 inventory",
        ),
    ],
    deny_inventory: Annotated[
        Path,
        typer.Option(
            "--deny-inventory",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Private reviewed literal/hash deny inventory",
        ),
    ],
    report: Annotated[
        Path,
        typer.Option(
            dir_okay=False,
            help="New JSON report path outside the candidate; existing files are not replaced",
        ),
    ],
    commit: Annotated[
        str | None,
        typer.Option(help="Full lowercase commit object ID; committed-tree mode only"),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Also emit the redacted report as JSON"),
    ] = False,
) -> None:
    """Check exact candidate bytes under explicit local bounds; never authorize publication."""
    try:
        result = run_public_release_check(
            mode,
            target,
            policy_path=policy,
            approved_inventory_path=approved_inventory,
            deny_inventory_path=deny_inventory,
            report_path=report,
            commit=commit,
        )
    except (OSError, RuntimeError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error

    if as_json:
        typer.echo(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        typer.echo(
            f"Public-release check {result.outcome.upper()}; "
            f"{len(result.findings)} redacted finding(s). Report: {report}"
        )
        typer.echo("Human review is still required; this command does not authorize publication.")
    if result.outcome != "pass":
        raise typer.Exit(code=1)


@app.command("case-validate")
def case_validate(
    root: Annotated[
        Path,
        typer.Option(exists=True, file_okay=False, help="Repository root"),
    ] = Path("."),
) -> None:
    """Validate every managed research-case workspace."""
    try:
        errors = validate_case_workspaces(root)
    except (FileNotFoundError, OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    if errors:
        for error in errors:
            typer.echo(error)
        raise typer.Exit(code=1)
    typer.echo("Research case validation passed.")


@app.command("case-init")
def case_init(
    slug: Annotated[
        str,
        typer.Argument(help="Lowercase hyphenated case directory slug"),
    ],
    title: Annotated[str, typer.Option(help="Short neutral case title")],
    question: Annotated[str, typer.Option(help="Falsifiable primary research question")],
    observation: Annotated[
        str,
        typer.Option(help="Observed fact and where it was observed, without inference"),
    ],
    hypothesis: Annotated[str, typer.Option(help="Falsifiable explanation to test")],
    supporting_evidence: Annotated[
        str,
        typer.Option(help="Specific evidence that would support the hypothesis"),
    ],
    refuting_evidence: Annotated[
        str,
        typer.Option(help="Specific evidence that would weaken or refute the hypothesis"),
    ],
    next_question: Annotated[
        str,
        typer.Option("--next-question", help="One precise bounded next research action"),
    ],
    action_source: Annotated[
        str,
        typer.Option(help="Specific lawful and accessible source for the next action"),
    ],
    distinguishing_outcomes: Annotated[
        str,
        typer.Option(help="How plausible results distinguish competing explanations"),
    ],
    triage_lane: Annotated[
        str,
        typer.Option(help=f"Lane changed by the next action: {', '.join(TRIAGE_LANES)}"),
    ],
    disposition_change: Annotated[
        str,
        typer.Option(help="How a plausible result could change case routing"),
    ],
    effort_cap: Annotated[
        str,
        typer.Option(help="Explicit time, query, download, compute, and fee cap"),
    ],
    cutoff_basis: Annotated[
        str,
        typer.Option(help="Why the bounded action could change the disposition"),
    ],
    program: Annotated[
        list[str] | None,
        typer.Option("--program", help="Repeat for each in-scope program or dataset"),
    ] = None,
    ordinary_explanation: Annotated[
        list[str] | None,
        typer.Option(
            "--ordinary-explanation",
            help="Repeat for each plausible lawful explanation identified at intake",
        ),
    ] = None,
    case_id: Annotated[
        str | None,
        typer.Option(
            help="Explicit CASE-NNNN only when it equals the next sequential ID after the highest"
        ),
    ] = None,
    root: Annotated[
        Path,
        typer.Option(exists=True, file_okay=False, help="Repository root"),
    ] = Path("."),
) -> None:
    """Create a complete, non-overwriting research-case workspace."""
    try:
        require_case_research_root(root)
        result = initialize_case(
            root,
            slug=slug,
            title=title,
            question=question,
            observation=observation,
            hypothesis=hypothesis,
            supporting_evidence=supporting_evidence,
            refuting_evidence=refuting_evidence,
            next_action=next_question,
            action_source=action_source,
            distinguishing_outcomes=distinguishing_outcomes,
            triage_lane=triage_lane,
            disposition_change=disposition_change,
            effort_cap=effort_cap,
            cutoff_basis=cutoff_basis,
            programs=tuple(program or ()),
            ordinary_explanations=tuple(ordinary_explanation or ()),
            case_id=case_id,
        )
    except (
        FileExistsError,
        FileNotFoundError,
        OSError,
        ValueError,
        WorkspaceLifecycleError,
    ) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"Created {result.case_id} at {result.destination}")
    typer.echo("Review and commit the intake before registering loads or analyses for this case.")


@app.command("onboarding-demo")
def onboarding_demo(
    output_dir: Annotated[
        Path,
        typer.Argument(help="Directory for new synthetic shortlist and audit-manifest outputs"),
    ],
    input_path: Annotated[
        Path,
        typer.Option("--input", help="Explicitly synthetic Part B scan fixture"),
    ] = DEFAULT_DEMO_INPUT,
    allow_unpinned_input: Annotated[
        bool,
        typer.Option(
            help=(
                "Allow an altered synthetic input after marker checks; the run manifest will "
                "not claim the data is verified as the maintained teaching fixture"
            )
        ),
    ] = False,
) -> None:
    """Run the offline, deterministic synthetic Part B screening example."""
    try:
        result = run_synthetic_part_b_demo(
            input_path,
            output_dir,
            allow_unpinned_input=allow_unpinned_input,
        )
    except (FileExistsError, FileNotFoundError, OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"Wrote {result.selected_rows} shortlist rows "
        f"({result.shortlist_sha256}) to {result.shortlist_path}"
    )
    typer.echo(f"Wrote audit manifest ({result.manifest_sha256}) to {result.manifest_path}")


@app.command("doctor")
def doctor(
    root: Annotated[
        Path,
        typer.Option(exists=True, file_okay=False, help="Repository root to inspect"),
    ] = Path("."),
    skip_database: Annotated[
        bool,
        typer.Option(help="Skip PostgreSQL reachability and migration checks"),
    ] = False,
    command_timeout_seconds: Annotated[
        int,
        typer.Option(min=1, max=30, help="Per-command Git and Docker timeout"),
    ] = 5,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON"),
    ] = False,
) -> None:
    """Run bounded, credential-safe local setup checks."""
    try:
        report = build_doctor_report(
            root,
            skip_database=skip_database,
            command_timeout_seconds=command_timeout_seconds,
        )
        identity_check = inspect_research_root_identity(root)
        report = type(report)(
            ready=report.ready and identity_check.status != "fail",
            checks=report.checks + (identity_check,),
        )
        if (root.resolve() / WORKSPACE_MARKER).exists():
            workspace_report = inspect_private_workspace(root)
            report = type(report)(
                ready=report.ready and workspace_report.ready,
                checks=report.checks + workspace_report.checks,
            )
    except (OSError, ValueError, WorkspaceLifecycleError) as error:
        raise typer.BadParameter(str(error)) from error

    if as_json:
        typer.echo(json.dumps(report.as_dict(), indent=2))
    else:
        for check in report.checks:
            typer.echo(f"{check.status.upper():4}  {check.name}: {check.detail}")
        typer.echo("READY" if report.ready else "NOT READY")
    if not report.ready:
        raise typer.Exit(code=1)


@app.command("workspace-bootstrap")
def workspace_bootstrap(
    source_root: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, help="Absolute public-baseline source root"),
    ],
    baseline_manifest: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            help="Absolute exact baseline inventory inside the source root",
        ),
    ],
    destination: Annotated[
        Path,
        typer.Argument(help="New absolute private-workspace destination outside the source"),
    ],
    expected_version: Annotated[
        str,
        typer.Option(help="Exact baseline release or revision expected in the inventory"),
    ],
    expected_manifest_sha256: Annotated[
        str,
        typer.Option(help="Previously reviewed SHA-256 of the baseline inventory"),
    ],
    git_author_name: Annotated[
        str,
        typer.Option(help="Explicit local Git author name for the fresh private repository"),
    ],
    git_author_email: Annotated[
        str,
        typer.Option(help="Explicit local Git author email for the fresh private repository"),
    ],
    compose_project_name: Annotated[
        str,
        typer.Option(help="Unique lowercase Docker Compose project name for this workspace"),
    ],
    postgres_port: Annotated[
        int,
        typer.Option(min=1, max=65_535, help="Unused host port for this workspace PostgreSQL"),
    ],
    max_paths: Annotated[
        int,
        typer.Option(min=1, max=DEFAULT_MAX_BASELINE_PATHS, help="Baseline path ceiling"),
    ] = DEFAULT_MAX_BASELINE_PATHS,
    max_file_bytes: Annotated[
        int,
        typer.Option(
            min=1,
            max=DEFAULT_MAX_BASELINE_FILE_BYTES,
            help="Per-file copy ceiling",
        ),
    ] = DEFAULT_MAX_BASELINE_FILE_BYTES,
    max_total_bytes: Annotated[
        int,
        typer.Option(
            min=1,
            max=DEFAULT_MAX_BASELINE_TOTAL_BYTES,
            help="Aggregate copy ceiling",
        ),
    ] = DEFAULT_MAX_BASELINE_TOTAL_BYTES,
    as_json: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
) -> None:
    """Create an isolated private workspace without public Git history or remotes."""
    try:
        result = bootstrap_private_workspace(
            source_root,
            baseline_manifest,
            destination,
            expected_version=expected_version,
            expected_manifest_sha256=expected_manifest_sha256,
            git_author_name=git_author_name,
            git_author_email=git_author_email,
            compose_project_name=compose_project_name,
            postgres_port=postgres_port,
            max_paths=max_paths,
            max_file_bytes=max_file_bytes,
            max_total_bytes=max_total_bytes,
        )
    except (OSError, WorkspaceLifecycleError) as error:
        raise typer.BadParameter(str(error)) from error
    if as_json:
        typer.echo(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        typer.echo(
            f"Workspace {result.status}: {result.destination} "
            f"(baseline {result.baseline_version}, {result.managed_file_count} managed files)"
        )
        typer.echo(
            "Fresh local Git metadata has no remote; run workspace-finalize before real research."
        )


@app.command("workspace-finalize")
def workspace_finalize(
    root: Annotated[
        Path,
        typer.Option(exists=True, file_okay=False, help="Bootstrapped private workspace root"),
    ] = Path("."),
    confirm_private_filesystem: Annotated[
        bool,
        typer.Option(
            "--confirm-private-filesystem",
            help="Confirm the workspace filesystem audience is appropriate for private research",
        ),
    ] = False,
    confirm_private_sync: Annotated[
        bool,
        typer.Option(
            "--confirm-private-sync",
            help="Confirm synchronization services will not expose workspace contents",
        ),
    ] = False,
    confirm_private_backups: Annotated[
        bool,
        typer.Option(
            "--confirm-private-backups",
            help="Confirm backup destinations are appropriate for private research",
        ),
    ] = False,
    confirm_no_public_remote: Annotated[
        bool,
        typer.Option(
            "--confirm-no-public-remote",
            help="Confirm no public remote or fork relationship is configured",
        ),
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
) -> None:
    """Record the private-boundary review and create the fresh root commit."""
    try:
        result = finalize_private_workspace(
            root,
            confirm_private_filesystem=confirm_private_filesystem,
            confirm_private_sync=confirm_private_sync,
            confirm_private_backups=confirm_private_backups,
            confirm_no_public_remote=confirm_no_public_remote,
        )
    except (OSError, WorkspaceLifecycleError) as error:
        raise typer.BadParameter(str(error)) from error
    if as_json:
        typer.echo(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        typer.echo(f"Workspace {result.status} at private root commit {result.commit}")


@app.command("workspace-doctor")
def workspace_doctor(
    root: Annotated[
        Path,
        typer.Option(exists=True, file_okay=False, help="Private workspace root to inspect"),
    ] = Path("."),
    as_json: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
) -> None:
    """Check private-workspace isolation, baseline drift, namespaces, and recovery state."""
    try:
        report = inspect_private_workspace(root)
    except (OSError, WorkspaceLifecycleError) as error:
        raise typer.BadParameter(str(error)) from error
    if as_json:
        typer.echo(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        for check in report.checks:
            typer.echo(f"{check.status.upper():4}  {check.name}: {check.detail}")
        typer.echo("WORKSPACE READY" if report.ready else "WORKSPACE NOT READY")
    if not report.ready:
        raise typer.Exit(code=1)


@app.command("workspace-upgrade-plan")
def workspace_upgrade_plan(
    source_root: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, help="Absolute new-baseline source root"),
    ],
    baseline_manifest: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, help="Absolute new-baseline inventory"),
    ],
    expected_version: Annotated[
        str,
        typer.Option(help="Exact new baseline release or revision expected in the inventory"),
    ],
    expected_manifest_sha256: Annotated[
        str,
        typer.Option(help="Previously reviewed SHA-256 of the new baseline inventory"),
    ],
    report: Annotated[
        Path,
        typer.Option(
            dir_okay=False,
            help="New JSON plan path to hash and review before applying the upgrade",
        ),
    ],
    root: Annotated[
        Path,
        typer.Option(exists=True, file_okay=False, help="Private workspace root"),
    ] = Path("."),
    retain_removed: Annotated[
        bool,
        typer.Option(help="Explicitly retain upstream-removed paths as local-only content"),
    ] = False,
    max_paths: Annotated[
        int,
        typer.Option(min=1, max=DEFAULT_MAX_BASELINE_PATHS),
    ] = DEFAULT_MAX_BASELINE_PATHS,
    max_file_bytes: Annotated[
        int,
        typer.Option(min=1, max=DEFAULT_MAX_BASELINE_FILE_BYTES),
    ] = DEFAULT_MAX_BASELINE_FILE_BYTES,
    max_total_bytes: Annotated[
        int,
        typer.Option(min=1, max=DEFAULT_MAX_BASELINE_TOTAL_BYTES),
    ] = DEFAULT_MAX_BASELINE_TOTAL_BYTES,
    as_json: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
) -> None:
    """Preview a three-way public-baseline update without changing the workspace."""
    try:
        plan, _manifest = plan_baseline_upgrade(
            root,
            source_root,
            baseline_manifest,
            expected_version=expected_version,
            expected_manifest_sha256=expected_manifest_sha256,
            retain_removed=retain_removed,
            max_paths=max_paths,
            max_file_bytes=max_file_bytes,
            max_total_bytes=max_total_bytes,
        )
        plan_sha256 = write_upgrade_plan(
            plan,
            report,
            forbidden_roots=(root.resolve(), source_root.resolve()),
        )
    except (OSError, WorkspaceLifecycleError) as error:
        raise typer.BadParameter(str(error)) from error
    if as_json:
        payload = plan.as_dict()
        payload["plan_path"] = str(report.resolve())
        payload["plan_sha256"] = plan_sha256
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        typer.echo(f"Baseline {plan.from_version} -> {plan.to_version}")
        for action in plan.actions:
            typer.echo(f"{action.action.upper():8} {action.classification}: {action.path}")
        typer.echo(f"Plan: {report} ({plan_sha256})")
        if plan.requires_skill_approval:
            typer.echo("Skill changes require separate explicit approval before apply.")
        typer.echo("BLOCKED" if plan.blocked else "READY FOR EXPLICIT APPROVAL")
    if plan.blocked:
        raise typer.Exit(code=1)


@app.command("workspace-upgrade")
def workspace_upgrade(
    source_root: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, help="Absolute new-baseline source root"),
    ],
    baseline_manifest: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, help="Absolute new-baseline inventory"),
    ],
    expected_version: Annotated[str, typer.Option(help="Exact expected new baseline version")],
    expected_manifest_sha256: Annotated[
        str,
        typer.Option(help="Previously reviewed SHA-256 of the new baseline inventory"),
    ],
    approved_plan: Annotated[
        Path,
        typer.Option(exists=True, dir_okay=False, help="Exact separately reviewed plan JSON"),
    ],
    approved_plan_sha256: Annotated[
        str,
        typer.Option(help="Previously reviewed SHA-256 of the approved plan JSON"),
    ],
    root: Annotated[
        Path,
        typer.Option(exists=True, file_okay=False, help="Private workspace root"),
    ] = Path("."),
    approve: Annotated[
        bool,
        typer.Option("--approve", help="Confirm the separately reviewed upgrade plan"),
    ] = False,
    approve_skill_changes: Annotated[
        bool,
        typer.Option(
            "--approve-skill-changes",
            help="Separate approval when the exact plan changes baseline skill files",
        ),
    ] = False,
    retain_removed: Annotated[
        bool,
        typer.Option(help="Explicitly retain upstream-removed paths as local-only content"),
    ] = False,
    max_paths: Annotated[
        int,
        typer.Option(min=1, max=DEFAULT_MAX_BASELINE_PATHS),
    ] = DEFAULT_MAX_BASELINE_PATHS,
    max_file_bytes: Annotated[
        int,
        typer.Option(min=1, max=DEFAULT_MAX_BASELINE_FILE_BYTES),
    ] = DEFAULT_MAX_BASELINE_FILE_BYTES,
    max_total_bytes: Annotated[
        int,
        typer.Option(min=1, max=DEFAULT_MAX_BASELINE_TOTAL_BYTES),
    ] = DEFAULT_MAX_BASELINE_TOTAL_BYTES,
    as_json: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
) -> None:
    """Apply an approved baseline update without changing local research content."""
    try:
        result = apply_baseline_upgrade(
            root,
            source_root,
            baseline_manifest,
            expected_version=expected_version,
            expected_manifest_sha256=expected_manifest_sha256,
            approved=approve,
            approved_plan_path=approved_plan,
            approved_plan_sha256=approved_plan_sha256,
            skill_changes_approved=approve_skill_changes,
            retain_removed=retain_removed,
            max_paths=max_paths,
            max_file_bytes=max_file_bytes,
            max_total_bytes=max_total_bytes,
        )
    except (OSError, WorkspaceLifecycleError) as error:
        raise typer.BadParameter(str(error)) from error
    if as_json:
        typer.echo(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        typer.echo(
            f"Workspace {result.status}: baseline {result.from_version} -> {result.to_version}; "
            f"added={result.added}, replaced={result.replaced}, preserved={result.preserved}"
        )


@app.command("workspace-recover")
def workspace_recover(
    root: Annotated[
        Path,
        typer.Option(exists=True, file_okay=False, help="Private workspace root"),
    ] = Path("."),
    as_json: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
) -> None:
    """Recover the prior baseline after an interrupted workspace upgrade."""
    try:
        result = recover_baseline_upgrade(root)
    except (OSError, WorkspaceLifecycleError) as error:
        raise typer.BadParameter(str(error)) from error
    if as_json:
        typer.echo(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        typer.echo(
            f"Workspace recovery {result.status}; restored={result.restored}, "
            f"removed additions={result.removed_additions}"
        )


@app.command("source-status")
def source_status(
    root: Annotated[
        Path,
        typer.Option(exists=True, file_okay=False, help="Repository root to inspect"),
    ] = Path("."),
    manifest_dir: Annotated[
        Path,
        typer.Option(help="Source-manifest directory relative to the repository root"),
    ] = Path("research/data-manifests"),
    manifest: Annotated[
        list[Path] | None,
        typer.Option("--manifest", help="Repeat for specific manifests instead of the directory"),
    ] = None,
    hash_byte_cap: Annotated[
        int | None,
        typer.Option(
            min=1,
            help="Explicit aggregate byte cap that enables bounded streaming SHA-256 checks",
        ),
    ] = None,
    details: Annotated[
        bool,
        typer.Option(help="List every manifest after the summary"),
    ] = False,
    require_ready: Annotated[
        bool,
        typer.Option(help="Exit nonzero unless every complete release file is hash verified"),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON with file-level results"),
    ] = False,
) -> None:
    """Check local raw-file availability; hash only under an explicit byte cap."""
    try:
        report = inspect_source_status(
            root,
            manifest_dir=manifest_dir,
            manifests=manifest,
            hash_byte_cap=hash_byte_cap,
        )
    except (OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error

    if as_json:
        typer.echo(json.dumps(report.as_dict(), indent=2))
    else:
        other_manifest_count = (
            report.manifest_count - report.complete_manifest_count - report.invalid_manifest_count
        )
        typer.echo(
            f"Manifests: {report.manifest_count} total; "
            f"{report.complete_manifest_count} complete; "
            f"{other_manifest_count} non-complete; {report.invalid_manifest_count} invalid"
        )
        typer.echo(
            "Complete-manifest files: "
            f"{report.available_file_count}/{report.declared_file_count} present; "
            f"missing: {report.missing_file_count}; size mismatches: "
            f"{report.size_mismatch_count}"
        )
        if hash_byte_cap is None:
            typer.echo("SHA-256: not checked (supply --hash-byte-cap to enable bounded hashing)")
        else:
            typer.echo(
                f"SHA-256 checked: {report.hash_checked_count}; mismatches: "
                f"{report.hash_mismatch_count}; skipped for cap: "
                f"{report.hash_skipped_for_cap_count}; bytes read: {report.hash_bytes_read}/"
                f"{hash_byte_cap}"
            )
        typer.echo(
            "Complete-manifest source set: verified"
            if report.manifest_source_set_verified
            else "Complete-manifest source set: not fully verified"
        )
        if details or manifest:
            for item in report.manifests:
                year = item.data_year if item.data_year is not None else "-"
                slug = item.dataset_slug or "-"
                typer.echo(f"{item.restore_status:26} {slug} {year} {item.manifest_path}")
                for file in item.files:
                    if file.size_status not in {"match"} or file.hash_status in {
                        "mismatch",
                        "skipped_cap",
                        "changed_during_hash",
                    }:
                        typer.echo(f"  {file.size_status}/{file.hash_status}: {file.relative_path}")
    if require_ready and not report.manifest_source_set_verified:
        raise typer.Exit(code=1)


@app.command("release-list")
def release_list(
    root: Annotated[
        Path,
        typer.Option(exists=True, file_okay=False, help="Repository root to inspect"),
    ] = Path("."),
    manifest_dir: Annotated[
        Path,
        typer.Option(help="Source-manifest directory relative to the repository root"),
    ] = Path("research/data-manifests"),
    dataset_slug: Annotated[
        str | None,
        typer.Option("--dataset", help="Exact dataset slug filter"),
    ] = None,
    data_year: Annotated[
        int | None,
        typer.Option("--year", min=1900, max=2200, help="Exact data-year filter"),
    ] = None,
    include_database: Annotated[
        bool,
        typer.Option(help="Also read registered releases from PostgreSQL when available"),
    ] = False,
    database_row_cap: Annotated[
        int,
        typer.Option(
            min=1,
            max=100_000,
            help="Maximum registered database releases to return",
        ),
    ] = DATABASE_RELEASE_ROW_CAP,
    database_statement_timeout_seconds: Annotated[
        int,
        typer.Option(
            min=1,
            max=30,
            help="PostgreSQL statement and connection timeout",
        ),
    ] = DATABASE_STATEMENT_TIMEOUT_SECONDS,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON"),
    ] = False,
) -> None:
    """List local release manifests and optionally registered database releases."""
    try:
        report = build_release_list(
            root,
            manifest_dir=manifest_dir,
            dataset_slug=dataset_slug,
            data_year=data_year,
            include_database=include_database,
            database_row_cap=database_row_cap,
            database_statement_timeout_seconds=database_statement_timeout_seconds,
        )
    except (OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error

    if as_json:
        typer.echo(json.dumps(report.as_dict(), indent=2))
        return

    typer.echo(
        f"Local manifests: {len(report.local_releases)}; "
        f"invalid manifests: {len(report.invalid_manifests)}"
    )
    for release in report.local_releases:
        year = release.data_year if release.data_year is not None else "-"
        typer.echo(
            f"{release.manifest_status:8} {release.dataset_slug or '-'} {year} "
            f"{release.manifest_path}"
        )
    if report.database_status == "not_checked":
        typer.echo("Database releases: not checked (use --include-database)")
    elif report.database_status == "unavailable":
        typer.echo(
            "Database releases: unavailable "
            f"({report.database_error_type}); no credentials were shown"
        )
    else:
        suffix = " (truncated at the requested row cap)" if report.database_truncated else ""
        typer.echo(f"Database releases: {len(report.database_releases)}{suffix}")
        for release in report.database_releases:
            year = release.data_year if release.data_year is not None else "-"
            typer.echo(
                f"{release.status:10} id={release.source_release_id} "
                f"{release.dataset_slug} {year} {release.manifest_path or '-'}"
            )


@app.command("cms-file-preflight")
def cms_file_preflight(
    request_url: Annotated[str, typer.Argument(help="Official CMS file URL")],
) -> None:
    """Inspect one CMS artifact without retaining its body."""
    try:
        result = preflight_cms_file(request_url)
    except (ValueError, RuntimeError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(json.dumps(asdict(result), indent=2))


@app.command("dmepos-supplier-acquire")
def dmepos_supplier_acquire(
    data_year: Annotated[int, typer.Argument(min=2022, max=2024)],
    output: Annotated[
        Path,
        typer.Argument(help="New immutable CSV path below data/raw/"),
    ],
    manifest: Annotated[
        Path,
        typer.Argument(help="New committed source-manifest path"),
    ],
    max_bytes: Annotated[
        int,
        typer.Option(min=1, help="Hard transfer ceiling; no implicit unlimited download"),
    ] = 40 * 1024 * 1024,
    documentation_snapshot: Annotated[
        list[str] | None,
        typer.Option(help="Repeatable retained documentation path"),
    ] = None,
    observed_at: Annotated[
        str | None,
        typer.Option(help="UTC acquisition timestamp ending in Z; defaults to current time"),
    ] = None,
) -> None:
    """Acquire and manifest one pinned annual DMEPOS supplier summary."""
    try:
        release = get_dmepos_supplier_release(data_year)
        if output.name != release.filename:
            raise ValueError(
                f"Output filename must preserve the CMS release name {release.filename!r}"
            )
        preflight = preflight_cms_file(release.download_url)
        if preflight.total_bytes is not None and preflight.total_bytes != release.expected_bytes:
            raise RuntimeError(
                "CMS preflight size differs from the reviewed pinned release; "
                "refresh the release metadata before downloading"
            )
        observation = (
            datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            if observed_at
            else datetime.now(UTC)
        )
        if output.exists():
            if manifest.exists():
                raise FileExistsError(f"Refusing to replace manifest: {manifest}")
            download = inspect_retained_cms_file(
                release.download_url,
                output,
                max_bytes=max_bytes,
                preflight_result=preflight,
            )
        else:
            download = download_cms_file(
                release.download_url,
                output,
                max_bytes=max_bytes,
                preflight_result=preflight,
            )
        result = build_dmepos_supplier_manifest(
            download,
            manifest,
            release=release,
            observed_at=observation,
            documentation_snapshots=tuple(documentation_snapshot or ()),
        )
    except (ValueError, FileExistsError, RuntimeError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"Retained and validated {result['validation']['rows']} supplier rows "
        f"({download.sha256}) and wrote {manifest}"
    )


@app.command("dmepos-supplier-load")
def dmepos_supplier_load(
    manifest: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    case_id: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Load one complete DMEPOS supplier-summary manifest into PostgreSQL."""
    settings = get_settings()
    code_commit = clean_repository_commit(Path.cwd())
    try:
        release_id, rows = load_dmepos_supplier(
            settings.database_url.get_secret_value(),
            manifest,
            code_commit=code_commit,
            pipeline_version="0.1.0",
            case_id=case_id,
        )
    except (ValueError, FileExistsError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"Loaded {rows} DMEPOS supplier rows into source release {release_id}")


@app.command("dmepos-supplier-service-api-manifest")
def dmepos_supplier_service_api_manifest(
    output: Annotated[Path, typer.Argument(help="New committed source-manifest path")],
    data_year: Annotated[int, typer.Option(min=2022, max=2024)],
    inventories: Annotated[
        list[Path],
        typer.Option(
            "--inventory",
            exists=True,
            dir_okay=False,
            help="Repeatable retained CMS API inventory path",
        ),
    ],
    documentation_snapshot: Annotated[
        list[str] | None,
        typer.Option(help="Repeatable retained documentation path"),
    ] = None,
) -> None:
    """Build one annual manifest for targeted DMEPOS supplier-service pages."""
    try:
        result = build_dmepos_supplier_service_api_manifest(
            inventories,
            output,
            data_year=data_year,
            documentation_snapshots=documentation_snapshot or [],
        )
    except (ValueError, FileExistsError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"Validated {result['validation']['rows']} DMEPOS supplier-service rows and wrote {output}"
    )


@app.command("dmepos-supplier-service-load")
def dmepos_supplier_service_load(
    manifest: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    case_id: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Load one targeted DMEPOS supplier-service manifest into PostgreSQL."""
    settings = get_settings()
    code_commit = clean_repository_commit(Path.cwd())
    try:
        release_id, rows = load_dmepos_supplier_service(
            settings.database_url.get_secret_value(),
            manifest,
            code_commit=code_commit,
            pipeline_version="0.1.0",
            case_id=case_id,
        )
    except (ValueError, FileExistsError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"Loaded {rows} DMEPOS supplier-service rows into source release {release_id}")


@app.command("open-payments-preflight")
def open_payments_preflight(
    request_url: Annotated[str, typer.Argument(help="Official CMS file or API URL")],
) -> None:
    """Inspect an Open Payments artifact size without transferring its body."""
    try:
        result = preflight_open_payments_file(request_url)
    except (ValueError, RuntimeError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(json.dumps(asdict(result), indent=2))


@app.command("open-payments-download")
def open_payments_download(
    request_url: Annotated[str, typer.Argument(help="Official CMS file or API URL")],
    output: Annotated[Path, typer.Argument()],
    max_bytes: Annotated[
        int,
        typer.Option(min=1, help="Hard transfer ceiling; no implicit unlimited download"),
    ],
) -> None:
    """Preflight and stream one immutable Open Payments artifact under a hard byte cap."""
    try:
        result = download_open_payments_file(request_url, output, max_bytes=max_bytes)
    except (ValueError, FileExistsError, RuntimeError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"Saved {result.bytes} bytes ({result.sha256}) to {result.path}; "
        f"resumed from {result.resumed_from}"
    )


@app.command("open-payments-sql-capture")
def open_payments_sql_capture(
    dataset_id: Annotated[str, typer.Argument(help="Program-year Open Payments dataset UUID")],
    resource_id: Annotated[str, typer.Argument(help="Datastore resource UUID")],
    output: Annotated[Path, typer.Argument(help="Immutable raw JSON response path")],
    manifest: Annotated[Path, typer.Argument(help="Committed source-manifest path")],
    program_year: Annotated[int, typer.Option(min=2013, max=2200)],
    filters: Annotated[
        list[str],
        typer.Option("--filter", help="Repeatable exact datastore FIELD=VALUE filter"),
    ],
    max_bytes: Annotated[
        int,
        typer.Option(min=1, help="Hard byte ceiling for the targeted response"),
    ],
    payment_category: Annotated[
        str,
        typer.Option(help="Open Payments category: general, research, or ownership"),
    ] = "general",
    published_at: Annotated[str | None, typer.Option(help="Dataset issue date YYYY-MM-DD")] = None,
    modified_at: Annotated[
        str | None,
        typer.Option(help="Dataset modified date YYYY-MM-DD"),
    ] = None,
) -> None:
    """Retain and manifest a bounded exact-filter Open Payments SQL response."""
    if max_bytes > MAX_TARGETED_SQL_RESPONSE_BYTES:
        raise typer.BadParameter(
            "Open Payments targeted SQL capture has a 32 MiB validation ceiling; "
            "narrow the exact filters or use a future streaming path"
        )
    categories = {"general", "research", "ownership"}
    normalized_category = payment_category.casefold()
    if normalized_category not in categories:
        raise typer.BadParameter("payment-category must be general, research, or ownership")
    try:
        exact_filters = parse_filter_arguments(filters)
        query = build_open_payments_sql_query(resource_id, exact_filters)
        request_url = build_open_payments_sql_url(query)
        observation = datetime.now(UTC)
        download = download_open_payments_file(request_url, output, max_bytes=max_bytes)
        category = cast(PaymentCategory, normalized_category)
        category_title = {
            "general": "General Payment",
            "research": "Research Payment",
            "ownership": "Ownership Payment",
        }[category]
        result = build_open_payments_sql_api_manifest(
            download.path,
            manifest,
            dataset_name=f"Open Payments {program_year} {category_title} Data",
            dataset_slug=f"open-payments-{normalized_category}-payments",
            program_year=program_year,
            payment_category=category,
            dataset_id=dataset_id,
            resource_id=resource_id,
            request_url=request_url,
            query=query,
            exact_filters=exact_filters,
            observed_at=observation,
            landing_page=f"https://openpaymentsdata.cms.gov/dataset/{dataset_id}",
            published_at=date.fromisoformat(published_at) if published_at else None,
            modified_at=date.fromisoformat(modified_at) if modified_at else None,
        )
    except (ValueError, FileExistsError, RuntimeError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"Captured {result['validation']['rows']} Open Payments rows "
        f"({download.sha256}) and wrote {manifest}"
    )


@app.command("open-payments-sql-manifest")
def open_payments_sql_manifest(
    dataset_id: Annotated[str, typer.Argument(help="Program-year Open Payments dataset UUID")],
    resource_id: Annotated[str, typer.Argument(help="Datastore resource UUID")],
    response: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    manifest: Annotated[Path, typer.Argument(help="Committed source-manifest path")],
    program_year: Annotated[int, typer.Option(min=2013, max=2200)],
    filters: Annotated[
        list[str],
        typer.Option("--filter", help="Repeatable exact datastore FIELD=VALUE filter"),
    ],
    observed_at: Annotated[
        str,
        typer.Option(help="UTC response retrieval timestamp ending in Z"),
    ],
    payment_category: Annotated[
        str,
        typer.Option(help="Open Payments category: general, research, or ownership"),
    ] = "general",
    published_at: Annotated[str | None, typer.Option(help="Dataset issue date YYYY-MM-DD")] = None,
    modified_at: Annotated[
        str | None,
        typer.Option(help="Dataset modified date YYYY-MM-DD"),
    ] = None,
) -> None:
    """Validate and manifest a previously retained exact-filter SQL response."""
    categories = {"general", "research", "ownership"}
    normalized_category = payment_category.casefold()
    if normalized_category not in categories:
        raise typer.BadParameter("payment-category must be general, research, or ownership")
    try:
        exact_filters = parse_filter_arguments(filters)
        query = build_open_payments_sql_query(resource_id, exact_filters)
        request_url = build_open_payments_sql_url(query)
        category = cast(PaymentCategory, normalized_category)
        category_title = {
            "general": "General Payment",
            "research": "Research Payment",
            "ownership": "Ownership Payment",
        }[category]
        result = build_open_payments_sql_api_manifest(
            response,
            manifest,
            dataset_name=f"Open Payments {program_year} {category_title} Data",
            dataset_slug=f"open-payments-{normalized_category}-payments",
            program_year=program_year,
            payment_category=category,
            dataset_id=dataset_id,
            resource_id=resource_id,
            request_url=request_url,
            query=query,
            exact_filters=exact_filters,
            observed_at=datetime.fromisoformat(observed_at.replace("Z", "+00:00")),
            landing_page=f"https://openpaymentsdata.cms.gov/dataset/{dataset_id}",
            published_at=date.fromisoformat(published_at) if published_at else None,
            modified_at=date.fromisoformat(modified_at) if modified_at else None,
        )
    except (ValueError, FileExistsError, RuntimeError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"Validated {result['validation']['rows']} Open Payments rows and wrote {manifest}")


@app.command("nppes-api-manifest")
def nppes_api_manifest(
    snapshot: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Argument()],
    npi: Annotated[str, typer.Option(help="Exact ten-digit NPI requested")],
    observed_at: Annotated[str, typer.Option(help="UTC retrieval timestamp ending in Z")],
) -> None:
    """Build a validated manifest for one retained NPPES exact-NPI response."""
    try:
        manifest = build_nppes_api_manifest(
            snapshot,
            output,
            npi=npi,
            observed_at=observed_at,
        )
    except (ValueError, FileExistsError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"Wrote NPPES manifest for {npi} ({manifest['files'][0]['sha256']}) to {output}")


@app.command("open-payments-load")
def open_payments_load(
    manifest: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    case_id: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Load a complete bounded Open Payments General Payment manifest."""
    settings = get_settings()
    code_commit = clean_repository_commit(Path.cwd())
    release_id, rows = load_open_payments_general(
        settings.database_url.get_secret_value(),
        manifest,
        code_commit=code_commit,
        pipeline_version="0.1.0",
        case_id=case_id,
    )
    typer.echo(f"Loaded {rows} Open Payments rows into source release {release_id}")


@app.command("db-check")
def db_check() -> None:
    """Verify that the configured PostgreSQL database is reachable."""
    settings = get_settings()
    version = check_connection(settings.database_url.get_secret_value())
    typer.echo(f"Connected to {version}")


@app.command("db-migrate")
def db_migrate(
    migrations: Annotated[
        Path,
        typer.Option(exists=True, file_okay=False),
    ] = Path("sql/migrations"),
) -> None:
    """Apply pending immutable SQL migrations in filename order."""
    settings = get_settings()
    applied = apply_migrations(settings.database_url.get_secret_value(), migrations)
    if applied:
        typer.echo("Applied migrations: " + ", ".join(applied))
    else:
        typer.echo("Database schema is current.")


@app.command("cms-api-extract")
def cms_api_extract(
    dataset_uuid: Annotated[str, typer.Argument(help="Version-specific CMS dataset UUID")],
    output_dir: Annotated[Path, typer.Argument()],
    filters: Annotated[
        list[str],
        typer.Option("--filter", help="Repeatable CMS API FIELD=VALUE filter"),
    ],
    page_size: Annotated[int, typer.Option(min=1, max=5_000)] = 5_000,
    max_pages: Annotated[
        int,
        typer.Option(min=1, help="Safety limit for targeted API pagination"),
    ] = 1_000,
    max_response_bytes: Annotated[
        int,
        typer.Option(min=1, help="Hard byte ceiling for any one API page"),
    ] = 16 * 1024 * 1024,
    max_total_bytes: Annotated[
        int,
        typer.Option(min=1, help="Hard cumulative byte ceiling for the extraction"),
    ] = 64 * 1024 * 1024,
) -> None:
    """Download an immutable, filtered, paginated CMS API snapshot."""
    try:
        parsed_filters = parse_filter_arguments(filters)
        stats = fetch_filtered_stats(dataset_uuid, parsed_filters)
        required_pages = max(1, (stats["found_rows"] + page_size - 1) // page_size)
        if required_pages > max_pages:
            raise ValueError(
                f"Filtered stats report {stats['found_rows']} rows requiring "
                f"{required_pages} pages, above the {max_pages}-page safety limit"
            )
        inventory = download_filtered_pages(
            dataset_uuid,
            parsed_filters,
            output_dir,
            page_size=page_size,
            max_pages=max_pages,
            max_response_bytes=max_response_bytes,
            max_total_bytes=max_total_bytes,
            expected_rows=stats["found_rows"],
        )
    except (ValueError, FileExistsError, RuntimeError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"Saved {len(inventory['pages'])} pages with {inventory['total_rows']} rows to {output_dir}"
    )


@app.command("cms-api-stats")
def cms_api_stats(
    dataset_uuid: Annotated[str, typer.Argument(help="Version-specific CMS dataset UUID")],
    filters: Annotated[
        list[str],
        typer.Option("--filter", help="Repeatable CMS API FIELD=VALUE filter"),
    ],
) -> None:
    """Preflight the filtered and full CMS API row counts without downloading rows."""
    try:
        parsed_filters = parse_filter_arguments(filters)
        stats = fetch_filtered_stats(dataset_uuid, parsed_filters)
    except (ValueError, RuntimeError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(json.dumps(stats, indent=2))


@app.command("cms-api-extract-in")
def cms_api_extract_in(
    dataset_uuid: Annotated[str, typer.Argument(help="Version-specific CMS dataset UUID")],
    field: Annotated[str, typer.Argument(help="Exact CMS field used by the closed IN filter")],
    output_dir: Annotated[Path, typer.Argument()],
    values: Annotated[
        list[str],
        typer.Option("--value", help="Repeatable exact filter value"),
    ],
    sort_fields: Annotated[
        list[str],
        typer.Option("--sort-field", help="Repeatable stable API sort field"),
    ],
    page_size: Annotated[int, typer.Option(min=1, max=1_000)] = 1_000,
    max_pages: Annotated[int, typer.Option(min=1, max=5)] = 5,
    max_rows: Annotated[int, typer.Option(min=1, max=5_000)] = 5_000,
    max_response_bytes: Annotated[
        int,
        typer.Option(min=1, max=4 * 1024 * 1024),
    ] = 4 * 1024 * 1024,
    max_total_bytes: Annotated[
        int,
        typer.Option(min=1, max=16 * 1024 * 1024),
    ] = 16 * 1024 * 1024,
) -> None:
    """Download one immutable, exact-value-set CMS API snapshot."""
    try:
        if len(values) != len(set(values)):
            raise ValueError("IN-filter values must be unique")
        exact_filter = ExactInFilter(field, tuple(sorted(values)))
        stats = fetch_filtered_stats(dataset_uuid, exact_filter)
        if stats["found_rows"] > max_rows:
            raise ValueError(
                f"Filtered stats report {stats['found_rows']} rows, above the "
                f"{max_rows}-row safety limit"
            )
        required_pages = max(1, (stats["found_rows"] + page_size - 1) // page_size)
        if required_pages > max_pages:
            raise ValueError(
                f"Filtered stats require {required_pages} pages, above the "
                f"{max_pages}-page safety limit"
            )
        inventory = download_filtered_pages(
            dataset_uuid,
            exact_filter,
            output_dir,
            page_size=page_size,
            max_pages=max_pages,
            max_response_bytes=max_response_bytes,
            max_total_bytes=max_total_bytes,
            expected_rows=stats["found_rows"],
            sort_fields=tuple(sort_fields),
        )
    except (ValueError, FileExistsError, RuntimeError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"Saved {len(inventory['pages'])} pages with {inventory['total_rows']} rows to {output_dir}"
    )


@app.command("cms-api-extract-in-batches")
def cms_api_extract_in_batches(
    dataset_uuid: Annotated[str, typer.Argument(help="Version-specific CMS dataset UUID")],
    field: Annotated[str, typer.Argument(help="Exact CMS field used by the closed IN filter")],
    output_dir: Annotated[Path, typer.Argument()],
    values: Annotated[
        list[str],
        typer.Option("--value", help="Repeatable exact filter value"),
    ],
    sort_fields: Annotated[
        list[str],
        typer.Option("--sort-field", help="Repeatable stable API sort field"),
    ],
    batch_size: Annotated[int, typer.Option(min=1, max=50)] = 1,
    page_size: Annotated[int, typer.Option(min=1, max=1_000)] = 1_000,
    max_batches: Annotated[int, typer.Option(min=1, max=50)] = 50,
    max_pages_per_batch: Annotated[int, typer.Option(min=1, max=5)] = 5,
    max_rows_per_batch: Annotated[int, typer.Option(min=1, max=5_000)] = 5_000,
    max_bundle_rows: Annotated[int, typer.Option(min=1, max=25_000)] = 25_000,
    max_response_bytes: Annotated[
        int,
        typer.Option(min=1, max=4 * 1024 * 1024),
    ] = 4 * 1024 * 1024,
    max_bundle_bytes: Annotated[
        int,
        typer.Option(min=1, max=16 * 1024 * 1024),
    ] = 16 * 1024 * 1024,
) -> None:
    """Download a bounded exact-value union as disjoint, preflighted batches."""
    try:
        descriptor = download_exact_in_batches(
            dataset_uuid,
            field,
            values,
            output_dir,
            sort_fields=sort_fields,
            batch_size=batch_size,
            page_size=page_size,
            max_batches=max_batches,
            max_pages_per_batch=max_pages_per_batch,
            max_rows_per_batch=max_rows_per_batch,
            max_bundle_rows=max_bundle_rows,
            max_response_bytes=max_response_bytes,
            max_bundle_bytes=max_bundle_bytes,
        )
    except (ValueError, FileExistsError, RuntimeError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"Saved {len(descriptor['inventories'])} disjoint batches with "
        f"{descriptor['total_rows']} rows to {output_dir}"
    )


@app.command("cms-provider-records-api-manifest")
def cms_provider_records_api_manifest(
    output: Annotated[Path, typer.Argument(help="New committed source-manifest path")],
    dataset: Annotated[
        str,
        typer.Option(help="Pinned provider-record dataset: enrollment or revoked"),
    ],
    inventory: Annotated[
        Path,
        typer.Option(exists=True, dir_okay=False, help="Retained exact-NPI API inventory"),
    ],
    documentation_snapshot: Annotated[
        list[str] | None,
        typer.Option(help="Repeatable retained documentation path"),
    ] = None,
) -> None:
    """Validate and manifest a bounded CMS enrollment or revocation snapshot."""
    try:
        result = build_cms_provider_records_api_manifest(
            inventory,
            output,
            dataset_key=dataset,
            documentation_snapshots=documentation_snapshot or [],
        )
    except (ValueError, FileExistsError) as error:
        raise typer.BadParameter(str(error)) from error
    matched = result["retrieval"]["parameters"]["matched_npis"]
    missing = result["retrieval"]["parameters"]["missing_npis"]
    typer.echo(
        f"Validated {result['validation']['rows']} {dataset} rows; "
        f"matched {len(matched)} NPIs, missing {len(missing)}, and wrote {output}"
    )


@app.command("cms-version")
def cms_version(
    dataset_title: Annotated[str, typer.Argument(help="Exact CMS catalog dataset title")],
    data_year: Annotated[int, typer.Option(min=1900, max=2200)],
) -> None:
    """Resolve a pinned annual dataset UUID from the official CMS catalog."""
    try:
        version = fetch_dataset_version(dataset_title, data_year)
    except (ValueError, RuntimeError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(json.dumps(version.as_dict(), indent=2))


@app.command("part-d-load")
def part_d_load(
    manifest: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    case_id: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Load a complete Part D provider/drug manifest into PostgreSQL."""
    settings = get_settings()
    code_commit = clean_repository_commit(Path.cwd())
    release_id, rows = load_part_d_provider_drug(
        settings.database_url.get_secret_value(),
        manifest,
        code_commit=code_commit,
        pipeline_version="0.1.0",
        case_id=case_id,
    )
    typer.echo(f"Loaded {rows} rows into source release {release_id}")


@app.command("part-b-provider-api-manifest")
def part_b_provider_api_manifest(
    inventory: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Argument()],
    data_year: Annotated[int, typer.Option(min=2013, max=2200)],
    modified_at: Annotated[str | None, typer.Option()] = None,
    documentation_snapshot: Annotated[
        list[str] | None,
        typer.Option(help="Repeatable retained documentation path"),
    ] = None,
) -> None:
    """Build a source manifest for a retained Part B provider API snapshot."""
    try:
        manifest = build_part_b_provider_api_manifest(
            inventory,
            output,
            data_year=data_year,
            modified_at=modified_at,
            documentation_snapshots=documentation_snapshot or [],
        )
    except (ValueError, FileExistsError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"Wrote complete provider manifest for {manifest['validation']['rows']} rows")


@app.command("part-b-provider-service-api-manifest")
def part_b_provider_service_api_manifest(
    inventory: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Argument()],
    data_year: Annotated[int, typer.Option(min=2013, max=2200)],
    modified_at: Annotated[str | None, typer.Option()] = None,
    documentation_snapshot: Annotated[
        list[str] | None,
        typer.Option(help="Repeatable retained documentation path"),
    ] = None,
) -> None:
    """Build a source manifest for a retained Part B provider-service API snapshot."""
    try:
        manifest = build_part_b_provider_service_api_manifest(
            inventory,
            output,
            data_year=data_year,
            modified_at=modified_at,
            documentation_snapshots=documentation_snapshot or [],
        )
    except (ValueError, FileExistsError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"Wrote complete provider-service manifest for {manifest['validation']['rows']} rows"
    )


@app.command("part-b-provider-load")
def part_b_provider_load(
    manifest: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    case_id: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Load a complete Part B provider manifest into PostgreSQL."""
    settings = get_settings()
    code_commit = clean_repository_commit(Path.cwd())
    release_id, rows = load_part_b_provider(
        settings.database_url.get_secret_value(),
        manifest,
        code_commit=code_commit,
        pipeline_version="0.1.0",
        case_id=case_id,
    )
    typer.echo(f"Loaded {rows} Part B provider rows into source release {release_id}")


@app.command("part-b-provider-service-load")
def part_b_provider_service_load(
    manifest: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    case_id: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Load a complete Part B provider-service manifest into PostgreSQL."""
    settings = get_settings()
    code_commit = clean_repository_commit(Path.cwd())
    release_id, rows = load_part_b_provider_service(
        settings.database_url.get_secret_value(),
        manifest,
        code_commit=code_commit,
        pipeline_version="0.1.0",
        case_id=case_id,
    )
    typer.echo(f"Loaded {rows} Part B provider-service rows into source release {release_id}")


@app.command("part-d-api-manifest")
def part_d_api_manifest(
    inventory: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Argument()],
    data_year: Annotated[int, typer.Option(min=2013, max=2200)],
    modified_at: Annotated[str | None, typer.Option()] = None,
    documentation_snapshot: Annotated[
        list[str] | None,
        typer.Option(help="Repeatable retained documentation path"),
    ] = None,
) -> None:
    """Build and validate a complete source manifest from a Part D API inventory."""
    manifest = build_part_d_api_manifest(
        inventory,
        output,
        data_year=data_year,
        modified_at=modified_at,
        documentation_snapshots=documentation_snapshot or [],
    )
    typer.echo(f"Wrote complete manifest for {manifest['validation']['rows']} rows to {output}")


@app.command("analysis-sql")
def analysis_sql(
    query: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Argument()],
    case_id: Annotated[str, typer.Option()],
    name: Annotated[str, typer.Option()],
    parameter: Annotated[
        list[str] | None,
        typer.Option(
            "--parameter",
            help=(
                "Repeatable SQL parameter KEY=VALUE; source_release_ids is bound from "
                "--source-release-id"
            ),
        ),
    ] = None,
    source_release_id: Annotated[
        list[int] | None,
        typer.Option("--source-release-id", min=1),
    ] = None,
    notes: Annotated[str | None, typer.Option()] = None,
    statement_timeout_seconds: Annotated[
        int,
        typer.Option(min=1, max=3_600, help="Database statement timeout"),
    ] = 120,
    max_output_rows: Annotated[
        int,
        typer.Option(min=1, help="Maximum CSV data rows"),
    ] = 100_000,
    max_output_bytes: Annotated[
        int,
        typer.Option(min=1, help="Maximum CSV bytes"),
    ] = 256 * 1024 * 1024,
) -> None:
    """Run a read-only repository SQL query and register its CSV artifact."""
    settings = get_settings()
    code_commit = clean_repository_commit(Path.cwd())
    try:
        parameters = parse_parameters(parameter or [])
        run_id, rows, artifact_hash = run_sql_analysis(
            settings.database_url.get_secret_value(),
            query,
            output,
            case_id=case_id,
            name=name,
            code_commit=code_commit,
            parameters=parameters,
            source_release_ids=source_release_id or [],
            notes=notes,
            statement_timeout_seconds=statement_timeout_seconds,
            max_output_rows=max_output_rows,
            max_output_bytes=max_output_bytes,
        )
    except (ValueError, FileExistsError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"Analysis run {run_id} wrote {rows} rows ({artifact_hash}) to {output}")


@app.command("part-b-shortlist")
def part_b_shortlist(
    input_path: Annotated[
        Path,
        typer.Argument(help="Complete CSV emitted by the Part B provider-service scan"),
    ],
    output_path: Annotated[
        Path,
        typer.Argument(help="New deterministic latest-year shortlist CSV"),
    ],
) -> None:
    """Create the maintained review shortlist from a complete Part B scan."""
    try:
        row_count, artifact_hash = generate_part_b_shortlist(input_path, output_path)
    except (ValueError, FileExistsError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"Wrote {row_count} shortlist rows ({artifact_hash}) to {output_path}")


@app.command("dmepos-anonymous-shortlist")
def dmepos_anonymous_shortlist(
    input_path: Annotated[
        Path,
        typer.Argument(help="Complete anonymous CSV emitted by the DMEPOS supplier screen"),
    ],
    output_path: Annotated[
        Path,
        typer.Argument(help="New deterministic anonymous candidate shortlist CSV"),
    ],
    max_input_bytes: Annotated[
        int, typer.Option(min=1, help="Maximum complete screen CSV size in bytes")
    ] = dmepos_shortlist_analysis.DEFAULT_MAX_INPUT_BYTES,
    max_input_rows: Annotated[
        int, typer.Option(min=1, help="Maximum complete screen data rows")
    ] = dmepos_shortlist_analysis.DEFAULT_MAX_INPUT_ROWS,
    max_output_bytes: Annotated[
        int, typer.Option(min=1, help="Maximum anonymous shortlist size in bytes")
    ] = dmepos_shortlist_analysis.DEFAULT_MAX_OUTPUT_BYTES,
    max_output_rows: Annotated[
        int, typer.Option(min=1, help="Maximum anonymous candidate rows")
    ] = dmepos_shortlist_analysis.DEFAULT_MAX_OUTPUT_ROWS,
) -> None:
    """Freeze mechanically selected NPIs before any identity lookup."""
    try:
        selected, persistent, emerging, artifact_hash = generate_dmepos_anonymous_shortlist(
            input_path,
            output_path,
            max_input_bytes=max_input_bytes,
            max_input_rows=max_input_rows,
            max_output_bytes=max_output_bytes,
            max_output_rows=max_output_rows,
        )
    except (ValueError, FileExistsError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"Wrote {selected} DMEPOS candidates ({persistent} persistent, {emerging} emerging; "
        f"{artifact_hash}) to {output_path}"
    )


@app.command("dmepos-candidate-identities")
def dmepos_candidate_identities(
    anonymous_path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            help="Previously frozen anonymous candidate shortlist CSV",
        ),
    ],
    output_path: Annotated[
        Path,
        typer.Argument(help="New exact-scope candidate identity mapping CSV"),
    ],
    case_id: Annotated[str, typer.Option(help="Research case receiving the analysis run")],
) -> None:
    """Extract identities only for validated frozen DMEPOS candidate pairs."""
    try:
        pairs, shortlist_hash, shortlist_bytes, shortlist_rows = (
            read_dmepos_anonymous_candidate_scope(
                anonymous_path,
                max_bytes=DEFAULT_MAX_ANONYMOUS_BYTES,
                max_rows=DEFAULT_MAX_ANONYMOUS_ROWS,
            )
        )
        pairs = list(pairs)
        pairs.sort(key=lambda pair: (pair[0], pair[1]))
        release_ids = sorted({release_id for _, release_id in pairs})
        settings = get_settings()
        code_commit = clean_repository_commit(Path.cwd())
        run_id, rows, artifact_hash = run_sql_analysis(
            settings.database_url.get_secret_value(),
            Path("sql/analysis/dmepos/frozen_candidate_identities.sql"),
            output_path,
            case_id=case_id,
            name="dmepos-frozen-candidate-identities-v1",
            code_commit=code_commit,
            parameters={
                "candidate_npis": postgres_array([npi for npi, _ in pairs]),
                "candidate_source_release_ids": postgres_array(
                    [release_id for _, release_id in pairs]
                ),
                "expected_data_year": "2024",
                "frozen_shortlist_sha256": shortlist_hash,
                "maximum_candidate_rows": str(DEFAULT_MAX_IDENTITY_ROWS),
            },
            source_release_ids=release_ids,
            notes=(
                "Exact identity scope derived from frozen anonymous shortlist "
                f"{shortlist_hash}; {shortlist_rows} artifact rows; {shortlist_bytes} bytes."
            ),
            statement_timeout_seconds=60,
            max_output_rows=DEFAULT_MAX_IDENTITY_ROWS,
            max_output_bytes=DEFAULT_MAX_IDENTITY_BYTES,
        )
    except (ValueError, FileExistsError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"Analysis run {run_id} wrote {rows} exact-scope DMEPOS identities "
        f"({artifact_hash}) to {output_path}"
    )


@app.command("dmepos-join-identities")
def dmepos_join_identities(
    anonymous_path: Annotated[
        Path,
        typer.Argument(help="Previously frozen anonymous candidate shortlist CSV"),
    ],
    identity_path: Annotated[
        Path,
        typer.Argument(help="Exact frozen-candidate identity mapping CSV"),
    ],
    output_path: Annotated[
        Path,
        typer.Argument(help="New identified review shortlist CSV"),
    ],
) -> None:
    """Join identities only to an already-frozen anonymous candidate artifact."""
    try:
        selected, artifact_hash = join_dmepos_shortlist_identities(
            anonymous_path,
            identity_path,
            output_path,
        )
    except (ValueError, FileExistsError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"Wrote {selected} identified DMEPOS candidates ({artifact_hash}) to {output_path}")


@app.command("dmepos-service-code-freeze")
def dmepos_service_code_freeze(
    input_path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            help="Complete registered candidate-code discovery CSV",
        ),
    ],
    output_path: Annotated[
        Path,
        typer.Argument(help="New deterministic selected-HCPCS freeze CSV"),
    ],
    focus_year: Annotated[
        int,
        typer.Option(min=2022, max=2024, help="Pinned discovery focus year"),
    ],
    top_codes_per_candidate: Annotated[
        int,
        typer.Option(min=1, max=25, help="Expected per-candidate top-code setting"),
    ] = DEFAULT_TOP_CODES_PER_CANDIDATE,
    min_cohort_code_payment: Annotated[
        str,
        typer.Option(help="Expected minimum reconstructed cohort Medicare payment"),
    ] = str(DEFAULT_MIN_COHORT_CODE_PAYMENT),
    target_candidate_visible_payment_share: Annotated[
        str,
        typer.Option(help="Expected cumulative candidate visible-payment target"),
    ] = str(DEFAULT_TARGET_CANDIDATE_VISIBLE_PAYMENT_SHARE),
    min_visible_summary_payment_coverage: Annotated[
        str,
        typer.Option(help="Expected candidate detail-to-summary payment coverage floor"),
    ] = str(DEFAULT_MIN_VISIBLE_SUMMARY_PAYMENT_COVERAGE),
    max_selected_codes: Annotated[
        int,
        typer.Option(min=1, max=100, help="Expected hard ceiling on the frozen code union"),
    ] = DEFAULT_MAX_SELECTED_CODES,
    max_input_bytes: Annotated[
        int,
        typer.Option(min=1, help="Maximum registered discovery CSV bytes"),
    ] = DEFAULT_DMEPOS_CODE_FREEZE_INPUT_BYTES,
    max_input_rows: Annotated[
        int,
        typer.Option(min=1, help="Maximum complete decomposition data rows"),
    ] = DEFAULT_DMEPOS_CODE_FREEZE_INPUT_ROWS,
    max_output_bytes: Annotated[
        int,
        typer.Option(min=1, help="Maximum frozen-code CSV bytes"),
    ] = DEFAULT_DMEPOS_CODE_FREEZE_OUTPUT_BYTES,
) -> None:
    """Freeze selected codes from a complete, registered DMEPOS decomposition."""
    try:
        minimum_payment = Decimal(min_cohort_code_payment)
        target_share = Decimal(target_candidate_visible_payment_share)
        coverage_floor = Decimal(min_visible_summary_payment_coverage)
    except InvalidOperation as error:
        raise typer.BadParameter("DMEPOS monetary/share settings must be decimals") from error
    try:
        selected, discovery_rows, artifact_hash = freeze_dmepos_service_codes(
            input_path,
            output_path,
            focus_year=focus_year,
            top_codes_per_candidate=top_codes_per_candidate,
            min_cohort_code_payment=minimum_payment,
            target_candidate_visible_payment_share=target_share,
            min_visible_summary_payment_coverage=coverage_floor,
            max_selected_codes=max_selected_codes,
            max_input_bytes=max_input_bytes,
            max_input_rows=max_input_rows,
            max_output_bytes=max_output_bytes,
        )
    except (ValueError, OSError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"Froze {selected} DMEPOS HCPCS codes from {discovery_rows} complete decomposition "
        f"rows ({artifact_hash}) to {output_path}"
    )


@app.command("dmepos-materiality-code-freeze")
def dmepos_materiality_code_freeze(
    input_path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            help="Complete registered candidate-code discovery CSV",
        ),
    ],
    output_path: Annotated[
        Path,
        typer.Argument(help="New deterministic materiality-complete HCPCS freeze CSV"),
    ],
    focus_year: Annotated[
        int,
        typer.Option(min=2022, max=2024, help="Pinned discovery focus year"),
    ],
    observed_payment_threshold: Annotated[
        str,
        typer.Option(
            help=(
                "Minimum rental-aggregated candidate reconstructed Medicare payment; "
                "defaults to the exact-cell observed-payment gate"
            )
        ),
    ] = str(DEFAULT_OBSERVED_PAYMENT_THRESHOLD),
    top_codes_per_candidate: Annotated[
        int,
        typer.Option(min=1, max=25, help="Expected discovery top-code setting"),
    ] = DEFAULT_TOP_CODES_PER_CANDIDATE,
    min_cohort_code_payment: Annotated[
        str,
        typer.Option(help="Expected discovery minimum reconstructed cohort Medicare payment"),
    ] = str(DEFAULT_MIN_COHORT_CODE_PAYMENT),
    target_candidate_visible_payment_share: Annotated[
        str,
        typer.Option(help="Expected discovery cumulative candidate visible-payment target"),
    ] = str(DEFAULT_TARGET_CANDIDATE_VISIBLE_PAYMENT_SHARE),
    min_visible_summary_payment_coverage: Annotated[
        str,
        typer.Option(help="Expected discovery detail-to-summary payment coverage floor"),
    ] = str(DEFAULT_MIN_VISIBLE_SUMMARY_PAYMENT_COVERAGE),
    max_selected_codes: Annotated[
        int,
        typer.Option(min=1, max=100, help="Expected discovery portfolio-union ceiling"),
    ] = DEFAULT_MAX_SELECTED_CODES,
    max_materiality_codes: Annotated[
        int,
        typer.Option(min=1, max=10_000, help="Hard ceiling on materiality-selected codes"),
    ] = DEFAULT_MAX_MATERIALITY_CODES,
    max_input_bytes: Annotated[
        int,
        typer.Option(min=1, help="Maximum registered discovery CSV bytes"),
    ] = DEFAULT_DMEPOS_CODE_FREEZE_INPUT_BYTES,
    max_input_rows: Annotated[
        int,
        typer.Option(min=1, help="Maximum complete decomposition data rows"),
    ] = DEFAULT_DMEPOS_CODE_FREEZE_INPUT_ROWS,
    max_output_bytes: Annotated[
        int,
        typer.Option(min=1, help="Maximum frozen-code CSV bytes"),
    ] = DEFAULT_DMEPOS_CODE_FREEZE_OUTPUT_BYTES,
) -> None:
    """Freeze all codes that could pass the focus-year exact-cell payment gate."""
    try:
        threshold = Decimal(observed_payment_threshold)
        minimum_payment = Decimal(min_cohort_code_payment)
        target_share = Decimal(target_candidate_visible_payment_share)
        coverage_floor = Decimal(min_visible_summary_payment_coverage)
    except InvalidOperation as error:
        raise typer.BadParameter("DMEPOS monetary/share settings must be decimals") from error
    try:
        selected, discovery_rows, artifact_hash = freeze_dmepos_materiality_codes(
            input_path,
            output_path,
            focus_year=focus_year,
            observed_payment_threshold=threshold,
            top_codes_per_candidate=top_codes_per_candidate,
            min_cohort_code_payment=minimum_payment,
            target_candidate_visible_payment_share=target_share,
            min_visible_summary_payment_coverage=coverage_floor,
            max_selected_codes=max_selected_codes,
            max_materiality_codes=max_materiality_codes,
            max_input_bytes=max_input_bytes,
            max_input_rows=max_input_rows,
            max_output_bytes=max_output_bytes,
        )
    except (ValueError, OSError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"Froze {selected} materiality-complete DMEPOS HCPCS codes from "
        f"{discovery_rows} complete decomposition rows ({artifact_hash}) to {output_path}"
    )


@app.command("part-b-multicode-reach-screen")
def part_b_multicode_reach_screen(
    input_path: Annotated[
        Path,
        typer.Argument(help="Complete CSV emitted by the Part B provider-service scan"),
    ],
    output_path: Annotated[
        Path,
        typer.Argument(help="New deterministic exploratory multi-code reach CSV"),
    ],
    reach_percentile_threshold: Annotated[
        str,
        typer.Option(
            "--reach-percentile-threshold",
            help="Explicit empirical-percentile threshold on the scan's 0-100 scale",
        ),
    ],
    minimum_tail_years: Annotated[
        int,
        typer.Option("--min-years", min=1, help="Minimum distinct threshold-reaching years"),
    ],
    minimum_qualifying_codes: Annotated[
        int,
        typer.Option("--min-codes", min=1, help="Minimum qualifying HCPCS codes per stable group"),
    ],
    max_input_bytes: Annotated[
        int,
        typer.Option(min=1, help="Maximum input CSV bytes retained for the bounded screen"),
    ] = DEFAULT_MAX_INPUT_BYTES,
    max_input_rows: Annotated[
        int,
        typer.Option(min=1, help="Maximum input CSV data rows retained for the bounded screen"),
    ] = DEFAULT_MAX_INPUT_ROWS,
    max_output_bytes: Annotated[
        int,
        typer.Option(min=1, help="Maximum exploratory output CSV bytes"),
    ] = DEFAULT_MAX_OUTPUT_BYTES,
) -> None:
    """Create a separate exploratory multi-code reach screen, not a fraud score."""
    try:
        row_count, group_count, npi_count, artifact_hash = generate_part_b_multicode_reach_screen(
            input_path,
            output_path,
            reach_percentile_threshold=reach_percentile_threshold,
            minimum_tail_years=minimum_tail_years,
            minimum_qualifying_codes=minimum_qualifying_codes,
            max_input_bytes=max_input_bytes,
            max_input_rows=max_input_rows,
            max_output_bytes=max_output_bytes,
        )
    except (ValueError, OSError) as error:
        raise typer.BadParameter(str(error)) from error
    group_label = "group" if group_count == 1 else "groups"
    npi_label = "NPI" if npi_count == 1 else "NPIs"
    typer.echo(
        f"Wrote {row_count} exploratory code rows across {group_count} stable {group_label} and "
        f"{npi_count} unique {npi_label} ({artifact_hash}) to {output_path}"
    )


@app.command("open-payments-organization-analysis")
def open_payments_organization_analysis(
    output_dir: Annotated[Path, typer.Argument()],
    case_id: Annotated[str, typer.Option(help="Existing CASE-NNNN identifier")],
    organization_id: Annotated[
        str,
        typer.Option(help="Exact Open Payments paying-entity ID"),
    ],
    from_year: Annotated[int, typer.Option(min=2013, max=2200)],
    to_year: Annotated[int, typer.Option(min=2013, max=2200)],
    payment_nature: Annotated[
        str,
        typer.Option(help="Exact payment nature used for both roster-transition analyses"),
    ],
    source_release_id: Annotated[
        list[int] | None,
        typer.Option(
            "--source-release-id",
            min=1,
            help="Repeat once for each loaded program-year General Payment release",
        ),
    ] = None,
) -> None:
    """Run the standard Open Payments organization analyses as one bounded bundle."""
    settings = get_settings()
    try:
        result = run_open_payments_organization_analysis(
            settings.database_url.get_secret_value(),
            output_dir,
            case_id=case_id,
            organization_id=organization_id,
            from_year=from_year,
            to_year=to_year,
            source_release_ids=source_release_id or [],
            payment_nature=payment_nature,
        )
    except (ValueError, FileExistsError, FileNotFoundError) as error:
        incomplete_path = output_dir / INCOMPLETE_FILENAME
        if incomplete_path.exists():
            raise typer.BadParameter(
                f"Open Payments organization analysis failed: {error}. "
                f"Partial outputs were retained; inspect {incomplete_path}."
            ) from error
        raise typer.BadParameter(str(error)) from error
    except Exception as error:
        incomplete_path = output_dir / INCOMPLETE_FILENAME
        detail = (
            f" Partial outputs were retained; inspect {incomplete_path}."
            if incomplete_path.exists()
            else ""
        )
        raise typer.BadParameter(
            f"Open Payments organization analysis failed: {type(error).__name__}: {error}.{detail}"
        ) from error

    typer.echo(
        f"Open Payments organization bundle used commit {result.code_commit} and "
        f"source releases {', '.join(map(str, result.source_release_ids))}."
    )
    for artifact in result.artifacts:
        typer.echo(
            f"Analysis run {artifact.analysis_run_id} wrote {artifact.rows} rows "
            f"({artifact.sha256}) to {artifact.path}"
        )
    typer.echo(f"Wrote bundle provenance to {result.manifest_path}")


@app.command("part-d-geography-api-manifest")
def part_d_geography_api_manifest(
    inventory: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Argument()],
    data_year: Annotated[int, typer.Option(min=2013, max=2200)],
    modified_at: Annotated[str | None, typer.Option()] = None,
    documentation_snapshot: Annotated[
        list[str] | None,
        typer.Option(help="Repeatable retained documentation path"),
    ] = None,
) -> None:
    """Build a source manifest from a Part D geography/drug API inventory."""
    manifest = build_part_d_geography_api_manifest(
        inventory,
        output,
        data_year=data_year,
        modified_at=modified_at,
        documentation_snapshots=documentation_snapshot or [],
    )
    typer.echo(f"Wrote complete manifest for {manifest['validation']['rows']} rows to {output}")


@app.command("part-d-geography-load")
def part_d_geography_load(
    manifest: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    case_id: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Load a complete Part D geography/drug manifest into PostgreSQL."""
    settings = get_settings()
    code_commit = clean_repository_commit(Path.cwd())
    release_id, rows = load_part_d_geography_drug(
        settings.database_url.get_secret_value(),
        manifest,
        code_commit=code_commit,
        pipeline_version="0.1.0",
        case_id=case_id,
    )
    typer.echo(f"Loaded {rows} geography rows into source release {release_id}")


@app.command("part-d-first-pass")
def part_d_first_pass(
    output_dir: Annotated[Path, typer.Argument()],
    case_id: Annotated[str, typer.Option(help="Existing CASE-NNNN identifier")],
    npi: Annotated[str, typer.Option(help="Ten-digit candidate prescriber NPI")],
    brand: Annotated[str, typer.Option(help="Exact Part D brand name")],
    generic: Annotated[str, typer.Option(help="Exact Part D generic name")],
    from_year: Annotated[int, typer.Option(min=2013, max=2200)],
    to_year: Annotated[int, typer.Option(min=2013, max=2200)],
    leader_limit: Annotated[
        int,
        typer.Option(min=1, max=100, help="Leaders and churn rows retained per year"),
    ] = 25,
) -> None:
    """Build a bounded first-pass bundle from already-loaded Part D releases."""
    settings = get_settings()
    try:
        result = run_part_d_first_pass(
            settings.database_url.get_secret_value(),
            output_dir,
            case_id=case_id,
            candidate_npi=npi,
            brand_name=brand,
            generic_name=generic,
            from_year=from_year,
            to_year=to_year,
            leader_limit=leader_limit,
        )
    except (ValueError, FileExistsError, FileNotFoundError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"Wrote first-pass bundle with {len(result.analysis_run_ids)} registered analyses "
        f"to {result.output_dir}"
    )
    if result.warnings:
        typer.echo(f"Coverage warnings: {len(result.warnings)}")
