from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from statistics import median
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_PATH = PROJECT_ROOT / "examples" / "synthetic-workflow-suite" / "scenarios.yaml"
DEFAULT_REGISTRY_PATH = PROJECT_ROOT / "examples" / "synthetic-identities.yaml"
DEFAULT_SCENARIO_SHA256_LF = (
    "272145b4964742d98569e133c84719d15cc5ca1dabd4435fd053952f77772d19"
)
DEFAULT_REGISTRY_SHA256_LF = (
    "b948915c7e11b580ea5cc7afda1e6028076e65bdfb7703ea72da8094f66303a8"
)
MAX_SCENARIO_BYTES = 262_144
MAX_REGISTRY_BYTES = 262_144
SYNTHETIC_TOKEN = re.compile(r"\bSYNTHETIC-[A-Z0-9-]+\b")


@dataclass(frozen=True)
class SyntheticWorkflowResult:
    input_assurance: str
    scenario_sha256_lf: str
    registry_sha256_lf: str
    output_path: Path
    output_sha256: str


def _read_bounded_yaml(path: Path, *, byte_limit: int, label: str) -> tuple[dict[str, Any], bytes]:
    if not path.is_file():
        raise FileNotFoundError(f"{label} does not exist: {path}")
    with path.open("rb") as source:
        content = source.read(byte_limit + 1)
    if len(content) > byte_limit:
        raise ValueError(f"{label} exceeds its {byte_limit:,}-byte safety limit")
    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not valid UTF-8") from error
    loaded = yaml.safe_load(decoded)
    if not isinstance(loaded, dict):
        raise ValueError(f"{label} must contain a YAML mapping")
    return loaded, content


def _sha256_lf(content: bytes) -> str:
    text = content.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return sha256(text.encode("utf-8")).hexdigest()


def _all_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        strings: list[str] = []
        for key, item in value.items():
            strings.extend(_all_strings(key))
            strings.extend(_all_strings(item))
        return strings
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        strings = []
        for item in value:
            strings.extend(_all_strings(item))
        return strings
    return []


def _registered_values(registry: Mapping[str, Any]) -> set[str]:
    entries = registry.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Synthetic identity registry must contain a nonempty entries list")
    values: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Synthetic identity registry entry {index} must be a mapping")
        value = entry.get("value")
        if not isinstance(value, str) or not value:
            raise ValueError(f"Synthetic identity registry entry {index} has no value")
        if value in values:
            raise ValueError(f"Synthetic identity registry repeats {value!r}")
        values.add(value)
    return values


def _validate_registered_tokens(scenarios: Mapping[str, Any], registry: Mapping[str, Any]) -> None:
    registered = _registered_values(registry)
    used = {
        match.group(0)
        for value in _all_strings(scenarios)
        for match in SYNTHETIC_TOKEN.finditer(value)
    }
    missing = sorted(used - registered)
    if missing:
        raise ValueError("Unregistered synthetic identity tokens: " + ", ".join(missing))


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def _run_bounded_acquisition(scenario: Mapping[str, Any]) -> dict[str, Any]:
    bounds = _require_mapping(scenario.get("bounds"), "bounded_acquisition.bounds")
    pages = _require_list(scenario.get("pages"), "bounded_acquisition.pages")
    max_pages = int(bounds["max_pages"])
    max_total_bytes = int(bounds["max_total_bytes"])
    max_records = int(bounds["max_records"])
    max_retries = int(bounds["max_retries_per_page"])
    if min(max_pages, max_total_bytes, max_records) <= 0 or max_retries < 0:
        raise ValueError("Bounded acquisition limits must be explicit and nonnegative")

    seen_cursors: set[str] = set()
    total_bytes = 0
    record_ids: list[str] = []
    ended = False
    expected_cursor: str | None = None
    for page_number, raw_page in enumerate(pages, start=1):
        if page_number > max_pages:
            raise ValueError("Bounded acquisition exceeded max_pages")
        page = _require_mapping(raw_page, f"bounded_acquisition.pages[{page_number}]")
        cursor = str(page["cursor"])
        if expected_cursor is not None and cursor != expected_cursor:
            raise ValueError("Bounded acquisition broke the declared pagination chain")
        if cursor in seen_cursors:
            raise ValueError(f"Bounded acquisition repeated cursor {cursor!r}")
        seen_cursors.add(cursor)
        retries = int(page.get("retries", 0))
        if retries > max_retries:
            raise ValueError("Bounded acquisition exceeded max_retries_per_page")
        total_bytes += int(page["response_bytes"])
        if total_bytes > max_total_bytes:
            raise ValueError("Bounded acquisition exceeded max_total_bytes")
        page_records = _require_list(page.get("record_ids"), "bounded acquisition record_ids")
        record_ids.extend(str(record_id) for record_id in page_records)
        if len(record_ids) > max_records:
            raise ValueError("Bounded acquisition exceeded max_records")
        next_cursor = page.get("next_cursor")
        if next_cursor is None:
            ended = True
            if page_number != len(pages):
                raise ValueError("Bounded acquisition contains pages after the terminal page")
        elif str(next_cursor) in seen_cursors:
            raise ValueError("Bounded acquisition next_cursor would repeat a page")
        else:
            expected_cursor = str(next_cursor)
    if not ended:
        raise ValueError("Bounded acquisition did not reach a terminal page")
    if len(record_ids) != len(set(record_ids)):
        raise ValueError("Bounded acquisition returned duplicate record IDs")
    return {
        "bounds_enforced": True,
        "pages": len(pages),
        "records": len(record_ids),
        "record_ids": record_ids,
        "retries": sum(int(page.get("retries", 0)) for page in pages),
        "total_bytes": total_bytes,
    }


