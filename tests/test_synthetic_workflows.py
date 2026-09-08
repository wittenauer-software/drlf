from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
import yaml

from drlf.synthetic_workflows import (
    DEFAULT_REGISTRY_PATH,
    DEFAULT_REGISTRY_SHA256_LF,
    DEFAULT_SCENARIO_PATH,
    DEFAULT_SCENARIO_SHA256_LF,
    evaluate_synthetic_workflow_suite,
    run_synthetic_workflow_suite,
)


def _load_yaml(path: Path) -> dict:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


@pytest.fixture
def scenarios() -> dict:
    return _load_yaml(DEFAULT_SCENARIO_PATH)


@pytest.fixture
def registry() -> dict:
    return _load_yaml(DEFAULT_REGISTRY_PATH)


def test_suite_is_deterministic_registered_and_covers_required_boundaries(tmp_path: Path) -> None:
    first = run_synthetic_workflow_suite(tmp_path / "first")
    second = run_synthetic_workflow_suite(tmp_path / "second")

    assert first.input_assurance == "pinned-committed-fixtures"
    assert first.scenario_sha256_lf == DEFAULT_SCENARIO_SHA256_LF
    assert first.registry_sha256_lf == DEFAULT_REGISTRY_SHA256_LF
    assert first.scenario_sha256_lf == second.scenario_sha256_lf
    assert first.registry_sha256_lf == second.registry_sha256_lf
    assert first.output_sha256 == second.output_sha256

    document = json.loads(first.output_path.read_text(encoding="utf-8"))
    assert document["assurance"]["pinned_fixture_verified"] is True
    assert document["assurance"]["input_assurance"] == "pinned-committed-fixtures"
    assert "no record represents a real person" in document["assurance"]["notice"]
    results = document["results"]
    assert set(results) == {
        "bounded_acquisition",
        "case_lifecycle",
        "dmepos_provider_status",
        "operational_context",
        "part_d_protocol_attribution",
        "schema_version",
        "suite_id",
        "upgrade_rehearsal",
    }

    acquisition = results["bounded_acquisition"]
    assert acquisition == {
        "bounds_enforced": True,
        "pages": 2,
        "record_ids": [
            "SYNTHETIC-RECORD-ONE",
            "SYNTHETIC-RECORD-TWO",
            "SYNTHETIC-RECORD-THREE",
        ],
        "records": 3,
        "retries": 1,
        "total_bytes": 420,
    }

    protocol = results["part_d_protocol_attribution"]
    assert protocol["gross_cost_is_not_prescriber_payment"] is True
    aggregate = protocol["aggregate_stage"]
    assert aggregate["classification"] == "aggregate-outlier-requires-role-resolution"
    assert aggregate["product"] == "SYNTHETIC-PRODUCT-VACCINE"
    assert aggregate["population"] == "invented-drug-benefit-enrollees"
    assert aggregate["role_state"]["attributed_prescriber_field"] == (
        "SYNTHETIC-PRESCRIBER-PROTOCOL"
    )
    assert {
        value
        for role, value in aggregate["role_state"].items()
        if role != "attributed_prescriber_field"
    } == {"unknown"}
    assert aggregate["annual_metrics"][-1] == {
        "attributed_claim_share": 0.18,
        "beneficiaries": 150,
        "claims": 180,
        "claims_growth_from_prior_year": 6.5,
        "claims_per_beneficiary": 1.2,
        "compatible_national_claims": 1000,
        "fills": 180,
        "fills_per_beneficiary": 1.2,
        "total_drug_cost": 3600.0,
        "year": 2099,
    }
    assert aggregate["latest_denominator_context"] == {
        "attributed_claim_share": 0.18,
        "compatible_national_claims": 1000,
        "suppressed_rows_present": True,
        "visible_provider_claim_gap": 688,
        "visible_provider_claims": 312,
        "visible_provider_sum_is_not_national_total": True,
        "year": 2099,
    }
    assert aggregate["latest_peer_context"] == {
        "candidate_claims": 180,
        "candidate_to_peer_median_claims": 4.090909,
        "peer_claim_median": 44.0,
        "peer_count": 3,
        "year": 2099,
    }
    reassignment = protocol["reassignment_review"]
    assert reassignment["classification"] == "offsetting-attribution-discontinuity-for-review"
    assert reassignment["attributed_prescriber_claim_change"] == 156
    assert reassignment["prior_prescriber_claim_change"] == -148
    assert reassignment["combined_claim_change"] == 8
    assert "not proof" in reassignment["limitation"]
    resolved = protocol["resolved_stage"]
    assert resolved["classification"] == "protocol-attribution-plausible-not-proven"
    assert set(resolved["role_assignments"]) == {
        "administrator",
        "biller",
        "dispenser",
        "payment-recipient",
        "prescriber",
    }

    context = results["operational_context"]
    assert context["geographies_kept_separate"] is True
    assert context["money_meanings_kept_separate"] is True
    assert context["unknown_fields"] == [
        "beneficiary_residence",
        "paid_to_prescriber",
        "pharmacy_remittance",
        "service_location",
    ]

    dmepos = results["dmepos_provider_status"]
    assert dmepos["billing_unit"] == "monthly-rental-unit"
    assert dmepos["component_classification"] == "dominant-visible-component"
    assert dmepos["peer_status"] == "exact-candidate-excluded-peer-cohort"
    assert dmepos["provider_status_interpretation"] == "point-in-time-source-assertions-only"
    assert dmepos["units_are_not_visits_or_deliveries"] is True

    lifecycle = results["case_lifecycle"]
    assert lifecycle["evidence_directions"] == ["supports", "weakens"]
    assert lifecycle["research_cutoff_reached"] is True
    assert lifecycle["actions_completed_before_cutoff"] is True
    assert lifecycle["bounded_actions_completed"] == 2
    assert lifecycle["consecutive_no_change_actions"] == 2
    assert all(
        action["changed_disposition"] is False
        and action["disposition_before"] == action["disposition_after"]
        and action["disposition_consequence"].startswith("no-change;")
        and len(action["distinguishing_outcomes"]) >= 2
        and bool(action["observed_outcome"])
        for action in lifecycle["bounded_actions"]
    )
    assert lifecycle["learning_status"] == "candidate"
    assert lifecycle["skill_change_gate"] == "blocked-pending-explicit-user-permission"

    upgrade = results["upgrade_rehearsal"]
    assert upgrade["applied"] is False
    assert {item["action"] for item in upgrade["plan"]} == {
        "add-upstream-baseline",
        "conflict-review-required",
        "no-change",
        "preserve-local-only",
        "replace-unchanged-baseline",
        "upstream-removal-decision-required",
    }


