# DRLF capabilities

DRLF is an evidence-governed, agent-assisted healthcare claims-integrity research framework. It moves
from public aggregate data to reproducible, review-ready investigative leads while preserving the
difference between an unusual pattern and proof of wrongdoing.

“Learning” means reviewed procedural memory: cases can record what worked, what failed, which ordinary
explanations mattered, and what should change next time. DRLF does not retrain the model, silently
change thresholds, or turn an anomaly into a fraud finding.

## Maturity labels

- **Available:** Maintained v0.1 code, schema, asset, command, or offline workflow exists in the
  repository and has local tests.
- **Supervised:** DRLF supplies a structured method or bounded component, but a human must select or
  verify sources, parameters, interpretations, or consequential actions.
- **Planned:** The architecture anticipates the capability, but v0.1 does not provide a maintained
  end-to-end path.

Available does not mean autonomous. Live acquisition, large database work, named research, context
interpretation, disposition, and external action remain supervised even when the underlying command
exists.

## Platform capabilities

| Capability | What it provides | v0.1 maturity |
|---|---|---|
| Agent-ready repository policy | Gives an agent the evidence, privacy, authorization, workstation, change, and stopping rules needed to work consistently. | Available |
| Seven routed research skills | Provides task-specific instructions for acquisition, cases, validation, Part B, Part D protocol attribution, DMEPOS, and learning. | Available |
| Public foundation knowledge | Supplies 22 independently supported safeguards in a stable public namespace; 20 are maintained and two are explicitly experimental. | Available |
| Offline synthetic workflow | Deterministically exercises bounded acquisition, role separation, Part D time/peer/reassignment context, DMEPOS semantics, case cutoff, learning permission, and upgrade planning without live data. | Available |
| Public/private workspace separation | Creates a fresh-history private workspace from an exact public baseline, verifies its boundary, and keeps real cases out of the public checkout. | Available, with operator confirmation required |
| Non-destructive baseline upgrades | Plans a bounded three-way update, preserves local cases/skills/learning, blocks conflicts, requires separate skill approval, and supports recovery from an incomplete filesystem update. | Available, explicitly approved execution only |
| Environment, manifest, and source readiness | `doctor` checks the environment and database lineage; `release-list` inventories manifests; `source-status` checks referenced source-file presence without treating a manifest as the bytes. | Available as three separate commands |
| Reproducible source acquisition | Uses explicit API/file bounds, immutable raw paths, hashes, release metadata, methodology, and data dictionaries for supported public CMS routes. | Available components; live acquisition is supervised |
| PostgreSQL data foundation | Applies source-specific migrations and loaders while retaining release, file, run, validation, and code lineage. | Available for the supported families in the data-source catalog |
| Case management | Creates non-overwriting case structures and validates evidence, source, decision, cutoff, retrospective, and report records. | Available in a verified private workspace |
| Research stopping discipline | Requires each next action to name bounds, distinguishing outcomes, and a disposition consequence; stops when the decisive evidence is nonpublic or no qualifying action remains. | Available |
| Review-ready reporting | Maintains evidence-linked Markdown dossiers and report manifests. | Available; automated PDF rendering is planned |
| Authorized internal-claims integration | Extends the same controls to an organization's claim-line, clinical, authorization, or remittance data. | Planned and outside the public-data authorization boundary |

The command line is discoverable with `drlf --help`. A documented command is available only when it
is listed there and all assets it names ship in the same repository. DRLF v0.1 is repository-first;
the checkout, not a wheel alone, is the supported product.

Workspace separation is not a universal filesystem sandbox. Every registered command is explicitly
classified, and real-data commands fail closed unless invoked from the private system of record or a
finalized, passing private workspace. Case initialization also checks its explicit root. The agent
and operator must still review explicit input and output paths because a current-directory gate
cannot establish the audience or safety of every referenced location.

## Research capabilities

| Research family | Supported behavior | Maturity and boundary |
|---|---|---|
| Medicare Part D provider and drug | Product/provider trajectories, claims-beneficiary-fill separation, national share, concentration, adjacent-year change, and bounded first-pass context. | Available for supported releases; data selection and interpretation are supervised. |
| Part D pharmacy/protocol attribution | Separates the aggregate prescriber field from patient-specific prescriber, standing-order provider, supervisor, administrator, dispenser, pharmacy, transaction submitter, biller, plan, and payment recipient. Tests possible methodology, policy, network, and reassignment explanations. | Maintained method and synthetic exercise available; historical network and contract research is supervised. |
| Medicare Part B practitioner services | Exact HCPCS/place-of-service peers, aligned provider denominators, service reach, repeat intensity, payment/utilization measures, temporal checks, and deterministic shortlists. | Available for supported Physician product releases; medical-necessity interpretation is outside scope. |
| Medicare DMEPOS suppliers | Name-blind supplier screening, exact HCPCS/rental/entity peers, unit semantics, component coverage, material-cell completeness, market context, and point-in-time provider-status review. | Available for supported DMEPOS releases; broad downloads and identity interpretation are supervised. |
| Open Payments context | Bounded General Payments capture, source-specific organization/provider context, annual summaries, and roster-change review without equating transfers with claims reimbursement. | Available for targeted supported inputs; relationship meaning is supervised. |
| NPPES context | Exact-NPI current identity, taxonomy, and address assertions. | Bounded context is available; collection and historical interpretation are supervised. |
| CMS provider-status context | Exact-NPI enrollment or active-re-enrollment-bar snapshot review with source and observation dates. | Bounded current-snapshot context is available; it is not a historical clearance or payment record. |
| Population and geography | Enforces aligned program denominators and keeps provider address, service location, beneficiary residence, and dispensing location separate. | Available as a safeguard; broader Census and Medicare geographic adapters are planned. |