def _run_protocol_attribution(scenario: Mapping[str, Any]) -> dict[str, Any]:
    product = str(scenario["product"])
    attributed_prescriber = str(scenario["attributed_prescriber"])
    population = str(scenario["population"])
    aggregate = _require_mapping(scenario.get("aggregate_stage"), "protocol aggregate_stage")
    role_state = _require_mapping(
        aggregate.get("role_state"), "protocol aggregate_stage.role_state"
    )
    unknown_role_fields = {
        "patient_specific_prescriber",
        "protocol_or_standing_order_provider",
        "supervising_provider",
        "administrator",
        "dispenser",
        "pharmacy_location",
        "transaction_submitter",
        "biller",
        "plan",
        "payment_recipient",
    }
    expected_role_fields = unknown_role_fields | {"attributed_prescriber_field"}
    if set(role_state) != expected_role_fields:
        raise ValueError("Aggregate-only Part D stage must preserve every operational role lane")
    if role_state["attributed_prescriber_field"] != attributed_prescriber:
        raise ValueError("Aggregate attributed-prescriber field does not match the scenario")
    populated_roles = sorted(
        role for role in unknown_role_fields if role_state.get(role) != "unknown"
    )
    if populated_roles:
        raise ValueError(
            "Aggregate-only Part D stage cannot resolve operational roles: "
            + ", ".join(populated_roles)
        )

    yearly_rows = _require_list(
        aggregate.get("yearly_attribution"), "protocol aggregate_stage.yearly_attribution"
    )
    national_rows = _require_list(
        aggregate.get("compatible_national_totals"),
        "protocol aggregate_stage.compatible_national_totals",
    )
    if not yearly_rows or len(yearly_rows) > 20:
        raise ValueError("Part D yearly attribution must contain between one and 20 rows")
    if len(national_rows) != len(yearly_rows):
        raise ValueError("Part D national totals must align one-to-one with attribution years")

    attribution_by_year: dict[int, dict[str, Any]] = {}
    for raw_row in yearly_rows:
        row = _require_mapping(raw_row, "Part D yearly attribution row")
        year = int(row["year"])
        if year in attribution_by_year:
            raise ValueError("Part D yearly attribution repeats a year")
        claims = int(row["claims"])
        beneficiaries = int(row["beneficiaries"])
        fills = int(row["fills"])
        total_drug_cost = float(row["total_drug_cost"])
        if min(claims, beneficiaries, fills) <= 0 or total_drug_cost < 0:
            raise ValueError("Part D counts must be positive and cost must be nonnegative")
        if beneficiaries > claims or beneficiaries > fills:
            raise ValueError("Part D beneficiaries cannot exceed claims or fills in this fixture")
        attribution_by_year[year] = {
            "beneficiaries": beneficiaries,
            "claims": claims,
            "fills": fills,
            "total_drug_cost": total_drug_cost,
        }
    years = list(attribution_by_year)
    if years != sorted(years):
        raise ValueError("Part D yearly attribution must be in ascending year order")

    national_by_year: dict[int, int] = {}
    for raw_row in national_rows:
        row = _require_mapping(raw_row, "Part D compatible national-total row")
        year = int(row["year"])
        if year in national_by_year:
            raise ValueError("Part D compatible national totals repeat a year")
        if row.get("product") != product or row.get("population") != population:
            raise ValueError("Part D national total is not product-and-population compatible")
        if row.get("includes_suppressed_underlying_rows") is not True:
            raise ValueError("Part D national total must include suppressed underlying rows")
        claims = int(row["claims"])
        if claims <= 0:
            raise ValueError("Part D national claim totals must be positive")
        national_by_year[year] = claims
    if set(national_by_year) != set(attribution_by_year):
        raise ValueError("Part D national totals do not cover the attribution years")

    annual_metrics: list[dict[str, Any]] = []
    prior_claims: int | None = None
    for year, row in attribution_by_year.items():
        national_claims = national_by_year[year]
        if row["claims"] > national_claims:
            raise ValueError("Part D attributed claims exceed the compatible national total")
        annual_metrics.append(
            {
                "attributed_claim_share": round(row["claims"] / national_claims, 6),
                "beneficiaries": row["beneficiaries"],
                "claims": row["claims"],
                "claims_growth_from_prior_year": (
                    None
                    if prior_claims is None
                    else round(row["claims"] / prior_claims - 1, 6)
                ),
                "claims_per_beneficiary": round(
                    row["claims"] / row["beneficiaries"], 6
                ),
                "compatible_national_claims": national_claims,
                "fills": row["fills"],
                "fills_per_beneficiary": round(
                    row["fills"] / row["beneficiaries"], 6
                ),
                "total_drug_cost": row["total_drug_cost"],
                "year": year,
            }
        )
        prior_claims = row["claims"]

    latest_year = years[-1]
    latest_visible = _require_mapping(
        aggregate.get("latest_visible_provider_rows"),
        "protocol aggregate_stage.latest_visible_provider_rows",
    )
    if (
        int(latest_visible.get("year", -1)) != latest_year
        or latest_visible.get("product") != product
        or latest_visible.get("population") != population
    ):
        raise ValueError("Part D visible provider rows do not match the latest comparison grain")
    if latest_visible.get("suppressed_rows_present") is not True:
        raise ValueError("Part D visible-row exercise must preserve suppressed rows")
    visible_rows = _require_list(latest_visible.get("rows"), "Part D visible provider rows")
    if not visible_rows or len(visible_rows) > 50:
        raise ValueError("Part D visible provider rows must contain between one and 50 rows")
    visible_claims_by_prescriber: dict[str, int] = {}
    for raw_row in visible_rows:
        row = _require_mapping(raw_row, "Part D visible provider row")
        prescriber = str(row["prescriber"])
        if prescriber in visible_claims_by_prescriber:
            raise ValueError("Part D visible provider rows repeat a prescriber")
        claims = int(row["claims"])
        if claims < 0:
            raise ValueError("Part D visible provider claim counts must be nonnegative")
        visible_claims_by_prescriber[prescriber] = claims
    if visible_claims_by_prescriber.get(attributed_prescriber) != attribution_by_year[
        latest_year
    ]["claims"]:
        raise ValueError("Part D candidate visible row does not match latest attribution")
    visible_claims = sum(visible_claims_by_prescriber.values())
    latest_national_claims = national_by_year[latest_year]
    if visible_claims >= latest_national_claims:
        raise ValueError("Part D visible provider sum cannot substitute for the national total")

    exact_peers = _require_mapping(
        aggregate.get("latest_exact_product_peers"),
        "protocol aggregate_stage.latest_exact_product_peers",
    )
    if (
        int(exact_peers.get("year", -1)) != latest_year
        or exact_peers.get("product") != product
        or exact_peers.get("population") != population
    ):
        raise ValueError("Part D peers do not match the latest exact product and population")
    peer_rows = _require_list(exact_peers.get("rows"), "Part D exact-product peers")
    if not 3 <= len(peer_rows) <= 50:
        raise ValueError("Part D peer cohort must contain between three and 50 rows")
    peer_claims: list[int] = []
    peer_ids: set[str] = set()
    for raw_row in peer_rows:
        row = _require_mapping(raw_row, "Part D exact-product peer")
        prescriber = str(row["prescriber"])
        if prescriber == attributed_prescriber:
            raise ValueError("Part D candidate must be excluded from the exact-product peers")
        if prescriber in peer_ids:
            raise ValueError("Part D exact-product peers repeat a prescriber")
        peer_ids.add(prescriber)
        claims = int(row["claims"])
        beneficiaries = int(row["beneficiaries"])
        fills = int(row["fills"])
        if min(claims, beneficiaries, fills) <= 0:
            raise ValueError("Part D peer counts must be positive")
        if beneficiaries > claims or beneficiaries > fills:
            raise ValueError("Part D peer beneficiaries cannot exceed claims or fills")
        if visible_claims_by_prescriber.get(prescriber) != claims:
            raise ValueError("Part D peer row does not match its visible provider row")
        peer_claims.append(claims)
    peer_claim_median = float(median(peer_claims))

    reassignments = _require_list(
        aggregate.get("reassignment_series"),
        "protocol aggregate_stage.reassignment_series",
    )
    if len(reassignments) != len(years):
        raise ValueError("Part D reassignment series must cover every attribution year")
    prior_prescriber: str | None = None
    reassignment_rows: list[dict[str, int]] = []
    for index, raw_row in enumerate(reassignments):
        row = _require_mapping(raw_row, "Part D reassignment row")
        year = int(row["year"])
        if year != years[index]:
            raise ValueError("Part D reassignment series is not aligned to attribution years")
        row_prior_prescriber = str(row["prior_prescriber"])
        if row_prior_prescriber == attributed_prescriber:
            raise ValueError("Part D reassignment comparator cannot be the candidate")
        if prior_prescriber is not None and row_prior_prescriber != prior_prescriber:
            raise ValueError("Part D reassignment series changes the prior prescriber")
        prior_prescriber = row_prior_prescriber
        prior_row_claims = int(row["prior_claims"])
        current_row_claims = int(row["attributed_prescriber_claims"])
        if prior_row_claims < 0 or current_row_claims < 0:
            raise ValueError("Part D reassignment counts must be nonnegative")
        if current_row_claims != attribution_by_year[year]["claims"]:
            raise ValueError("Part D reassignment series conflicts with yearly attribution")
        reassignment_rows.append(
            {
                "attributed_prescriber_claims": current_row_claims,
                "combined_claims": prior_row_claims + current_row_claims,
                "prior_claims": prior_row_claims,
                "year": year,
            }
        )
    if len(reassignment_rows) < 2:
        raise ValueError("Part D reassignment review needs at least two years")
    preceding = reassignment_rows[-2]
    latest = reassignment_rows[-1]
    attributed_change = latest["attributed_prescriber_claims"] - preceding[
        "attributed_prescriber_claims"
    ]
    prior_change = latest["prior_claims"] - preceding["prior_claims"]
    combined_change = latest["combined_claims"] - preceding["combined_claims"]
    combined_change_ratio = combined_change / preceding["combined_claims"]
    reassignment_classification = (
        "offsetting-attribution-discontinuity-for-review"
        if attributed_change > 0 and prior_change < 0 and abs(combined_change_ratio) <= 0.1
        else "no-offsetting-reassignment-pattern-established"
    )

    assignments = _require_list(
        scenario.get("resolved_stage_role_assignments"),
        "protocol resolved_stage_role_assignments",
    )
    if not assignments or len(assignments) > 50:
        raise ValueError("Resolved Part D role assignments must contain between one and 50 rows")
    role_map: dict[str, list[str]] = {}
    protocol_basis = False
    for raw_assignment in assignments:
        assignment = _require_mapping(raw_assignment, "protocol role assignment")
        role = str(assignment["role"])
        actor = str(assignment["actor"])
        role_map.setdefault(role, []).append(actor)
        if (
            role == "prescriber"
            and actor == attributed_prescriber
            and assignment.get("basis") == "standing-order-protocol"
        ):
            protocol_basis = True
    required_roles = {"prescriber", "dispenser", "administrator", "biller", "payment-recipient"}
    missing_roles = sorted(required_roles - role_map.keys())
    if missing_roles:
        raise ValueError("Protocol scenario omits operational roles: " + ", ".join(missing_roles))
    if attributed_prescriber not in role_map["prescriber"]:
        raise ValueError("Resolved Part D prescriber role does not match aggregate attribution")
    unique_dispensers = set(role_map["dispenser"])
    classification = (
        "protocol-attribution-plausible-not-proven"
        if protocol_basis and len(unique_dispensers) > 1
        else "ordinary-clinical-attribution-unresolved"
    )
    return {
        "aggregate_stage": {
            "annual_metrics": annual_metrics,
            "classification": "aggregate-outlier-requires-role-resolution",
            "latest_denominator_context": {
                "attributed_claim_share": round(
                    attribution_by_year[latest_year]["claims"] / latest_national_claims, 6
                ),
                "compatible_national_claims": latest_national_claims,
                "suppressed_rows_present": True,
                "visible_provider_claim_gap": latest_national_claims - visible_claims,
                "visible_provider_claims": visible_claims,
                "visible_provider_sum_is_not_national_total": True,
                "year": latest_year,
            },
            "latest_peer_context": {
                "candidate_claims": attribution_by_year[latest_year]["claims"],
                "candidate_to_peer_median_claims": round(
                    attribution_by_year[latest_year]["claims"] / peer_claim_median, 6
                ),
                "peer_claim_median": peer_claim_median,
                "peer_count": len(peer_claims),
                "year": latest_year,
            },
            "population": population,
            "product": product,
            "role_state": {key: role_state[key] for key in sorted(role_state)},
        },
        "gross_cost_is_not_prescriber_payment": True,
        "reassignment_review": {
            "attributed_prescriber_claim_change": attributed_change,
            "classification": reassignment_classification,
            "combined_claim_change": combined_change,
            "combined_claim_change_ratio": round(combined_change_ratio, 6),
            "limitation": (
                "An offsetting aggregate discontinuity is a lead, not proof of reassignment."
            ),
            "prior_prescriber": prior_prescriber,
            "prior_prescriber_claim_change": prior_change,
            "series": reassignment_rows,
        },
        "resolved_stage": {
            "classification": classification,
            "role_assignments": {
                role: sorted(set(actors)) for role, actors in sorted(role_map.items())
            },
        },
        "required_nonpublic_evidence": list(scenario["required_nonpublic_evidence"]),
    }


