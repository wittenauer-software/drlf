from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

ROLE_AND_MONEY_CAVEATS = (
    "The NPI is the prescriber identifier recorded on the Part D event; it does not by "
    "itself identify the dispenser, administrator, billing entity, service location, or "
    "payment recipient.",
    "Total drug cost is a gross point-of-sale aggregate that can include ingredient cost, "
    "dispensing and applicable administration fees, tax, and funding from plans, subsidies, "
    "beneficiaries, and third parties. It is not prescriber revenue, Medicare payment, "
    "improper payment, or loss.",
    "Blank beneficiary and subgroup values can reflect suppression. This bundle preserves "
    "them as blank and never treats them as zero.",
    "National shares use the public Geography-and-Drug national row. When that denominator "
    "is unavailable, the bundle reports it as missing rather than substituting the sum of "
    "visible provider rows.",
    "Provider names, specialties, and addresses are source-specific assertions and do not "
    "establish the contemporaneous service location or operational role.",
)

MISSING_EVIDENCE_CHECKLIST = (
    "Resolve the provider identity, entity type, and time-varying taxonomy and address.",
    "Test protocol, standing-order, supervising, centralized, and default-prescriber roles.",
    "Identify dispensing outlets from PDE service-provider identifiers or PBM claim detail.",
    "Identify who administered the product and the actual administration locations.",
    "Resolve the billing workflow from NCPDP transactions, plan/PBM records, or equivalent.",
    "Resolve the legal payment recipient from remittance, enrollment, and contract records.",
    "Build effective-dated license, practice, telehealth, and company-affiliation timelines.",
    "Check structured company relationships, document the matching service, and label whether the "
    "relationship purpose is established or remains unknown.",
    "Use an appropriate Medicare opportunity denominator; use Census only as labeled context.",
    "Review product, coding, coverage, clinical-guidance, supply, and policy changes by year.",
    "Identify claim-level dates, NDCs, adjustments, reversals, inventory, and administration "
    "records needed to test the remaining hypothesis.",
)


def _display(value: Any) -> str:
    if value is None or value == "":
        return "not reported"
    return str(value)


def render_first_pass_markdown(
    *,
    case_id: str,
    candidate_npi: str,
    brand_name: str,
    generic_name: str,
    from_year: int,
    to_year: int,
    code_commit: str,
    warnings: Sequence[str],
    trajectory_rows: Sequence[Mapping[str, Any]],
    source_releases: Sequence[Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
) -> str:
    """Render a neutral first-pass review from already-produced analysis artifacts."""
    lines = [
        "# Part D first-pass research bundle",
        "",
        "## Scope",
        "",
        f"- Case: `{case_id}`",
        f"- Candidate prescriber NPI: `{candidate_npi}`",
        f"- Brand: `{brand_name}`",
        f"- Generic: `{generic_name}`",
        f"- Requested years: {from_year}–{to_year}",
        f"- Source commit: `{code_commit}`",
        "- Scope: already-loaded targeted or complete-annual public Part D Provider-and-Drug and "
        "Geography-and-Drug releases",
        "",
        "This is an anomaly-triage bundle, not a finding of fraud, falsity, medical "
        "necessity, payment recipient, or recoverable loss.",
        "",
        "## Candidate trajectory snapshot",
        "",
    ]

    if trajectory_rows:
        lines.extend(
            [
                "| Year | Claims | Beneficiaries | Total drug cost | National claim share | "
                "National denominator |",
                "|---:|---:|---:|---:|---:|---|",
            ]
        )
        for row in trajectory_rows:
            share = row.get("national_claim_share_pct")
            share_display = "not available" if share in (None, "") else f"{share}%"
            lines.append(
                "| {year} | {claims} | {beneficiaries} | {cost} | {share} | {status} |".format(
                    year=_display(row.get("data_year")),
                    claims=_display(row.get("total_claims")),
                    beneficiaries=_display(row.get("total_beneficiaries")),
                    cost=_display(row.get("total_drug_cost")),
                    share=share_display,
                    status=_display(row.get("national_denominator_status")),
                )
            )
    else:
        lines.append("No visible candidate row was found in the selected product cohorts.")

    lines.extend(["", "## Coverage and data warnings", ""])
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- No coverage warning was generated for the requested loaded releases.")

    lines.extend(["", "## Required interpretation guardrails", ""])
    lines.extend(f"- {caveat}" for caveat in ROLE_AND_MONEY_CAVEATS)

    lines.extend(["", "## Missing-evidence checklist", ""])
    lines.extend(f"- [ ] {item}" for item in MISSING_EVIDENCE_CHECKLIST)

    lines.extend(
        [
            "",
            "## Source releases",
            "",
            "| Role | Year | Version ID | Release ID | Manifest | Manifest SHA-256 |",
            "|---|---:|---|---:|---|---|",
        ]
    )
    for release in source_releases:
        lines.append(
            "| {role} | {year} | {version_id} | {release_id} | `{manifest}` | `{sha256}` |".format(
                role=_display(release.get("role")),
                year=_display(release.get("data_year")),
                version_id=_display(release.get("version_id")),
                release_id=_display(release.get("source_release_id")),
                manifest=_display(release.get("manifest_path")),
                sha256=_display(release.get("manifest_sha256")),
            )
        )

    lines.extend(
        [
            "",
            "## Reproducibility artifacts",
            "",
            "| Artifact | Rows | Analysis run | SHA-256 |",
            "|---|---:|---:|---|",
        ]
    )
    for artifact in artifacts:
        lines.append(
            "| `{path}` | {rows} | {run_id} | `{sha256}` |".format(
                path=_display(artifact.get("path")),
                rows=_display(artifact.get("rows")),
                run_id=_display(artifact.get("analysis_run_id")),
                sha256=_display(artifact.get("sha256")),
            )
        )

    lines.extend(
        [
            "",
            "The adjacent JSON manifest records parameters, selected releases, query hashes, "
            "analysis-run IDs, output hashes, warnings, and the same interpretive checklist.",
            "",
        ]
    )
    return "\n".join(lines)