def test_suite_refuses_to_overwrite_result(tmp_path: Path) -> None:
    run_synthetic_workflow_suite(tmp_path)
    with pytest.raises(FileExistsError, match="Refusing to replace"):
        run_synthetic_workflow_suite(tmp_path)


def test_suite_rejects_unpinned_inputs_unless_operator_explicitly_allows_them(
    tmp_path: Path, scenarios: dict
) -> None:
    scenarios["bounded_acquisition"]["bounds"]["max_pages"] = 4
    modified_path = tmp_path / "modified-scenarios.yaml"
    modified_path.write_text(yaml.safe_dump(scenarios, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="do not match the pinned committed fixtures"):
        run_synthetic_workflow_suite(
            tmp_path / "rejected",
            scenario_path=modified_path,
        )

    result = run_synthetic_workflow_suite(
        tmp_path / "allowed",
        scenario_path=modified_path,
        allow_unpinned_input=True,
    )
    document = json.loads(result.output_path.read_text(encoding="utf-8"))
    assert result.input_assurance == "operator-asserted-unpinned-inputs"
    assert document["assurance"]["pinned_fixture_verified"] is False
    assert "does not independently establish" in document["assurance"]["notice"]


def test_suite_pin_is_stable_across_crlf_checkout_behavior(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenarios.yaml"
    registry_path = tmp_path / "synthetic-identities.yaml"
    scenario_path.write_bytes(
        DEFAULT_SCENARIO_PATH.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8")
    )
    registry_path.write_bytes(
        DEFAULT_REGISTRY_PATH.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8")
    )

    result = run_synthetic_workflow_suite(
        tmp_path / "output",
        scenario_path=scenario_path,
        registry_path=registry_path,
    )
    assert result.input_assurance == "pinned-committed-fixtures"
    assert result.scenario_sha256_lf == DEFAULT_SCENARIO_SHA256_LF
    assert result.registry_sha256_lf == DEFAULT_REGISTRY_SHA256_LF


def test_suite_rejects_unregistered_synthetic_token(scenarios: dict, registry: dict) -> None:
    scenarios["bounded_acquisition"]["source"] = "SYNTHETIC-" + "NOT-REGISTERED"
    with pytest.raises(ValueError, match="Unregistered synthetic identity tokens"):
        evaluate_synthetic_workflow_suite(scenarios, registry)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("repeat-cursor", "pagination chain"),
        ("byte-cap", "max_total_bytes"),
        ("retry-cap", "max_retries_per_page"),
        ("record-cap", "max_records"),
    ],
)
def test_bounded_acquisition_fails_closed(
    scenarios: dict, registry: dict, mutation: str, message: str
) -> None:
    acquisition = scenarios["bounded_acquisition"]
    if mutation == "repeat-cursor":
        acquisition["pages"][1]["cursor"] = acquisition["pages"][0]["cursor"]
    elif mutation == "byte-cap":
        acquisition["bounds"]["max_total_bytes"] = 300
    elif mutation == "retry-cap":
        acquisition["bounds"]["max_retries_per_page"] = 0
    else:
        acquisition["bounds"]["max_records"] = 2

    with pytest.raises(ValueError, match=message):
        evaluate_synthetic_workflow_suite(scenarios, registry)