def _run_operational_context(scenario: Mapping[str, Any]) -> dict[str, Any]:
    geographies = _require_mapping(scenario.get("geographies"), "operational_context.geographies")
    expected_geographies = {
        "prescriber_address_state",
        "service_location",
        "beneficiary_residence",
    }
    if set(geographies) != expected_geographies:
        raise ValueError("Operational context must preserve three separate geography lanes")
    affiliations = _require_list(
        scenario.get("affiliation_lanes"), "operational_context.affiliation_lanes"
    )
    seen_lanes: set[str] = set()
    for raw_affiliation in affiliations:
        affiliation = _require_mapping(raw_affiliation, "operational affiliation")
        required = {"lane", "actor", "source", "effective_start", "effective_end", "observed_at"}
        if not required.issubset(affiliation):
            raise ValueError("Each affiliation lane needs source and effective-date provenance")
        lane = str(affiliation["lane"])
        if lane in seen_lanes:
            raise ValueError(f"Operational context merges duplicate affiliation lane {lane!r}")
        seen_lanes.add(lane)
    money = _require_mapping(scenario.get("money"), "operational_context.money")
    if money.get("paid_to_prescriber") != "unknown":
        raise ValueError("Aggregate fixture must not infer an amount paid to the prescriber")
    unknowns = sorted(
        key for key, value in {**geographies, **money}.items() if value == "unknown"
    )
    return {
        "affiliation_lanes": sorted(seen_lanes),
        "geographies_kept_separate": True,
        "money_meanings_kept_separate": True,
        "unknown_fields": unknowns,
    }


