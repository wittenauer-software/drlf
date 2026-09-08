"""Validate learning cards, their index, and cross-case evidence lineage."""

from __future__ import annotations

import csv
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml

ALLOWED_STATUSES = {"candidate", "reviewed", "adopted", "superseded", "rejected"}
ALLOWED_TYPES = {"domain", "data", "method", "process"}
ALLOWED_CONFIDENCE = {"low", "medium", "high"}
ALLOWED_DIRECTIONS = {"supports", "weakens", "context", "unresolved"}
ALLOWED_RELIABILITY = {"primary", "authoritative-secondary", "secondary", "unverified"}
ALLOWED_LEARNING_OUTCOMES = {
    "pending",
    "supported",
    "partial",
    "contradicted",
    "not-applicable",
}
REQUIRED_KEYS = {
    "id",
    "title",
    "status",
    "type",
    "created_at",
    "updated_at",
    "confidence",
    "scope",
    "source_cases",
    "evidence_ids",
    "source_manifests",
    "authoritative_sources",
    "review_after",
    "supersedes",
    "conflicts_with",
    "adopted_in",
}
REQUIRED_SCOPE_KEYS = {"programs", "datasets", "years", "codes", "populations", "geography"}
REQUIRED_SECTIONS = {
    "# Lesson",
    "## Why it matters",
    "## Supporting evidence",
    "## Counterevidence and limits",
    "## Failed approaches",
    "## Reuse guidance",
    "## Invalidation triggers",
    "## History",
}
LEARNING_ID = re.compile(r"^LOCAL-KNOWLEDGE-\d{4}$")
CASE_ID = re.compile(r"^CASE-\d{4}$")
EVIDENCE_ID = re.compile(r"^E-\d{4}$")
QUALIFIED_EVIDENCE_ID = re.compile(r"^(CASE-\d{4}):(E-\d{4})$")


def read_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    if not match:
        raise ValueError("missing YAML front matter")
    values = yaml.safe_load(match.group(1))
    if not isinstance(values, dict):
        raise ValueError("front matter must be a mapping")
    return values, text[match.end() :]


