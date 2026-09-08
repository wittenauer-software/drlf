# DRLF skill catalog

These seven skills are the maintained workflows shipped with DRLF. They guide reproducible
claims-integrity research; they do not determine fraud, medical necessity, intent, or the validity of
an individual claim.

| Skill | Use it for | Boundary |
|---|---|---|
| [Acquire CMS Data](acquire-cms-data/SKILL.md) | Bounded discovery, capture, validation, and provenance for public CMS data. | Public-use aggregate data only. |
| [Manage Research Cases](manage-research-cases/SKILL.md) | Auditable case intake, evidence, decisions, cutoff, disposition, and retrospective. | Identifiable work belongs only in a verified private workspace. |
| [Validate Claims Anomalies](validate-claims-anomalies/SKILL.md) | Peer, denominator, time, sensitivity, and ordinary-explanation tests. | An anomaly is lead generation, not an adjudication. |
| [Investigate Part B Service Utilization](investigate-part-b-service-utilization/SKILL.md) | Provider-service reach, repeat-use, and payment analysis. | Use aligned Part B cells and populations. |
| [Investigate Pharmacy Protocol Attribution](investigate-pharmacy-protocol-attribution/SKILL.md) | Prescriber, protocol, pharmacy, administration, billing, and payee role resolution. | Do not treat an attributed NPI as every actor. |
| [Investigate DMEPOS Supplier Outliers](investigate-dmepos-supplier-outliers/SKILL.md) | Product-specific supplier utilization and payment outlier analysis. | Resolve units, rental state, exact peers, and coverage first. |
| [Synthesize Research Learnings](synthesize-research-learnings/SKILL.md) | Provenance-linked local learning and public-foundation proposals. | Never overwrite baseline knowledge or silently edit a skill. |

Load only the skills that match the task, in workflow order. Resolve public safeguards by semantic
title and scope in `foundations/catalog.yaml`; foundation records guide a case but are not evidence
for it. Public skills are baseline-managed. User-approved local skills occupy a separate namespace
and cannot shadow or be overwritten by this catalog.

## Skill changes

Agents should notice reusable improvements without waiting for the user to maintain a backlog. Before
creating or changing any skill behavior, instructions, metadata, reference, asset, or script, present
the target, evidence and value, scope and behavior change, affected files, and planned validation.
Wait for explicit human permission. General authorization to investigate, update DRLF, or install a
new baseline is not skill-change permission.