def _run_dmepos(scenario: Mapping[str, Any]) -> dict[str, Any]:
    product = _require_mapping(scenario.get("product"), "dmepos.product")
    candidate = _require_mapping(scenario.get("candidate_cell"), "dmepos.candidate_cell")
    peers = _require_list(scenario.get("peer_cells"), "dmepos.peer_cells")
    components = _require_mapping(scenario.get("visible_components"), "dmepos.visible_components")
    total_payment = float(scenario["supplier_summary_total_payment"])
    component_values = [float(value) for value in components.values()]
    coverage_floor = float(scenario["coverage_floor"])
    dominance_floor = float(scenario["dominance_floor"])
    if total_payment <= 0 or any(value < 0 for value in component_values):
        raise ValueError("DMEPOS payment amounts must be nonnegative with a positive total")
    if not (0 <= coverage_floor <= 1 and 0 <= dominance_floor <= 1):
        raise ValueError("DMEPOS coverage and dominance floors must be proportions")
    if product.get("code") != candidate.get("code") or not product.get("billing_unit"):
        raise ValueError("DMEPOS product semantics do not match the candidate cell")
    visible_payment = sum(component_values)
    coverage = visible_payment / total_payment
    dominant_share = max((float(value) for value in components.values()), default=0.0)
    dominant_share = dominant_share / visible_payment if visible_payment else 0.0
    if coverage < coverage_floor:
        component_classification = "unclassified-insufficient-component-coverage"
    elif dominant_share >= dominance_floor:
        component_classification = "dominant-visible-component"
    else:
        component_classification = "mixed-visible-components"

    exact_peer_keys = ("year", "code", "rental_indicator", "entity_type")
    candidate_id = str(candidate["supplier"])
    for raw_peer in peers:
        peer = _require_mapping(raw_peer, "dmepos peer cell")
        if str(peer["supplier"]) == candidate_id:
            raise ValueError("DMEPOS candidate must be excluded from its peer cohort")
        if any(peer[key] != candidate[key] for key in exact_peer_keys):
            raise ValueError("DMEPOS peer does not match the exact comparison grain")
    if len(peers) < int(scenario["minimum_peer_count"]):
        peer_status = "descriptive-underpowered-peer-cohort"
    else:
        peer_status = "exact-candidate-excluded-peer-cohort"

    observations = _require_list(
        scenario.get("provider_status_observations"), "dmepos.provider_status_observations"
    )
    if not observations:
        raise ValueError("DMEPOS scenario needs at least one point-in-time status observation")
    statuses = []
    for raw_observation in observations:
        observation = _require_mapping(raw_observation, "provider status observation")
        statuses.append(
            {
                "as_of": observation["as_of"],
                "source": observation["source"],
                "status": observation["status"],
            }
        )
    return {
        "billing_unit": product["billing_unit"],
        "component_classification": component_classification,
        "component_coverage": round(coverage, 6),
        "dominant_visible_share": round(dominant_share, 6),
        "peer_status": peer_status,
        "provider_status_interpretation": "point-in-time-source-assertions-only",
        "provider_status_observations": statuses,
        "units_are_not_visits_or_deliveries": True,
    }


