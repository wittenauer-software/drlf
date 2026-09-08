from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml

CASE_DIRECTORY_PATTERN = re.compile(r"^(CASE-\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*$")
CASE_ID_PATTERN = re.compile(r"^CASE-\d{4}$")
STATUSES = {"triage", "active", "monitor", "explained", "dismissed", "counsel-review"}
DECISIONS = {"continue", "pivot", "stop", "escalate"}
CONTINUING_STATUSES = {"triage", "active"}
STOPPED_STATUSES = {"monitor", "explained", "dismissed"}
TRIAGE_LANES = {
    "identity",
    "semantics",
    "magnitude",
    "peer robustness",
    "operational plausibility",
    "policy and clinical context",
    "ordinary explanations",
    "payment relevance",
    "reproducibility",
    "investigative next step",
}
REQUIRED_FILES = {
    "case.yaml",
    "hypothesis.md",
    "evidence-log.csv",
    "sources.md",
    "decision-log.md",
    "retrospective.md",
    "analysis/context-review.md",
    "analysis/research-cutoff.md",
    "dossier/report.md",
    "dossier/report.yaml",
}
ACTION_FIELDS = {
    "question",
    "source",
    "distinguishing_outcomes",
    "triage_lane",
    "disposition_change",
    "effort_cap",
}
AUTHORIZATION_FIELDS = {
    "contact_subject",
    "publish",
    "report_to_government",
    "contact_counsel",
}


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_iso_date(value: Any) -> bool:
    if isinstance(value, date):
        return True
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _validate_authorization(errors: list[str], case_id: str, case: dict[str, Any]) -> None:
    authorization = case.get("authorization")
    if not isinstance(authorization, dict):
        errors.append(f"{case_id}: authorization must be a mapping")
        return
    if set(authorization) != AUTHORIZATION_FIELDS:
        errors.append(
            f"{case_id}: authorization must contain exactly "
            + ", ".join(sorted(AUTHORIZATION_FIELDS))
        )
    for field in sorted(AUTHORIZATION_FIELDS & set(authorization)):
        if not isinstance(authorization[field], bool):
            errors.append(f"{case_id}: authorization.{field} must be boolean")


