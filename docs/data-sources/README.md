# Public data-source catalog

This catalog describes the public data-product families DRLF v0.1 knows how to acquire, persist, or
analyze. The repository ships software, schemas, methods, and synthetic fixtures—not claims data,
publisher archives, prior observations, or a populated PostgreSQL volume.

This page is the v0.1 product-family support catalog. Dataset identifiers and field contracts also
live in their source modules and manifest validators; v0.1 does not ship a second machine-readable
readiness registry. Keep this catalog synchronized with those implementations, and never infer a
later-stage capability merely from a constant or schema field.

## Readiness vocabulary

- **Available:** A maintained bounded component and its local tests ship for the stated stage.
- **Supervised:** DRLF supplies a contract or targeted component, but a human must obtain, validate,
  interpret, or scope the source for the case.
- **Planned:** No maintained v0.1 component completes that stage.

Readiness is stage-specific. An acquisition adapter does not imply a loader or analysis, and one exact
API slice does not imply a complete national peer universe.

Three records must also remain distinct:

- a **product family** is the publisher dataset with stable population, grain, roles, and semantics;
- an **observed release** is a private workspace's immutable retrieval plus manifest; and
- a **case evidence item** is a cited observation used for one research decision.

No observed real release or case record belongs in the public DRLF checkout.

## Supported CMS and related product families

| Product family | Research purpose | Acquisition and manifest | PostgreSQL | Analysis |
|---|---|---|---|---|
| Medicare Part D Prescribers by Provider and Drug | Provider/product trajectories, utilization, cost, portfolio, concentration, and attribution leads. | Available for bounded exact-filter CMS API observations. | Available in a source-specific Part D table. | Available for bounded first-pass and trajectory work; operational attribution is supervised. |
| Medicare Part D Prescribers by Geography and Drug | Compatible national drug totals and geographic context for Provider-and-Drug analyses. | Available for bounded exact-filter CMS API observations. | Available in a source-specific geography/drug table. | Available for denominator and contextual use; published geography is not automatically beneficiary or dispensing geography. |
| Medicare Physician & Other Practitioners by Provider | Annual provider-panel denominator and overall activity for the published Original Medicare FFS population. | Available for bounded exact-filter CMS API observations. | Available in a source-specific provider table. | Available as an aligned provider denominator and bounded sensitivity input. |
| Medicare Physician & Other Practitioners by Provider and Service | Exact HCPCS/place-of-service utilization, payment, reach, repeat use, peers, and shortlists. | Available for bounded exact-filter CMS API observations. | Available in a source-specific provider-service table. | Available for exact-cell screening and temporal comparison; medical necessity remains outside the source. |
| Medicare DMEPOS by Supplier | Identity-blind, materiality-oriented supplier screening before service or identity review. | Available for capped, preflighted, streamed annual files. | Available in a source-specific supplier table. | Available for deterministic anonymous screening within a verified complete universe. |
| Medicare DMEPOS by Supplier and Service | HCPCS, rental-status, unit, component, exact-peer, and market interpretation of a supplier lead. | Available for bounded exact-supplier, exact-code, and reconciled CMS API observations. | Available in a source-specific supplier-service table. | Available for code selection, exact peers, coverage, and context within the observed universe. |
| CMS Open Payments General Payments | Source-specific company/provider relationship context without treating a reported transfer as claims reimbursement. | Available for bounded SQL capture and capped file workflows; complete annual archive work is supervised. | Available for validated General Payments observations. | Available for bounded organization summaries and roster change; relationship interpretation is supervised. |
| NPPES NPI Registry | Current identity, taxonomy, enumeration, and address assertions for a specific NPI. | Supervised exact-response capture with manifest validation. | Planned. | Supervised current context only; it does not establish licensure, employment, service location, or historical identity. |
| Medicare FFS Public Provider Enrollment | Current published indication that an enrollment application was approved within the file's stated scope. | Available for bounded exact-NPI CMS API observation and manifest validation. | Planned. | Supervised point-in-time context; presence or absence does not prove historical billing, ownership, or payment receipt. |
| Revoked Medicare Providers and Suppliers | Current context for the publication's active-re-enrollment-bar population. | Available for bounded exact-NPI CMS API observation and manifest validation. | Planned. | Supervised point-in-time context; a negative search is not historical clearance. |