def _run_case_lifecycle(scenario: Mapping[str, Any]) -> dict[str, Any]:
    expected_stages = [
        "intake",
        "context-review",
        "analysis",
        "cutoff",
        "disposition",
        "retrospective",
        "learning-proposal",
        "skill-change-proposal",
    ]
    if scenario.get("stages") != expected_stages:
        raise ValueError("Synthetic case lifecycle is incomplete or out of order")
    evidence = _require_list(scenario.get("evidence"), "case_lifecycle.evidence")
    directions = {str(item["direction"]) for item in evidence if isinstance(item, dict)}
    if not {"supports", "weakens"}.issubset(directions):
        raise ValueError("Synthetic lifecycle must preserve supporting and weakening evidence")
    evidence_ids = {str(item["evidence_id"]) for item in evidence if isinstance(item, dict)}
    if any(item.get("kind") == "learning" for item in evidence if isinstance(item, dict)):
        raise ValueError("A learning record cannot substitute for case evidence")
    learning = _require_mapping(scenario.get("candidate_learning"), "candidate_learning")
    required_learning_fields = {
        "learning_id",
        "title",
        "status",
        "type",
        "confidence",
        "scope",
        "evidence_ids",
        "lesson",
        "why_it_matters",
        "counterevidence_and_limits",
        "failed_approaches",
        "reuse_guidance",
        "invalidation_triggers",
        "history",
    }
    if not required_learning_fields.issubset(learning):
        raise ValueError("Candidate learning card omits required reusable-learning fields")
    _require_mapping(learning["scope"], "candidate_learning.scope")
    if not _require_list(learning["history"], "candidate_learning.history"):
        raise ValueError("Candidate learning card must retain append-only history")
    learning_sources = set(str(value) for value in learning.get("evidence_ids", []))
    if not learning_sources or not learning_sources.issubset(evidence_ids):
        raise ValueError("Candidate learning must retain valid case-evidence lineage")
    if learning["status"] != "candidate":
        raise ValueError("A single synthetic case must not silently promote a candidate learning")
    bounded_actions = _require_list(
        scenario.get("bounded_actions"), "case_lifecycle.bounded_actions"
    )
    if len(bounded_actions) != 2:
        raise ValueError("Synthetic cutoff exercise must contain exactly two bounded actions")
    action_ids: set[str] = set()
    action_summaries: list[dict[str, Any]] = []
    required_action_fields = {
        "action_id",
        "question",
        "source",
        "bounds",
        "distinguishing_outcomes",
        "observed_outcome",
        "disposition_before",
        "disposition_after",
        "disposition_consequence",
        "changed_disposition",
    }
    required_bounds = {"max_queries", "max_runtime_seconds", "max_output_bytes"}
    for raw_action in bounded_actions:
        action = _require_mapping(raw_action, "case lifecycle bounded action")
        if not required_action_fields.issubset(action):
            raise ValueError("A bounded action omits its question, outcomes, or consequence")
        action_id = str(action["action_id"])
        if action_id in action_ids:
            raise ValueError("Synthetic case lifecycle repeats a bounded action ID")
        action_ids.add(action_id)
        if not str(action["question"]).strip() or not str(action["source"]).strip():
            raise ValueError("Each bounded action needs a question and source")
        bounds = _require_mapping(action["bounds"], "case lifecycle action bounds")
        if set(bounds) != required_bounds:
            raise ValueError("Each bounded action needs explicit query, runtime, and output caps")
        limit_values = [bounds[key] for key in sorted(required_bounds)]
        if any(isinstance(value, bool) or not isinstance(value, int) for value in limit_values):
            raise ValueError("Bounded action caps must be integers")
        if any(value <= 0 for value in limit_values):
            raise ValueError("Bounded action caps must be positive")
        if (
            int(bounds["max_queries"]) > 10
            or int(bounds["max_runtime_seconds"]) > 60
            or int(bounds["max_output_bytes"]) > 8192
        ):
            raise ValueError("Synthetic bounded action exceeds the exercise resource envelope")
        distinguishing_outcomes = _require_list(
            action["distinguishing_outcomes"], "bounded action distinguishing_outcomes"
        )
        if not 2 <= len(distinguishing_outcomes) <= 5 or any(
            not isinstance(value, str) or not value.strip()
            for value in distinguishing_outcomes
        ):
            raise ValueError("Each bounded action needs two to five distinguishing outcomes")
        if not isinstance(action["observed_outcome"], str) or not action[
            "observed_outcome"
        ].strip():
            raise ValueError("Each bounded action must record an observed outcome")
        if action["changed_disposition"] is not False:
            raise ValueError("Synthetic cutoff actions must record consecutive no-change outcomes")
        if action["disposition_before"] != action["disposition_after"]:
            raise ValueError("A no-change action cannot silently change the disposition")
        consequence = str(action["disposition_consequence"])
        if not consequence.startswith("no-change;"):
            raise ValueError("Each no-change action must state its disposition consequence")
        action_summaries.append(
            {
                "action_id": action_id,
                "bounds": {key: bounds[key] for key in sorted(bounds)},
                "changed_disposition": False,
                "disposition_after": action["disposition_after"],
                "disposition_before": action["disposition_before"],
                "disposition_consequence": consequence,
                "distinguishing_outcomes": distinguishing_outcomes,
                "observed_outcome": action["observed_outcome"],
                "question": action["question"],
                "source": action["source"],
            }
        )
    cutoff = _require_mapping(scenario.get("cutoff"), "case_lifecycle.cutoff")
    if cutoff.get("qualifying_public_actions") != []:
        raise ValueError(
            "Closed synthetic case should have no remaining disposition-changing action"
        )
    proposal = _require_mapping(scenario.get("skill_change_proposal"), "skill_change_proposal")
    required_proposal = {"target", "evidence", "behavior", "files", "validation", "permission"}
    if not required_proposal.issubset(proposal):
        raise ValueError("Skill change proposal omits the permission contract")
    permission = _require_mapping(proposal["permission"], "skill_change_proposal.permission")
    approved = permission.get("explicit_user_approval") is True
    expected_permission_status = "granted" if approved else "not-granted"
    if permission.get("status") != expected_permission_status:
        raise ValueError("Skill permission state is contradictory")
    if not approved and permission.get("change_applied"):
        raise ValueError("Skill change cannot be applied without explicit user permission")
    return {
        "actions_completed_before_cutoff": True,
        "bounded_actions": action_summaries,
        "bounded_actions_completed": len(action_summaries),
        "case_id": scenario["case_id"],
        "consecutive_no_change_actions": len(action_summaries),
        "disposition": scenario["disposition"],
        "evidence_directions": sorted(directions),
        "learning_status": learning["status"],
        "research_cutoff_reached": True,
        "skill_change_gate": (
            "blocked-pending-explicit-user-permission"
            if not approved
            else "permission-recorded"
        ),
        "stages_completed": expected_stages,
    }