def test_protocol_scenario_requires_every_operational_role(scenarios: dict, registry: dict) -> None:
    assignments = scenarios["part_d_protocol_attribution"]["resolved_stage_role_assignments"]
    scenarios["part_d_protocol_attribution"]["resolved_stage_role_assignments"] = [
        assignment for assignment in assignments if assignment["role"] != "payment-recipient"
    ]
    with pytest.raises(ValueError, match="omits operational roles: payment-recipient"):
        evaluate_synthetic_workflow_suite(scenarios, registry)


def test_protocol_aggregate_stage_keeps_every_operational_role_unresolved(
    scenarios: dict, registry: dict
) -> None:
    role_state = scenarios["part_d_protocol_attribution"]["aggregate_stage"]["role_state"]
    role_state["dispenser"] = "SYNTHETIC-PHARMACY-NORTH"
    with pytest.raises(ValueError, match="cannot resolve operational roles: dispenser"):
        evaluate_synthetic_workflow_suite(scenarios, registry)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("incompatible-population", "not product-and-population compatible"),
        ("suppression-lost", "must include suppressed underlying rows"),
        ("visible-sum-as-total", "cannot substitute for the national total"),
    ],
)
def test_protocol_denominator_and_suppression_checks_fail_closed(
    scenarios: dict, registry: dict, mutation: str, message: str
) -> None:
    aggregate = scenarios["part_d_protocol_attribution"]["aggregate_stage"]
    if mutation == "incompatible-population":
        aggregate["compatible_national_totals"][-1]["population"] = "another-population"
    elif mutation == "suppression-lost":
        aggregate["compatible_national_totals"][-1][
            "includes_suppressed_underlying_rows"
        ] = False
    else:
        aggregate["compatible_national_totals"][-1]["claims"] = 312

    with pytest.raises(ValueError, match=message):
        evaluate_synthetic_workflow_suite(scenarios, registry)


def test_protocol_peer_and_reassignment_checks_fail_closed(
    scenarios: dict, registry: dict
) -> None:
    included_candidate = copy.deepcopy(scenarios)
    included_candidate["part_d_protocol_attribution"]["aggregate_stage"][
        "latest_exact_product_peers"
    ]["rows"][0]["prescriber"] = "SYNTHETIC-PRESCRIBER-PROTOCOL"
    with pytest.raises(ValueError, match="excluded from the exact-product peers"):
        evaluate_synthetic_workflow_suite(included_candidate, registry)

    inconsistent_series = copy.deepcopy(scenarios)
    inconsistent_series["part_d_protocol_attribution"]["aggregate_stage"][
        "reassignment_series"
    ][-1]["attributed_prescriber_claims"] = 179
    with pytest.raises(ValueError, match="conflicts with yearly attribution"):
        evaluate_synthetic_workflow_suite(inconsistent_series, registry)