Exact CMS product titles and schemas can change. Before each observation, verify the authoritative
landing page, current dataset identifier, methodology, dictionary, release metadata, terms, population,
suppression, and reporting lag. Pin what was actually observed rather than relying on this catalog as
publisher metadata.

## What a clean checkout contains

A clean checkout contains acquisition and manifest logic, source-specific database code for the
available rows above, analysis methods, tests, and synthetic examples. It does not contain:

- files under `data/raw/`, `data/derived/`, or `data/cache/`;
- a user's observed release manifests;
- a PostgreSQL database or Docker volume;
- private case sources or analytical exports; or
- third-party documentation, code sets, or data redistributed under the DRLF license.

Use `drlf release-list` to inventory manifests in a private workspace and `drlf source-status` to
check whether their source bytes actually exist. Optional hash work requires an explicit aggregate
byte cap. Neither command establishes that a manifest is committed, so inspect Git state separately.

## Source semantics that must survive

Public access does not grant unrestricted redistribution. The CMS Provider and Service product
includes AMA CPT material: review the [CMS AMA license terms](https://www.cms.gov/license/ama)
and the [source dictionary](https://data.cms.gov/resources/medicare-physician-other-practitioners-by-provider-and-service-data-dictionary)
before using or sharing it. DRLF's Apache-2.0 license covers its project contributions, not that
third-party material. Generated manifests record the applicable CPT terms; `public_use_verified`
records public-source availability, not redistribution clearance. Existing retained manifests remain
immutable and may predate this clarification; review their source terms separately.

The Part B examples use registered letters-only test service identifiers with invented descriptions.
Real service identifiers are supplied by authorized research inputs in a private workspace; the
software's generic code fields do not grant rights to the data placed in them.

Every product model and manifest must preserve:

- publisher, dataset and release identifiers, service or benefit year, publication and observation
  times, methodology, dictionary, and access terms;
- population, aggregation grain, attributed identifier role, geography, exclusions, redaction, and
  suppression;
- the exact meanings of claims, beneficiaries, services, fills, days supply, units, and rental state;
- submitted charges, allowed amounts, Medicare payments, standardized payments, beneficiary or
  third-party amounts, and total drug cost as separate measures; and
- retrieval URL and parameters, stable ordering or pagination, row and byte counts, media type,
  SHA-256, validation status, and code version.

A visible-row sum is not automatically a national total when smaller rows are suppressed. A provider
address is not automatically the service, dispensing, administration, beneficiary, or payment
location. A published prescriber, rendering provider, supplier, or recipient identifier establishes
only the role defined by that source.

## Evidence ceilings by context source

- Part D aggregates do not reveal the dispensing pharmacy, administration site, transaction routing,
  plan/PBM arrangement, protocol use, reversal, remittance, or payment recipient unless the published
  fields explicitly say so.
- Part B aggregates do not reveal beneficiary diagnoses, medical necessity, ordering facts, charts,
  or knowledge.
- DMEPOS aggregates do not by themselves prove an order, delivery, inventory movement, modifier,
  rental month, ownership relationship, or claim validity.
- Open Payments reports are source-specific transfers of value, not proof of a claims relationship or
  reimbursement route.
- NPPES and provider-status files are current or point-in-time assertions, not complete historical
  biographies or enforcement clearances.

## Planned source coverage

The following are architecturally useful but not implemented as maintained v0.1 product families:

- Census ACS and reusable demographic-geography adapters;
- Medicare enrollment denominators at additional geographic grains;
- historical NPPES, enrollment, reassignment, ownership, and provider-status timelines;
- authoritative drug, HCPCS, NDC, and other code-system reference integrations;
- full annual Open Payments archive validation and ingestion; and
- beneficiary-identifiable, claim-line, clinical, authorization, billing, or remittance data.

The last category requires a separately authorized private environment and governance; it is not a
public-data extension by default.

## Extend the catalog

Follow [Adding a data source](adding-a-source.md). A new observation of an existing product usually
needs a new immutable file and manifest, not a new table. A genuinely different population, grain,
role, suppression regime, monetary meaning, or persistence model is a new family and requires a full
adapter/schema/loader/analysis review.