def _upgrade_action(base: Any, current: Any, upstream: Any) -> str:
    if base is None:
        if current is None and upstream is not None:
            return "add-upstream-baseline"
        if current is not None and upstream is None:
            return "preserve-local-only"
        return "namespace-collision-review-required"
    if current == base:
        if upstream is None:
            return "upstream-removal-decision-required"
        if upstream == base:
            return "no-change"
        return "replace-unchanged-baseline"
    if upstream == base or upstream is None:
        return "preserve-local-divergence"
    return "conflict-review-required"


def _run_upgrade_rehearsal(scenario: Mapping[str, Any]) -> dict[str, Any]:
    entries = _require_list(scenario.get("paths"), "upgrade_rehearsal.paths")
    plan: list[dict[str, str]] = []
    for raw_entry in entries:
        entry = _require_mapping(raw_entry, "upgrade path")
        action = _upgrade_action(
            entry.get("base_sha256"), entry.get("current_sha256"), entry.get("upstream_sha256")
        )
        expected = entry.get("expected_action")
        if action != expected:
            raise ValueError(
                f"Upgrade rehearsal expectation mismatch for {entry.get('path')!r}: "
                f"expected {expected!r}, calculated {action!r}"
            )
        plan.append({"action": action, "path": str(entry["path"])})
    actions = {item["action"] for item in plan}
    required_actions = {
        "add-upstream-baseline",
        "conflict-review-required",
        "no-change",
        "preserve-local-only",
        "replace-unchanged-baseline",
        "upstream-removal-decision-required",
    }
    if not required_actions.issubset(actions):
        raise ValueError("Upgrade rehearsal does not cover every required non-destructive outcome")
    return {
        "applied": False,
        "baseline_to": scenario["proposed_baseline_version"],
        "baseline_from": scenario["installed_baseline_version"],
        "plan": plan,
        "requires_review": True,
    }