def read_index(path: Path) -> dict[str, dict[str, Any]]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("learnings"), list):
        raise ValueError("knowledge index must contain a learnings list")
    if document.get("schema_version") != 1:
        raise ValueError("knowledge index schema_version must equal 1")
    if document.get("namespace") != "LOCAL-KNOWLEDGE":
        raise ValueError("knowledge index namespace must equal LOCAL-KNOWLEDGE")
    if document.get("origin") != "private-workspace":
        raise ValueError("knowledge index origin must equal private-workspace")
    entries: dict[str, dict[str, Any]] = {}
    for entry in document["learnings"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise ValueError("every index entry must be a mapping with an ID")
        learning_id = entry["id"]
        if learning_id in entries:
            raise ValueError(f"duplicate index ID {learning_id}")
        entries[learning_id] = entry
    return entries


def _case_directories(root: Path) -> dict[str, Path]:
    cases: dict[str, Path] = {}
    pattern = "CASE-[0-9][0-9][0-9][0-9]-*"
    for path in sorted((root / "research" / "cases").glob(pattern)):
        case_id = path.name[:9]
        if case_id in cases:
            raise ValueError(f"duplicate case workspace for {case_id}")
        cases[case_id] = path
    return cases


def _read_evidence(path: Path, root: Path) -> tuple[set[str], list[str]]:
    errors: list[str] = []
    evidence_ids: set[str] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        for line_number, row in enumerate(csv.DictReader(handle), start=2):
            evidence_id = row.get("evidence_id", "")
            prefix = f"{path.relative_to(root)}:{line_number}"
            if not EVIDENCE_ID.fullmatch(evidence_id):
                errors.append(f"{prefix}: invalid evidence ID {evidence_id!r}")
            elif evidence_id in evidence_ids:
                errors.append(f"{prefix}: duplicate evidence ID {evidence_id}")
            evidence_ids.add(evidence_id)
            if row.get("evidence_direction") not in ALLOWED_DIRECTIONS:
                errors.append(f"{prefix}: invalid evidence direction")
            if row.get("reliability") not in ALLOWED_RELIABILITY:
                errors.append(f"{prefix}: invalid reliability {row.get('reliability')!r}")
            artifact = row.get("local_artifact", "")
            artifact_hash = row.get("sha256", "")
            if bool(artifact) != bool(artifact_hash):
                errors.append(f"{prefix}: local artifact and SHA-256 must be supplied together")
            if artifact and not (root / artifact).is_file():
                errors.append(f"{prefix}: local artifact not found: {artifact}")
            if artifact_hash and not re.fullmatch(r"[0-9a-f]{64}", artifact_hash):
                errors.append(f"{prefix}: invalid SHA-256")
    return evidence_ids, errors


def _validate_card_metadata(
    path: Path, fields: dict[str, Any], body: str, root: Path
) -> list[str]:
    errors: list[str] = []
    relative = path.relative_to(root)
    missing = sorted(REQUIRED_KEYS - fields.keys())
    if missing:
        errors.append(f"{relative}: missing fields {', '.join(missing)}")
        return errors
    learning_id = fields["id"]
    if not isinstance(learning_id, str) or not LEARNING_ID.fullmatch(learning_id):
        errors.append(f"{relative}: invalid learning ID {learning_id!r}")
    elif not path.name.startswith(f"{learning_id}-"):
        errors.append(f"{relative}: filename does not start with {learning_id}-")
    if fields["status"] not in ALLOWED_STATUSES:
        errors.append(f"{relative}: invalid status {fields['status']!r}")
    if fields["type"] not in ALLOWED_TYPES:
        errors.append(f"{relative}: invalid type {fields['type']!r}")
    if fields["confidence"] not in ALLOWED_CONFIDENCE:
        errors.append(f"{relative}: invalid confidence {fields['confidence']!r}")
    for key in ("created_at", "updated_at"):
        if not isinstance(fields[key], date):
            errors.append(f"{relative}: {key} must be an ISO date")
    if fields["review_after"] is not None and not isinstance(fields["review_after"], date):
        errors.append(f"{relative}: review_after must be an ISO date or null")
    scope = fields["scope"]
    if not isinstance(scope, dict) or set(scope) != REQUIRED_SCOPE_KEYS:
        errors.append(f"{relative}: scope must contain exactly {sorted(REQUIRED_SCOPE_KEYS)}")
    elif any(not isinstance(scope[key], list) for key in REQUIRED_SCOPE_KEYS):
        errors.append(f"{relative}: every scope value must be a list")
    list_keys = (
        "source_cases",
        "evidence_ids",
        "source_manifests",
        "authoritative_sources",
        "supersedes",
        "conflicts_with",
        "adopted_in",
    )
    for key in list_keys:
        if not isinstance(fields[key], list):
            errors.append(f"{relative}: {key} must be a list")
    missing_sections = sorted(section for section in REQUIRED_SECTIONS if section not in body)
    if missing_sections:
        errors.append(f"{relative}: missing narrative sections {', '.join(missing_sections)}")
    return errors


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    cards_dir = root / "research" / "knowledge" / "local" / "cards"
    try:
        index = read_index(root / "research" / "knowledge" / "local" / "index.yaml")
        case_directories = _case_directories(root)
    except (ValueError, yaml.YAMLError) as error:
        return [str(error)]

    try:
        foundation_document = yaml.safe_load(
            (root / "foundations" / "catalog.yaml").read_text(encoding="utf-8")
        )
        foundation_records = foundation_document["records"]
        foundation_ids = {
            record["id"]
            for record in foundation_records
            if isinstance(record, dict) and isinstance(record.get("id"), str)
        }
    except (OSError, UnicodeError, KeyError, TypeError, yaml.YAMLError) as error:
        return [f"cannot read public foundation catalog: {error}"]

    evidence_by_case: dict[str, set[str]] = {}
    for case_id, case_dir in case_directories.items():
        evidence_by_case[case_id], evidence_errors = _read_evidence(
            case_dir / "evidence-log.csv", root
        )
        errors.extend(evidence_errors)

    cards: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in sorted(cards_dir.glob("LOCAL-KNOWLEDGE-*.md")):
        try:
            fields, body = read_frontmatter(path)
        except (ValueError, yaml.YAMLError) as error:
            errors.append(f"{path.relative_to(root)}: {error}")
            continue
        errors.extend(_validate_card_metadata(path, fields, body, root))
        learning_id = fields.get("id")
        if not isinstance(learning_id, str) or not LEARNING_ID.fullmatch(learning_id):
            continue
        if learning_id in cards:
            errors.append(f"duplicate card ID {learning_id}")
            continue
        cards[learning_id] = (path, fields)

    if set(index) != set(cards):
        missing_from_index = sorted(set(cards) - set(index))
        missing_cards = sorted(set(index) - set(cards))
        if missing_from_index:
            errors.append(f"cards missing from index: {', '.join(missing_from_index)}")
        if missing_cards:
            errors.append(f"index entries missing cards: {', '.join(missing_cards)}")

    for learning_id, (path, fields) in cards.items():
        entry = index.get(learning_id)
        if entry:
            for key in ("title", "status", "type", "updated_at", "source_cases"):
                if entry.get(key) != fields.get(key):
                    errors.append(f"{learning_id}: index {key} does not match card")
            expected_path = path.relative_to(root).as_posix()
            if entry.get("path") != expected_path:
                errors.append(f"{learning_id}: index path should be {expected_path}")

        for case_id in fields.get("source_cases", []):
            if not isinstance(case_id, str) or not CASE_ID.fullmatch(case_id):
                errors.append(f"{learning_id}: invalid source case {case_id!r}")
            elif case_id not in case_directories:
                errors.append(f"{learning_id}: source case not found: {case_id}")
        for reference in fields.get("evidence_ids", []):
            match = QUALIFIED_EVIDENCE_ID.fullmatch(str(reference))
            if not match:
                errors.append(f"{learning_id}: evidence must be qualified: {reference}")
                continue
            case_id, evidence_id = match.groups()
            if case_id not in fields.get("source_cases", []):
                errors.append(f"{learning_id}: evidence uses undeclared source case {case_id}")
            elif evidence_id not in evidence_by_case.get(case_id, set()):
                errors.append(f"{learning_id}: evidence not found: {reference}")
        for manifest in fields.get("source_manifests", []):
            if not (root / manifest).is_file():
                errors.append(f"{learning_id}: source manifest not found: {manifest}")
        status = fields.get("status")
        destinations = fields.get("adopted_in", [])
        if status == "adopted" and not destinations:
            errors.append(f"{learning_id}: adopted card has no adopted_in destination")
        if status != "adopted" and destinations:
            errors.append(f"{learning_id}: only adopted cards may have adopted_in destinations")
        for destination in destinations:
            destination_path = root / destination
            if not destination_path.is_file():
                errors.append(f"{learning_id}: adopted destination not found: {destination}")
            elif learning_id not in destination_path.read_text(encoding="utf-8"):
                errors.append(
                    f"{learning_id}: adopted destination lacks reciprocal ID: {destination}"
                )
        for relationship in ("supersedes", "conflicts_with"):
            for other_id in fields.get(relationship, []):
                if other_id == learning_id or other_id not in cards:
                    errors.append(f"{learning_id}: invalid {relationship} target {other_id}")

    known_learning_ids = set(cards) | foundation_ids
    for case_id, case_dir in case_directories.items():
        case_data = yaml.safe_load((case_dir / "case.yaml").read_text(encoding="utf-8"))
        applied = case_data.get("applied_learnings", [])
        if not isinstance(applied, list):
            errors.append(f"{case_id}: applied_learnings must be a list")
            continue
        for position, item in enumerate(applied, start=1):
            prefix = f"{case_id}: applied_learnings item {position}"
            if not isinstance(item, dict):
                errors.append(f"{prefix} must be a mapping")
                continue
            if set(item) != {"id", "applicability", "outcome", "evidence_ids"}:
                errors.append(f"{prefix} has invalid fields")
                continue
            if item["id"] not in known_learning_ids:
                errors.append(f"{prefix} references unknown learning {item['id']}")
            if not isinstance(item["applicability"], str) or not item["applicability"].strip():
                errors.append(f"{prefix} requires applicability text")
            if item["outcome"] not in ALLOWED_LEARNING_OUTCOMES:
                errors.append(f"{prefix} has invalid outcome {item['outcome']!r}")
            if not isinstance(item["evidence_ids"], list):
                errors.append(f"{prefix} evidence_ids must be a list")
            else:
                for evidence_id in item["evidence_ids"]:
                    if evidence_id not in evidence_by_case.get(case_id, set()):
                        errors.append(f"{prefix} evidence not found: {evidence_id}")

    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[4]
    errors = validate(root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    index_path = root / "research" / "knowledge" / "local" / "index.yaml"
    print(f"Knowledge validation passed for {len(read_index(index_path))} cards.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