def _validate_report_manifest(errors: list[str], directory_id: str, case_directory: Path) -> None:
    report_path = case_directory / "dossier" / "report.yaml"
    if not report_path.is_file():
        return
    try:
        report = yaml.safe_load(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        errors.append(f"{directory_id}: cannot read dossier/report.yaml: {exc}")
        return
    if not isinstance(report, dict):
        errors.append(f"{directory_id}: dossier/report.yaml must contain a mapping")
        return
    if report.get("case_id") != directory_id:
        errors.append(f"{directory_id}: dossier/report.yaml case_id must match the directory ID")
    if not isinstance(report.get("transmission_authorized"), bool):
        errors.append(
            f"{directory_id}: dossier/report.yaml transmission_authorized must be boolean"
        )


def _validate_text_list(
    errors: list[str], case_id: str, cutoff: dict[str, Any], field: str
) -> list[str] | None:
    value = cutoff.get(field)
    if not isinstance(value, list) or any(not _nonempty_text(item) for item in value):
        errors.append(f"{case_id}: research_cutoff.{field} must be a list of nonempty strings")
        return None
    return value


def _validate_cutoff(errors: list[str], case_id: str, case: dict[str, Any]) -> None:
    status = case.get("status")
    cutoff = case.get("research_cutoff")
    if not isinstance(cutoff, dict):
        errors.append(f"{case_id}: research_cutoff must be a mapping")
        return

    if not _is_iso_date(cutoff.get("reviewed_at")):
        errors.append(f"{case_id}: research_cutoff.reviewed_at must be an ISO date")

    decision = cutoff.get("decision")
    if decision not in DECISIONS:
        errors.append(f"{case_id}: research_cutoff.decision must be one of {sorted(DECISIONS)}")
    if not _nonempty_text(cutoff.get("basis")):
        errors.append(f"{case_id}: research_cutoff.basis must be nonempty")

    residual_routes = _validate_text_list(errors, case_id, cutoff, "residual_routes")
    _validate_text_list(errors, case_id, cutoff, "public_data_ceiling")
    revisit_triggers = _validate_text_list(errors, case_id, cutoff, "revisit_triggers")

    no_change = cutoff.get("consecutive_no_change_actions")
    if not isinstance(no_change, int) or isinstance(no_change, bool) or no_change < 0:
        errors.append(
            f"{case_id}: research_cutoff.consecutive_no_change_actions "
            "must be a nonnegative integer"
        )

    action = cutoff.get("next_bounded_action")
    if status in CONTINUING_STATUSES:
        next_actions = case.get("next_actions")
        if (
            not isinstance(next_actions, list)
            or len(next_actions) != 1
            or not _nonempty_text(next_actions[0])
        ):
            errors.append(f"{case_id}: {status} status requires exactly one bounded next action")
        if decision not in {"continue", "pivot"}:
            errors.append(
                f"{case_id}: {status} status requires research_cutoff.decision continue or pivot"
            )
        if not isinstance(action, dict):
            errors.append(f"{case_id}: {status} status requires next_bounded_action")
        else:
            missing = sorted(ACTION_FIELDS - set(action))
            if missing:
                errors.append(
                    f"{case_id}: next_bounded_action missing fields: {', '.join(missing)}"
                )
            for field in sorted(ACTION_FIELDS):
                if field in action and not _nonempty_text(action[field]):
                    errors.append(f"{case_id}: next_bounded_action.{field} must be nonempty")
            lane = action.get("triage_lane")
            if _nonempty_text(lane) and lane.lower() not in TRIAGE_LANES:
                errors.append(
                    f"{case_id}: next_bounded_action.triage_lane is not a recognized lane"
                )
    elif status in STOPPED_STATUSES:
        if decision != "stop":
            errors.append(f"{case_id}: {status} status requires research_cutoff.decision stop")
        if action is not None:
            errors.append(f"{case_id}: {status} status requires null next_bounded_action")
        if revisit_triggers is not None and not revisit_triggers:
            errors.append(f"{case_id}: {status} status requires at least one revisit trigger")
        if case.get("next_actions") != []:
            errors.append(f"{case_id}: {status} status requires an empty next_actions list")
        if status == "monitor" and residual_routes is not None and not residual_routes:
            errors.append(f"{case_id}: monitor status requires at least one residual route")
        if status in {"explained", "dismissed"} and residual_routes:
            errors.append(f"{case_id}: {status} status requires no residual routes")
    elif status == "counsel-review":
        if decision != "escalate":
            errors.append(
                f"{case_id}: counsel-review status requires research_cutoff.decision escalate"
            )
        if action is not None:
            errors.append(f"{case_id}: counsel-review status requires null next_bounded_action")


def validate(repository_root: Path) -> list[str]:
    errors: list[str] = []
    cases_root = repository_root / "research" / "cases"
    if not cases_root.is_dir():
        return [f"missing cases directory: {cases_root}"]

    case_directories = sorted(
        path for path in cases_root.iterdir() if path.is_dir() and path.name.startswith("CASE-")
    )
    directories_by_id: dict[str, list[str]] = {}
    for case_directory in case_directories:
        match = CASE_DIRECTORY_PATTERN.fullmatch(case_directory.name)
        if match:
            directories_by_id.setdefault(match.group(1), []).append(case_directory.name)
    for case_id, directory_names in sorted(directories_by_id.items()):
        if len(directory_names) > 1:
            errors.append(f"{case_id}: duplicate case ID appears in: {', '.join(directory_names)}")

    for case_directory in case_directories:
        match = CASE_DIRECTORY_PATTERN.fullmatch(case_directory.name)
        directory_id = match.group(1) if match else case_directory.name
        if match is None:
            errors.append(
                f"{directory_id}: directory name must use CASE-NNNN-lowercase-hyphenated-slug"
            )
        elif directory_id == "CASE-0000":
            errors.append("CASE-0000: case identifier is reserved and cannot be assigned")
        for relative_path in sorted(REQUIRED_FILES):
            if not (case_directory / relative_path).is_file():
                errors.append(f"{directory_id}: missing required file {relative_path}")

        case_path = case_directory / "case.yaml"
        if not case_path.is_file():
            continue
        try:
            case = yaml.safe_load(case_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            errors.append(f"{directory_id}: cannot read case.yaml: {exc}")
            continue
        if not isinstance(case, dict):
            errors.append(f"{directory_id}: case.yaml must contain a mapping")
            continue

        case_id = case.get("id")
        if not isinstance(case_id, str) or CASE_ID_PATTERN.fullmatch(case_id) is None:
            errors.append(f"{directory_id}: case.yaml id must use CASE-NNNN")
        elif case_id == "CASE-0000" and directory_id != "CASE-0000":
            errors.append(f"{directory_id}: case.yaml id CASE-0000 is reserved")
        if case_id != directory_id:
            errors.append(f"{directory_id}: case.yaml id must match the directory ID")
        if not _nonempty_text(case.get("title")):
            errors.append(f"{directory_id}: title must be nonempty")
        if not _nonempty_text(case.get("primary_question")):
            errors.append(f"{directory_id}: primary_question must be nonempty")
        for field in ("created_at", "updated_at"):
            if not _is_iso_date(case.get(field)):
                errors.append(f"{directory_id}: {field} must be an ISO date")
        if case.get("status") not in STATUSES:
            errors.append(f"{directory_id}: invalid case status {case.get('status')!r}")
        if not isinstance(case.get("next_actions"), list):
            errors.append(f"{directory_id}: next_actions must be a list")
        _validate_authorization(errors, directory_id, case)
        _validate_cutoff(errors, directory_id, case)
        _validate_report_manifest(errors, directory_id, case_directory)

    return errors


def main() -> int:
    repository_root = (
        Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[4]
    )
    errors = validate(repository_root)
    if errors:
        for error in errors:
            print(error)
        return 1
    print("Research case validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