def test_operational_context_does_not_infer_prescriber_payment(
    scenarios: dict, registry: dict
) -> None:
    scenarios["operational_context"]["money"]["paid_to_prescriber"] = 3600.00
    with pytest.raises(ValueError, match="must not infer"):
        evaluate_synthetic_workflow_suite(scenarios, registry)


def test_dmepos_peer_must_match_exact_grain_and_exclude_candidate(
    scenarios: dict, registry: dict
) -> None:
    wrong_grain = copy.deepcopy(scenarios)
    wrong_grain["dmepos_provider_status"]["peer_cells"][0]["rental_indicator"] = False
    with pytest.raises(ValueError, match="exact comparison grain"):
        evaluate_synthetic_workflow_suite(wrong_grain, registry)

    included_candidate = copy.deepcopy(scenarios)
    included_candidate["dmepos_provider_status"]["peer_cells"][0]["supplier"] = (
        included_candidate["dmepos_provider_status"]["candidate_cell"]["supplier"]
    )
    with pytest.raises(ValueError, match="excluded from its peer cohort"):
        evaluate_synthetic_workflow_suite(included_candidate, registry)


def test_dmepos_component_coverage_precedes_dominance(scenarios: dict, registry: dict) -> None:
    scenarios["dmepos_provider_status"]["visible_components"] = {
        "respiratory": 500.00,
        "mobility": 10.00,
    }
    result = evaluate_synthetic_workflow_suite(scenarios, registry)
    assert result["dmepos_provider_status"]["dominant_visible_share"] > 0.95
    assert result["dmepos_provider_status"]["component_classification"] == (
        "unclassified-insufficient-component-coverage"
    )


def test_case_lifecycle_preserves_counterevidence_and_permission_gate(
    scenarios: dict, registry: dict
) -> None:
    missing_counterevidence = copy.deepcopy(scenarios)
    missing_counterevidence["case_lifecycle"]["evidence"] = [
        item
        for item in missing_counterevidence["case_lifecycle"]["evidence"]
        if item["direction"] != "weakens"
    ]
    with pytest.raises(ValueError, match="supporting and weakening"):
        evaluate_synthetic_workflow_suite(missing_counterevidence, registry)

    unauthorized_change = copy.deepcopy(scenarios)
    unauthorized_change["case_lifecycle"]["skill_change_proposal"]["permission"][
        "change_applied"
    ] = True
    with pytest.raises(ValueError, match="without explicit user permission"):
        evaluate_synthetic_workflow_suite(unauthorized_change, registry)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("one-action", "exactly two bounded actions"),
        ("zero-cap", "caps must be positive"),
        ("changed-disposition", "consecutive no-change outcomes"),
    ],
)
def test_case_cutoff_requires_two_bounded_no_change_actions(
    scenarios: dict, registry: dict, mutation: str, message: str
) -> None:
    actions = scenarios["case_lifecycle"]["bounded_actions"]
    if mutation == "one-action":
        actions.pop()
    elif mutation == "zero-cap":
        actions[0]["bounds"]["max_queries"] = 0
    else:
        actions[1]["changed_disposition"] = True

    with pytest.raises(ValueError, match=message):
        evaluate_synthetic_workflow_suite(scenarios, registry)


def test_upgrade_rehearsal_fails_on_silent_or_incomplete_plan(
    scenarios: dict, registry: dict
) -> None:
    scenarios["upgrade_rehearsal"]["paths"][2]["expected_action"] = (
        "replace-unchanged-baseline"
    )
    with pytest.raises(ValueError, match="expectation mismatch"):
        evaluate_synthetic_workflow_suite(scenarios, registry)


def test_fixture_contains_only_registered_tokens_and_no_npi_like_values(registry: dict) -> None:
    scenario_text = DEFAULT_SCENARIO_PATH.read_text(encoding="utf-8")
    registry_values = {entry["value"] for entry in registry["entries"]}
    used_tokens = set(re.findall(r"\bSYNTHETIC-[A-Z0-9-]+\b", scenario_text))

    assert used_tokens <= registry_values
    assert re.search(r"(?<!\d)\d{10}(?!\d)", scenario_text) is None
