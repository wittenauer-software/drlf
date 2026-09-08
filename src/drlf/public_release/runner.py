from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from drlf.public_release.bounds import Budget, ScanFailure
from drlf.public_release.checks import observed_inventory_sha256, run_content_checks
from drlf.public_release.filesystem import read_staging_filesystem
from drlf.public_release.git_tree import read_committed_tree
from drlf.public_release.models import Finding, PublicReleaseMode, PublicReleaseReport
from drlf.public_release.policy import (
    load_approved_inventory,
    load_deny_inventory,
    load_policy,
    validate_report_document,
)

CHECKER_VERSION = "1.1.0"
_SCOPE_EXCLUSIONS = (
    (
        "Archive contents are not inspected; recognized archive and opaque-file signatures, "
        "NUL bytes, and non-UTF-8 content are rejected."
    ),
    "External links are not requested; only basic deterministic Markdown file targets are checked.",
    "Repository history and other refs are not inspected.",
    (
        "Hosted issues, pull-request refs, releases, packages, attachments, caches, and settings "
        "are not inspected."
    ),
    (
        "Automated checks cannot prove the absence of PHI, personal data, real identities, or "
        "legal risk."
    ),
    (
        "Generic contact detection is limited to email-like values and configured exact deny "
        "rules; phone numbers and postal addresses still require human review."
    ),
)


def _resolved_nonexistent(path: Path) -> Path:
    return path.parent.resolve() / path.name


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _validate_locations(
    target: Path,
    control_paths: tuple[Path, ...],
    report_path: Path,
) -> tuple[Path, Path]:
    target_root = Path(os.path.abspath(target))
    if not target_root.is_dir():
        raise ValueError("public-release target must be an existing directory")
    containment_root = target_root.resolve()
    if report_path.exists():
        raise ValueError("refusing to overwrite an existing public-release report")
    if not report_path.parent.is_dir():
        raise ValueError("public-release report parent directory does not exist")
    resolved_report = _resolved_nonexistent(report_path)
    if _is_within(resolved_report, containment_root):
        raise ValueError("public-release report must be outside the candidate target")
    for path in control_paths:
        resolved = path.resolve()
        if _is_within(resolved, containment_root):
            raise ValueError(
                "public-release control documents must be outside the candidate target"
            )
        if resolved == resolved_report:
            raise ValueError("public-release report cannot replace a control document")
    return target_root, resolved_report


def _write_report(path: Path, report: PublicReleaseReport) -> None:
    document = report.as_dict()
    validate_report_document(document)
    serialized = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
    except FileExistsError as error:
        raise ValueError("refusing to overwrite an existing public-release report") from error
    except OSError as error:
        raise ValueError(
            f"unable to write public-release report ({type(error).__name__})"
        ) from error


def run_public_release_check(
    mode: PublicReleaseMode | str,
    target: Path,
    *,
    policy_path: Path,
    approved_inventory_path: Path,
    deny_inventory_path: Path,
    report_path: Path,
    commit: str | None = None,
) -> PublicReleaseReport:
    """Run one bounded local candidate check and write a non-overwriting JSON report."""
    try:
        mode = PublicReleaseMode(mode)
    except ValueError as error:
        raise ValueError("unsupported public-release checker mode") from error
    target_root, resolved_report = _validate_locations(
        target,
        (policy_path, approved_inventory_path, deny_inventory_path),
        report_path,
    )
    policy, policy_sha256 = load_policy(policy_path)
    inventory, inventory_sha256 = load_approved_inventory(approved_inventory_path, policy)
    deny_inventory, deny_sha256 = load_deny_inventory(deny_inventory_path, policy)
    if inventory_sha256 != policy.approved_inventory_sha256:
        raise ValueError("approved candidate inventory does not match the policy-bound SHA-256")
    if deny_sha256 != policy.deny_inventory_sha256:
        raise ValueError("private deny inventory does not match the policy-bound SHA-256")
    if mode is PublicReleaseMode.STAGING_FILESYSTEM and commit is not None:
        raise ValueError("--commit is not permitted in staging-filesystem mode")
    if mode is PublicReleaseMode.COMMITTED_TREE and commit is None:
        raise ValueError("--commit is required in committed-tree mode")

    budget = Budget.start(policy.bounds)
    snapshot = None
    scan_finding: Finding | None = None
    incomplete = False
    try:
        if mode is PublicReleaseMode.STAGING_FILESYSTEM:
            snapshot = read_staging_filesystem(target_root, policy, inventory, budget)
        else:
            snapshot = read_committed_tree(target_root, commit or "", policy, inventory, budget)
    except ScanFailure as error:
        approved_paths = {record.path for record in inventory.files}
        scan_finding = Finding(
            check="candidate-read",
            code=error.code,
            path=error.path if error.path in approved_paths else None,
            rule_id=None,
        )
        incomplete = error.incomplete

    if snapshot is None:
        findings = (scan_finding,) if scan_finding is not None else ()
        observed_hash = None
        subject: dict[str, str | bool | int | None] = {
            "root": str(target_root),
            "commit": commit,
            "tree": None,
        }
        stable = False
        clean_state = "not-established"
    else:
        try:
            content_result = run_content_checks(
                snapshot, policy, inventory, deny_inventory, budget
            )
        except ScanFailure as error:
            content_result = None
            findings = (
                Finding(
                    check="content-checks",
                    code=error.code,
                    path=None,
                    rule_id=None,
                ),
            )
            incomplete = True
        if content_result is not None:
            findings = content_result.findings
            incomplete = content_result.truncated
            if content_result.truncated:
                cap_finding = Finding(
                    check="reporting",
                    code="finding-cap-exceeded",
                    path=None,
                    rule_id=None,
                )
                findings = tuple(
                    sorted((*findings[:-1], cap_finding), key=lambda item: item.code)
                )
        observed_hash = observed_inventory_sha256(snapshot)
        subject = snapshot.subject
        stable = snapshot.stable
        clean_state = snapshot.clean_state

    if incomplete:
        outcome = "incomplete"
    elif findings:
        outcome = "fail"
    else:
        outcome = "pass"
    decision_report = asdict(policy.decisions)
    decision_report["approved_public_contacts"] = list(policy.decisions.approved_public_contacts)
    report = PublicReleaseReport(
        schema_version=1,
        checker_version=CHECKER_VERSION,
        mode=mode.value,
        subject=subject,
        outcome=outcome,
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        policy_sha256=policy_sha256,
        decisions=decision_report,
        expected_approved_inventory_sha256=policy.approved_inventory_sha256,
        expected_deny_inventory_sha256=policy.deny_inventory_sha256,
        control_bindings_verified=True,
        approved_inventory_id=inventory.inventory_id,
        approved_inventory_sha256=inventory_sha256,
        deny_inventory_id=deny_inventory.inventory_id,
        deny_inventory_sha256=deny_sha256,
        observed_inventory_sha256=observed_hash,
        candidate_stable=stable,
        clean_state=clean_state,
        history_checked=False,
        archive_inspection="disabled-recognized-signatures-rejected",
        network_checks="disabled",
        configured_bounds=asdict(policy.bounds),
        usage=snapshot.usage if snapshot is not None else budget.usage(),
        scope_exclusions=_SCOPE_EXCLUSIONS,
        findings=tuple(findings),
        human_review_required=True,
        publication_authorized=False,
    )
    _write_report(resolved_report, report)
    return report
