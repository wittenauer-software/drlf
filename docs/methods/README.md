# Research methods

DRLF methods turn public aggregate data into reproducible leads, not fraud determinations. Skills tell
an agent when and how to use a workflow; foundations record reusable safeguards; methods define the
calculation, comparison, evidence, and stopping contract a human reviewer should be able to inspect.

## Common method contract

A case analysis is complete only when its registered run and linked artifacts state and retain:

1. source product and exact release, population, year, grain, attributed role, suppression, and field
   meanings;
2. frozen input universe, code or product scope, parameters, thresholds, and resource envelope;
3. complete output plus any deterministic shortlist, with the candidate excluded from peer estimates;
4. an aligned denominator, exact defensible peer keys, peer count, missingness, and unscoreable rows;
5. clearly separated utilization and monetary measures;
6. temporal, policy, coding, price, and sensitivity context;
7. ordinary explanations, evidence that weakens the hypothesis, and unresolved operational roles;
8. source, code, parameter, execution, row-count, and artifact-hash lineage; and
9. the public-data ceiling, precise nonpublic evidence need, next bounded action, disposition
   consequence, cutoff, and revisit trigger.

Some maintained commands are bounded screening helpers rather than complete registered case runs.
For example, a helper may emit a deterministic shortlist and print its hash without writing a source
release, code revision, parameter record, or execution sidecar. Do not interpret or cite that artifact
as a completed case analysis until it is linked to the missing provenance and decision records in a
verified private workspace.

Suppressed values are censored unknowns, never zero. A zero or negligible robust scale does not
justify an infinite anomaly score. Dynamic SQL identifiers are allowlisted before execution, and
large or adverse query plans are reviewed under explicit statement, row, output, and compute bounds.

## Maintained method families

| Method | Core comparison | Important limit |
|---|---|---|
| General anomaly validation | Like-with-like peers, aligned exposure, multiple years, sensitivity checks, and ordinary explanations. | An anomaly is a candidate, not evidence of falsity or intent. |
| Part B service utilization | Exact year × HCPCS × place of service × specialty × entity type; service reach and repeat intensity remain separate. | Provider-service aggregates cannot decide diagnosis or medical necessity. |
| Part D pharmacy/protocol attribution | Provider/product time series and a compatible unsuppressed national drug total, followed by a role matrix and effective-dated affiliation lanes. | The attributed prescriber field does not identify every clinical, pharmacy, billing, or payment actor. |
| DMEPOS supplier outliers | Identity-blind supplier screen, then exact year × HCPCS × rental status × entity type peers with unit and component-coverage review. | Published units do not automatically mean visits, claims, deliveries, beneficiaries, or rental months. |
| Open Payments context | Source-specific organization/provider transfer summaries and adjacent-year roster changes. | A reported transfer is not claims reimbursement, ownership, or proof of a billing relationship. |
| Provider-status context | Exact source/version/as-of snapshot with positive or negative result. | A current search is not a historical clearance, eligibility finding, or claim-validity decision. |
| Decision-relevant stopping | Continue only when a lawful, accessible, bounded action has distinguishing outcomes that could change a lane or disposition. | Broadly useful engineering does not keep a weak case active. |
| Research learning | Retrospective to scoped local learning, reconciliation, and a separately approved skill proposal. | A learning card guides method selection; it is not evidence in another case. |

Two secondary routes are experimental in v0.1: Part D attribution-infrastructure screening and
persistent multi-code Part B reach. They must remain labeled experimental, cannot replace the
maintained exact-cell workflows, and need independent validation before promotion.

## Role and amount discipline

The [maintained DMEPOS supplier method](dmepos-supplier-outliers.md) records the fixed supplier-summary
parameters, annual and temporal routes, implementation handshake, and exact-product follow-up boundary.

Do not merge patient-specific prescriber, protocol or standing-order provider, supervising provider,
administrator, dispenser, pharmacy location, transaction submitter, rendering provider, supplier,
biller, plan/PBM, and payment recipient unless a source explicitly establishes the relationship for
the relevant time.

Likewise, keep submitted charge, allowed amount, Medicare payment, standardized payment, beneficiary
or third-party amount, Open Payments transfer, total drug cost, provider income, program loss, and
potential recovery separate. Names that sound similar do not make the measures interchangeable.

## Frozen evaluation and reproducibility

When identity-blind evaluation is possible, freeze the full input universe, method, parameters, and
output before revealing a named candidate. Any tuning after reveal is a separately labeled
exploratory run. Retain complete results so a shortlist does not hide excluded or contrary rows.

Exact replay requires the manifest-identified source bytes, compatible PostgreSQL lineage, code
revision, parameters, and output hash. If the raw artifact is absent, conduct a new observation or a
narrative audit; never place new bytes under an old manifest.

## Stop and disposition

A next action qualifies only when it names the precise question, lawful public source, distinguishing
outcomes, disposition consequence, and limits for time, queries, downloads, compute, rows, and output.
Review cutoff when a supported ordinary explanation emerges, before a bulk deep dive, after two
consecutive bounded actions do not change the decision, and at material milestones.

Supported dispositions are `triage`, `active`, `monitor`, `explained`, `dismissed`, and
`counsel-review`. A disposition summarizes the research state; it is not an adjudication. External
contact, reporting, publication, or legal action requires separate explicit authorization.

## Extending a method

A method change must include:

- the question it answers and the source/population/grain for which it is valid;
- formulas, peer and denominator rules, missing/suppressed behavior, thresholds, and invalidation
  conditions;
- bounded deterministic implementation in `src/drlf/analysis/` or `sql/analysis/` when automated;
- synthetic fixtures covering ordinary, extreme, censored, degenerate, and failure cases;
- provenance and output contracts plus updated capability and data-source status; and
- evidence that the new behavior improves validity rather than only producing more candidates.

Changing a method may warrant a skill revision, but it does not authorize one. The agent must propose
the target skill, evidence/value, intended behavior and boundaries, affected files, and planned
validation, then wait for explicit human permission.

See [Capabilities](../capabilities.md), the [data-source catalog](../data-sources/README.md), and the
[source-extension contract](../data-sources/adding-a-source.md).