def evaluate_synthetic_workflow_suite(
    scenarios: Mapping[str, Any], registry: Mapping[str, Any]
) -> dict[str, Any]:
    if scenarios.get("synthetic_record") is not True:
        raise ValueError("Synthetic workflow suite is not explicitly marked synthetic")
    if scenarios.get("schema_version") != 1:
        raise ValueError("Unsupported synthetic workflow suite schema_version")
    _validate_registered_tokens(scenarios, registry)
    return {
        "bounded_acquisition": _run_bounded_acquisition(
            _require_mapping(scenarios.get("bounded_acquisition"), "bounded_acquisition")
        ),
        "case_lifecycle": _run_case_lifecycle(
            _require_mapping(scenarios.get("case_lifecycle"), "case_lifecycle")
        ),
        "dmepos_provider_status": _run_dmepos(
            _require_mapping(scenarios.get("dmepos_provider_status"), "dmepos_provider_status")
        ),
        "operational_context": _run_operational_context(
            _require_mapping(scenarios.get("operational_context"), "operational_context")
        ),
        "part_d_protocol_attribution": _run_protocol_attribution(
            _require_mapping(
                scenarios.get("part_d_protocol_attribution"), "part_d_protocol_attribution"
            )
        ),
        "schema_version": 1,
        "suite_id": scenarios["suite_id"],
        "upgrade_rehearsal": _run_upgrade_rehearsal(
            _require_mapping(scenarios.get("upgrade_rehearsal"), "upgrade_rehearsal")
        ),
    }


