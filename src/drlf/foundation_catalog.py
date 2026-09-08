from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

FOUNDATION_ID = re.compile(r"^DRLF-FND-[A-Z0-9]+(?:-[A-Z0-9]+)*$")
ALLOWED_STATUS = {"maintained", "experimental", "superseded", "retired"}
ALLOWED_TYPES = {"data", "method", "process", "engineering", "domain"}
ALLOWED_CONFIDENCE = {"low", "medium", "high"}
REQUIRED_SCOPE_KEYS = {"programs", "datasets", "populations"}
REQUIRED_TEXT_FIELDS = {
    "title",
    "principle",
    "why_it_matters",
    "synthetic_evidence",
    "counterevidence_and_limits",
    "failed_or_misleading_approaches",
    "reuse_guidance",
}


def load_foundation_catalog(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("foundation catalog must be a mapping")
    return document


def validate_foundation_catalog(path: Path) -> list[str]:
    try:
        catalog = load_foundation_catalog(path)
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as error:
        return [str(error)]

    errors: list[str] = []
    if catalog.get("catalog_version") != 1:
        errors.append("catalog_version must equal 1")
    if catalog.get("namespace") != "DRLF-FND":
        errors.append("namespace must equal DRLF-FND")
    if catalog.get("origin") != "public-baseline":
        errors.append("origin must equal public-baseline")
    if not catalog.get("reviewed_at"):
        errors.append("reviewed_at is required")

    records = catalog.get("records")
    if not isinstance(records, list) or not records:
        return errors + ["records must be a nonempty list"]

    seen: set[str] = set()
    for position, value in enumerate(records, start=1):
        label = f"record {position}"
        if not isinstance(value, dict):
            errors.append(f"{label} must be a mapping")
            continue
        record_id = value.get("id")
        if not isinstance(record_id, str) or not FOUNDATION_ID.fullmatch(record_id):
            errors.append(f"{label} has an invalid public foundation id")
        elif record_id in seen:
            errors.append(f"{record_id} is duplicated")
        else:
            seen.add(record_id)
            label = record_id

        if value.get("status") not in ALLOWED_STATUS:
            errors.append(f"{label} has an invalid status")
        if value.get("type") not in ALLOWED_TYPES:
            errors.append(f"{label} has an invalid type")
        if value.get("confidence") not in ALLOWED_CONFIDENCE:
            errors.append(f"{label} has an invalid confidence")
        for field in REQUIRED_TEXT_FIELDS:
            if not isinstance(value.get(field), str) or not value[field].strip():
                errors.append(f"{label} requires nonempty {field}")
        scope = value.get("scope")
        if not isinstance(scope, dict) or not REQUIRED_SCOPE_KEYS <= set(scope):
            errors.append(f"{label} requires scope keys {sorted(REQUIRED_SCOPE_KEYS)}")
        elif any(not isinstance(scope[key], list) for key in REQUIRED_SCOPE_KEYS):
            errors.append(f"{label} scope values must be lists")
        sources = value.get("authoritative_sources")
        if not isinstance(sources, list) or not sources:
            errors.append(f"{label} requires authoritative_sources")
        elif any(
            not isinstance(source, str) or not source.startswith("https://")
            for source in sources
        ):
            errors.append(f"{label} authoritative_sources must be HTTPS URLs")
        triggers = value.get("invalidation_triggers")
        if not isinstance(triggers, list) or not triggers or not all(
            isinstance(trigger, str) and trigger.strip() for trigger in triggers
        ):
            errors.append(f"{label} requires nonempty invalidation_triggers")

    experimental = {record["id"] for record in records if record.get("status") == "experimental"}
    expected_experimental = {
        "DRLF-FND-EXPERIMENT-PART-D-INFRASTRUCTURE-SCREEN",
        "DRLF-FND-EXPERIMENT-PART-B-MULTICODE-REACH",
    }
    if experimental != expected_experimental:
        errors.append("the two routing hypotheses must remain explicitly experimental")

    return errors
