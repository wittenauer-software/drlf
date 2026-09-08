from __future__ import annotations

import importlib.util
import re
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

CASE_DIRECTORY_PATTERN = re.compile(r"^CASE-(\d{4})(?:-|$)")
CASE_ID_PATTERN = re.compile(r"^CASE-(\d{4})$")
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

NONPUBLIC_EVIDENCE_PLACEHOLDER = "[Evidence an authorized investigator would need]"
RETROSPECTIVE_CHANGE_PLACEHOLDER = (
    "[Observation, correction, or evidence that materially changed the working model]"
)
REPORT_SUMMARY_PLACEHOLDER = (
    "[State the research question, disposition, and most decision-relevant evidence without "
    "alleging fraud.]"
)
REPORT_DISPOSITION_PLACEHOLDER = (
    "[State the disposition and map each unresolved question to the specific nonpublic record, "
    "field, and likely custodian an authorized investigator would need.]"
)

TRIAGE_LANES = (
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
)


@dataclass(frozen=True)
class CaseInitialization:
    case_id: str
    destination: Path


def validate_case_workspaces(repository_root: Path) -> tuple[str, ...]:
    """Run the managed case validator through the active Python environment."""
    root = repository_root.resolve()
    validator_path = (
        root / ".agents" / "skills" / "manage-research-cases" / "scripts" / "validate_cases.py"
    )
    if not validator_path.is_file():
        raise FileNotFoundError(f"Missing managed case validator: {validator_path}")
    specification = importlib.util.spec_from_file_location(
        "drlf_managed_case_validator", validator_path
    )
    if specification is None or specification.loader is None:
        raise ValueError(f"Cannot load managed case validator: {validator_path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    validator = getattr(module, "validate", None)
    if not callable(validator):
        raise ValueError("Managed case validator does not expose validate(repository_root)")
    errors = validator(root)
    if not isinstance(errors, list) or any(not isinstance(error, str) for error in errors):
        raise ValueError("Managed case validator returned an invalid result")
    return tuple(errors)


@contextmanager
def _case_init_lock(cases_root: Path):
    lock_directory = cases_root / ".case-init.lock"
    try:
        lock_directory.mkdir()
    except FileExistsError as error:
        raise FileExistsError(
            "Another case initialization is running, or research/cases/.case-init.lock is stale"
        ) from error
    try:
        yield
    finally:
        lock_directory.rmdir()


def _required_text(label: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be blank")
    return normalized


def _next_case_id(cases_root: Path) -> str:
    used_numbers = {
        int(match.group(1))
        for path in cases_root.iterdir()
        if path.is_dir() and (match := CASE_DIRECTORY_PATTERN.match(path.name))
    }
    next_number = max(used_numbers, default=0) + 1
    if next_number > 9999:
        raise ValueError("No four-digit case identifiers remain")
    return f"CASE-{next_number:04d}"


def _replace_template_text(destination: Path, replacements: dict[str, str]) -> None:
    for path in sorted(item for item in destination.rglob("*") if item.is_file()):
        if path.suffix not in {".md", ".csv"}:
            continue
        content = path.read_text(encoding="utf-8")
        for placeholder, replacement in replacements.items():
            content = content.replace(placeholder, replacement)
        path.write_text(content, encoding="utf-8", newline="\n")


def initialize_case(
    repository_root: Path,
    *,
    slug: str,
    title: str,
    question: str,
    observation: str,
    hypothesis: str,
    supporting_evidence: str,
    refuting_evidence: str,
    next_action: str,
    action_source: str,
    distinguishing_outcomes: str,
    triage_lane: str,
    disposition_change: str,
    effort_cap: str,
    cutoff_basis: str,
    programs: tuple[str, ...] = (),
    ordinary_explanations: tuple[str, ...] = (),
    case_id: str | None = None,
    created_on: date | None = None,
) -> CaseInitialization:
    """Create one complete, non-overwriting case workspace from the managed template."""
    root = repository_root.resolve()
    cases_root = root / "research" / "cases"
    template_root = (
        root / ".agents" / "skills" / "manage-research-cases" / "assets" / "case-template"
    )
    if not cases_root.is_dir():
        raise FileNotFoundError(f"Missing case directory: {cases_root}")
    if not template_root.is_dir():
        raise FileNotFoundError(f"Missing managed case template: {template_root}")

    normalized_slug = slug.strip()
    if not SLUG_PATTERN.fullmatch(normalized_slug):
        raise ValueError("slug must contain lowercase letters, digits, and single hyphens only")
    if len(normalized_slug) > 80:
        raise ValueError("slug must be 80 characters or fewer")

    normalized_lane = triage_lane.strip().lower()
    if normalized_lane not in TRIAGE_LANES:
        raise ValueError(f"triage-lane must be one of: {', '.join(TRIAGE_LANES)}")

    normalized_programs = tuple(_required_text("program", value) for value in programs)
    normalized_alternatives = tuple(
        _required_text("ordinary explanation", value) for value in ordinary_explanations
    )
    intake_date = created_on or date.today()
    required_values = {
        "title": _required_text("title", title),
        "question": _required_text("question", question),
        "observation": _required_text("observation", observation),
        "hypothesis": _required_text("hypothesis", hypothesis),
        "supporting_evidence": _required_text("supporting-evidence", supporting_evidence),
        "refuting_evidence": _required_text("refuting-evidence", refuting_evidence),
        "next_action": _required_text("next-action", next_action),
        "action_source": _required_text("action-source", action_source),
        "distinguishing_outcomes": _required_text(
            "distinguishing-outcomes", distinguishing_outcomes
        ),
        "disposition_change": _required_text("disposition-change", disposition_change),
        "effort_cap": _required_text("effort-cap", effort_cap),
        "cutoff_basis": _required_text("cutoff-basis", cutoff_basis),
    }

    with _case_init_lock(cases_root):
        next_case_id = _next_case_id(cases_root)
        normalized_case_id = case_id.strip().upper() if case_id else next_case_id
        if not CASE_ID_PATTERN.fullmatch(normalized_case_id):
            raise ValueError("case-id must use the form CASE-NNNN")
        if normalized_case_id != next_case_id:
            raise ValueError(
                f"explicit case-id must equal the next sequential identifier, {next_case_id}"
            )
        case_number = int(normalized_case_id.removeprefix("CASE-"))
        if case_number == 0:
            raise ValueError("CASE-0000 is reserved and cannot be assigned")
        existing_for_id = sorted(
            path
            for path in cases_root.iterdir()
            if path.is_dir()
            and (path.name == normalized_case_id or path.name.startswith(f"{normalized_case_id}-"))
        )
        if existing_for_id:
            raise FileExistsError(
                f"{normalized_case_id} is already assigned to {existing_for_id[0].name}"
            )

        destination = cases_root / f"{normalized_case_id}-{normalized_slug}"
        try:
            destination.mkdir()
        except FileExistsError as error:
            raise FileExistsError(
                f"Refusing to overwrite existing case workspace: {destination}"
            ) from error

        try:
            shutil.copytree(template_root, destination, dirs_exist_ok=True)
            generic_intake_note = "Not yet established at intake."
            replacements = {
                "[CASE-NNNN]": normalized_case_id,
                "CASE-NNNN": normalized_case_id,
                "[Short neutral title]": required_values["title"],
                "[YYYY-MM-DD]": intake_date.isoformat(),
                "[Falsifiable research question]": required_values["question"],
                "[One-sentence summary of next_bounded_action]": required_values["next_action"],
                "[Why another bounded public action could change the disposition]": (
                    required_values["cutoff_basis"]
                ),
                "[Precise unresolved question]": required_values["next_action"],
                "[Specific lawful and accessible public source]": required_values["action_source"],
                "[How plausible results distinguish competing explanations]": (
                    required_values["distinguishing_outcomes"]
                ),
                "[Triage lane this action could change]": normalized_lane,
                "[How a plausible result could change routing]": (
                    required_values["disposition_change"]
                ),
                "[Elapsed time, query/page count, download size, compute, and fee cap]": (
                    required_values["effort_cap"]
                ),
                "[State only what was observed and where.]": required_values["observation"],
                "[State a falsifiable explanation for the observation.]": (
                    required_values["hypothesis"]
                ),
                "[Expected evidence]": (required_values["supporting_evidence"]),
                "[Expected counterevidence]": (required_values["refuting_evidence"]),
                "[Alternative explanation]": (
                    "\n- ".join(normalized_alternatives)
                    if normalized_alternatives
                    else "Plausible ordinary explanations remain to be tested."
                ),
                NONPUBLIC_EVIDENCE_PLACEHOLDER: (
                    "Identify the exact nonpublic record only if public evidence leaves a "
                    "material gap."
                ),
                "[Why the observation merits review.]": required_values["cutoff_basis"],
                "[What must be resolved next.]": required_values["next_action"],
                "[What the research established, weakened, or left unresolved]": (
                    "The case has been initialized; no research conclusion has been reached."
                ),
                RETROSPECTIVE_CHANGE_PLACEHOLDER: ("No post-intake change has been recorded."),
                "[Dataset quirk, policy change, code meaning, workflow, or ordinary explanation]": (
                    "No transferable lesson has been established."
                ),
                "[What to check early, avoid repeating, or deliberately challenge]": (
                    "Review applicable learning cards and test ordinary explanations early."
                ),
                (
                    "[New dataset year, policy change, contradictory case, or other event that "
                    "should cause review]"
                ): ("Define specific triggers if this case later moves to a stopped disposition."),
                REPORT_SUMMARY_PLACEHOLDER: ("Draft not prepared; the case remains in triage."),
                (
                    "[Identify datasets, years, populations, codes, suppression, and material "
                    "limitations.]"
                ): (generic_intake_note),
                (
                    "[Describe reproducible measures and comparisons, distinguishing facts from "
                    "inference.]"
                ): (generic_intake_note),
                "[Present supported, weakened, and unresolved ordinary explanations.]": (
                    generic_intake_note
                ),
                REPORT_DISPOSITION_PLACEHOLDER: (generic_intake_note),
                "[Link source manifests, analysis runs, queries, parameters, and artifacts.]": (
                    generic_intake_note
                ),
            }
            _replace_template_text(destination, replacements)

            case_path = destination / "case.yaml"
            try:
                case = yaml.safe_load(case_path.read_text(encoding="utf-8"))
            except yaml.YAMLError as error:
                raise ValueError("Managed case template case.yaml contains invalid YAML") from error
            if not isinstance(case, dict):
                raise ValueError("Managed case template case.yaml must contain a mapping")
            case["id"] = normalized_case_id
            case["title"] = required_values["title"]
            case["created_at"] = intake_date.isoformat()
            case["updated_at"] = intake_date.isoformat()
            case["primary_question"] = required_values["question"]
            case["programs"] = list(normalized_programs)
            case["alternative_explanations"] = list(normalized_alternatives)
            case["next_actions"] = [required_values["next_action"]]
            cutoff = case.get("research_cutoff")
            if not isinstance(cutoff, dict):
                raise ValueError(
                    "Managed case template case.yaml research_cutoff must contain a mapping"
                )
            cutoff["reviewed_at"] = intake_date.isoformat()
            cutoff["basis"] = required_values["cutoff_basis"]
            cutoff["next_bounded_action"] = {
                "question": required_values["next_action"],
                "source": required_values["action_source"],
                "distinguishing_outcomes": required_values["distinguishing_outcomes"],
                "triage_lane": normalized_lane,
                "disposition_change": required_values["disposition_change"],
                "effort_cap": required_values["effort_cap"],
            }
            case_path.write_text(
                yaml.safe_dump(case, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
                newline="\n",
            )

            report_manifest_path = destination / "dossier" / "report.yaml"
            try:
                report_manifest = yaml.safe_load(report_manifest_path.read_text(encoding="utf-8"))
            except yaml.YAMLError as error:
                raise ValueError(
                    "Managed case template dossier/report.yaml contains invalid YAML"
                ) from error
            if not isinstance(report_manifest, dict):
                raise ValueError("Managed case template dossier/report.yaml must contain a mapping")
            report_manifest["case_id"] = normalized_case_id
            report_manifest_path.write_text(
                yaml.safe_dump(report_manifest, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
                newline="\n",
            )
        except Exception:
            shutil.rmtree(destination)
            raise

    return CaseInitialization(case_id=normalized_case_id, destination=destination)