The [data-source catalog](data-sources/README.md) records the precise product families and stage-by-stage
readiness. A source being named in a schema does not mean an adapter, loader, or analysis exists.

## Seven skills

| Skill | Primary job |
|---|---|
| `acquire-cms-data` | Discover, acquire, version, and validate public CMS aggregate data under explicit limits. |
| `manage-research-cases` | Maintain intake, hypotheses, evidence, decisions, cutoff, disposition, dossier, and retrospective. |
| `validate-claims-anomalies` | Test peers, denominators, time, sensitivity, ordinary explanations, and evidence limits. |
| `investigate-part-b-service-utilization` | Evaluate provider-service reach, repeat use, care setting, and payment measures. |
| `investigate-pharmacy-protocol-attribution` | Resolve the operational roles behind pharmacy-administered drug or vaccine attribution. |
| `investigate-dmepos-supplier-outliers` | Screen and interpret DMEPOS suppliers at product, rental, entity, and unit grain. |
| `synthesize-research-learnings` | Turn reviewed outcomes into scoped local learning and governed public-improvement proposals. |

The [skill catalog](../.agents/skills/README.md) is the routing authority. Skills describe process; they
do not replace source documentation, deterministic calculations, or human judgment.

## Foundation knowledge

The public baseline contains 22 `DRLF-FND-*` records. They cover:

- Part D protocol attribution, unsuppressed national denominators, geography limits, effective-dated
  affiliations, and transaction-role separation;
- Part B aligned reach/repeat measures and frozen held-out evaluation;
- DMEPOS units, rental status, exact peers, component coverage, material-cell completeness, and
  point-in-time provider status;
- deterministic shortlists, censored or unscoreable cells, bounded API-union reconciliation,
  PostgreSQL identifier safety, robust-scale tolerance, canonical manifest bytes, bounded query-plan
  review, negative status-search limits, and decision-relevant stopping.

Two routes remain experimental: attribution-infrastructure screening for Part D and persistent
multi-code reach for Part B. Experimental records may guide a secondary analysis but cannot replace
maintained exact-cell methods or be described as validated fraud detectors.

Foundations guide method selection. They are not evidence in a case. Private case-derived learning
uses a separate namespace and retains its own provenance, scope, confidence, counterevidence, and
invalidation conditions.

## Investigation lifecycle

1. Define the program, population, release, grain, measure, period, hypothesis, ordinary alternatives,
   materiality, and decision question.
2. Acquire the smallest complete source that can answer it, under explicit byte, page, row, retry,
   worker, runtime, output, and disk limits.
3. Freeze the universe and configuration before identity review when blind evaluation is possible.
4. Calculate clearly labeled measures using aligned denominators and exact defensible peers. Preserve
   suppression and unscoreable rows as unknown or descriptive.
5. Review multiple years, methodology, policy, coding, price, operational roles, affiliations, and
   ordinary explanations.
6. Record supporting and weakening evidence, calculations, inferences, hypotheses, unknowns, and the
   exact nonpublic evidence needed.
7. Continue only with bounded actions that could change the lane or disposition; otherwise stop and
   record the disposition and revisit triggers.
8. Complete a retrospective and reconcile reusable learning. Propose a skill change when warranted,
   but wait for explicit human permission before editing skill files.

## Evidence ceiling and prohibited conclusions

DRLF does not use aggregate anomalies to determine fraud, falsity, intent, medical necessity, service
delivery, beneficiary experience, or payment receipt. It does not treat submitted charges, allowed
amounts, Medicare payments, beneficiary amounts, third-party amounts, total drug cost, provider
income, program loss, damages, or possible recovery as interchangeable.

It also does not authorize contact with a subject, publication of an allegation, submission of a
government report, contact with counsel, payment denial, credentialing, discipline, or another
adverse action. Those steps require explicit authorization and evidence beyond the public aggregate
workflow.

See [Architecture](architecture.md), [Methods](methods/README.md), and [Adding a data source](data-sources/adding-a-source.md)
for the extension contracts.