def run_synthetic_workflow_suite(
    output_dir: Path,
    *,
    scenario_path: Path = DEFAULT_SCENARIO_PATH,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    allow_unpinned_input: bool = False,
) -> SyntheticWorkflowResult:
    scenarios, scenario_bytes = _read_bounded_yaml(
        scenario_path, byte_limit=MAX_SCENARIO_BYTES, label="Synthetic workflow scenarios"
    )
    registry, registry_bytes = _read_bounded_yaml(
        registry_path, byte_limit=MAX_REGISTRY_BYTES, label="Synthetic identity registry"
    )
    scenario_sha256_lf = _sha256_lf(scenario_bytes)
    registry_sha256_lf = _sha256_lf(registry_bytes)
    pinned_fixture_verified = (
        scenario_sha256_lf == DEFAULT_SCENARIO_SHA256_LF
        and registry_sha256_lf == DEFAULT_REGISTRY_SHA256_LF
    )
    if not pinned_fixture_verified and not allow_unpinned_input:
        raise ValueError(
            "Synthetic workflow inputs do not match the pinned committed fixtures; "
            "explicitly allow "
            "unpinned inputs only after verifying that every value is invented"
        )
    results = evaluate_synthetic_workflow_suite(scenarios, registry)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "synthetic-workflow-results.json"
    if output_path.exists():
        raise FileExistsError(f"Refusing to replace synthetic workflow result: {output_path}")
    if pinned_fixture_verified:
        input_assurance = "pinned-committed-fixtures"
        notice = (
            "Verified pinned synthetic teaching scenarios; no record represents a real person, "
            "organization, provider, beneficiary, claim, payment, or allegation."
        )
    else:
        input_assurance = "operator-asserted-unpinned-inputs"
        notice = (
            "Unpinned inputs explicitly allowed by the operator. Structural checks passed, "
            "but this "
            "tool does not independently establish that every input value was invented."
        )
    document = {
        "assurance": {
            "input_assurance": input_assurance,
            "notice": notice,
            "pinned_fixture_verified": pinned_fixture_verified,
            "registry_sha256_lf": registry_sha256_lf,
            "scenario_sha256_lf": scenario_sha256_lf,
        },
        "results": results,
    }
    output_path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    output_bytes = output_path.read_bytes()
    return SyntheticWorkflowResult(
        input_assurance=input_assurance,
        scenario_sha256_lf=scenario_sha256_lf,
        registry_sha256_lf=registry_sha256_lf,
        output_path=output_path,
        output_sha256=sha256(output_bytes).hexdigest(),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the bounded, offline synthetic research-workflow suite."
    )
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--allow-unpinned-input",
        action="store_true",
        help=(
            "Run modified fixtures after independently confirming that they contain only invented "
            "values; the result labels their assurance as operator-asserted"
        ),
    )
    arguments = parser.parse_args(argv)
    result = run_synthetic_workflow_suite(
        arguments.output_dir, allow_unpinned_input=arguments.allow_unpinned_input
    )
    print(f"Wrote synthetic workflow result ({result.output_sha256}) to {result.output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
