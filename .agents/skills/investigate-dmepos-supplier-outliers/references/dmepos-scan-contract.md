# DMEPOS supplier investigation contract

## Source layers and grains

| Layer | Required grain | Permitted use |
|---|---|---|
| DMEPOS by Supplier | release × year × supplier NPI | Anonymous broad screen, annual totals, basic post-freeze identity, summary reconciliation |
| DMEPOS by Supplier and Service | release × year × supplier NPI × HCPCS × rental indicator | Exact product decomposition, peer statistics, visible-market context |
| FFS Public Provider Enrollment (PPEF) | observation × enrollment ID | Current published enrollment context by NPI and provider type |
| Revoked Medicare Providers and Suppliers | observation × enrollment ID | Current published revocation action, authority, effective date, and active bar context |

Use a version-specific CMS dataset UUID and a complete manifest for each release or observation. A
moving `latest` alias is not provenance. Reject incomplete requested-year coverage, duplicate canonical
cells, incompatible filters, missing successful load records, or output without fixed query and byte
caps.

## Freeze and identity contract

For a broad scan, the analysis artifact may retain NPI only as an opaque longitudinal key; omit names
and addresses until these are frozen:

- code commit and source-release IDs;
- complete parameter set and candidate-cohort rule;
- full anonymous output run ID and SHA-256;
- deterministic anonymous shortlist and SHA-256; and
- maximum input, output, query-time, memory, and download envelopes.

Resolve only the exact frozen NPI/release pairs for the first identity join. Never tune thresholds after
seeing identities. If a user names a supplier before the scan, freeze the same inputs before further
analysis, clearly label the route `named`, and report whether the supplier independently meets the
maintained route. Do not retrofit a blind-discovery claim.

## Broad-category v2 contract

Apply the adopted **Separate DMEPOS component coverage from category dominance** data safeguard for
Medicare Part B fee-for-service supplier summaries. When workspace learning records are available,
locate it by title, type, and scope rather than a private numeric identifier.

The broad-category label requires two separate decisions:

```text
component_coverage = (coalesce(DME payment, 0)
                    + coalesce(POS payment, 0)
                    + coalesce(Drug payment, 0)) / total Medicare payment
component_share_x = coalesce(component x payment, 0) / total Medicare payment
```

Apply these labels in order:

1. `none` when total payment is not positive or no component payment is positive.
2. `unclassified` when component coverage is below 80%.
3. `DME`, `POS`, or `Drug` when the corresponding component supplies at least 80% of total payment.
4. `mixed` only when coverage is at least 80% but no single component reaches 80%.

Publish the three component shares and enough information to distinguish coverage from dominance.
Never interpret `unclassified` as a product mix. Preserve v1 results as frozen historical artifacts;
apply v2 prospectively rather than silently relabeling a completed run.

## Independent screen lanes

At supplier/year grain retain, at minimum:

- observed and standardized Medicare payment per beneficiary;
- claims per beneficiary;
- services per beneficiary;
- absolute published payment materiality; and
- peer count, median, Q75, IQR, MAD, empirical percentile, ratio to median, and absolute difference.

Keep payment and utilization flags independent. A route must not be driven solely by a high-priced
product or solely by the number of billing units encoded by a HCPCS code. Segment discontinuities in
code, rental status, entity type, specialty, price, coverage, and reporting methodology before calling
multiple years comparable. A Q75 benchmark exposure is distance from a peer benchmark, not an estimate
of improper payment.

Use the maintained parameters and route definitions in
`docs/methods/dmepos-supplier-outliers.md` and
`sql/analysis/dmepos/supplier_summary_screen.sql`. Any deviation must be declared and frozen before
identity review.

## National exact-code service universe

Apply the adopted methods **Resolve DMEPOS units and peers before operational inference** and
**Separate DMEPOS portfolio completeness from material-cell completeness**. The first requires exact
HCPCS/rental semantics and cohort-excluded exact peers before operational inference; the second keeps
a failed portfolio route failed while allowing only a separately proven, nonnegative,
threshold-preserving code reduction for the unchanged exact-cell materiality gate. Locate workspace
learning records by title, type, and matching scope rather than a private numeric identifier.

Freeze a sorted, unique HCPCS list before detailed acquisition. For each requested year, the union of
Supplier-and-Service manifests used for peer analysis must contain every nationally published row for
exactly that code list, subject to CMS's documented suppression. Permitted narrowing filters are the
complete exact HCPCS set only. Reject an NPI, state, specialty, address, entity, or rental filter as a
peer-universe source.

Choose the freeze contract prospectively. The portfolio freeze preserves top-N, cohort-payment, and
cumulative-visible-payment coverage and is descriptive. For an exact-cell screen with absolute
observed-payment gate `T`, use `dmepos-materiality-code-freeze` with that same `T`. It selects each code
whose rental-aggregated reconstructed Medicare payment is at least `T` for one or more candidates.
Because every validated rental-cell amount is nonnegative, no individual rental cell meeting `T` can
belong to an excluded candidate-code aggregate. This is code-filter completeness for that gate, not a
claim that a selected cell meets it or any other qualification gate. Require the distinct
`dmepos-service-code-materiality-freeze` / `1` handshake and
`candidate-observed-payment-materiality-complete` selection mode. Retain the discovery input hash,
bytes, rows, full candidate/release/parameter handshake, threshold, qualifying candidate NPIs,
per-code maximum candidate amount, and input/output/code caps; reject overwrite and silent truncation.

The analysis must verify that:

- exactly one pinned supplier-summary release and one pinned supplier-service release cover each year;
- every service manifest's filter union equals the complete requested HCPCS set, with no extra code;
- all retained service rows fall within that set;
- every release has a successful load count matching the fact rows; and
- canonical service cells are unique.

Candidate-only service rows can answer “what makes up this supplier's total?” but cannot establish
national ranks, percentiles, market shares, or robust peer effects.

## Exact peer and market contract

Compute peer distributions at exact year × HCPCS × rental indicator × entity type. Use only cells with
a known positive beneficiary denominator. Exclude the entire requested candidate cohort from every
peer distribution so a discovered cluster does not normalize itself. Publish the cohort-excluded peer
count with every result.

When supplier-address clustering is part of the observation, publish a second sensitivity that adds
the source-published supplier state to the exact peer grain. Exclude the same full candidate cohort
and apply the peer minimum separately. Keep the national and state results separately labeled; a
state-address comparison is neither beneficiary geography nor furnishing or shipment geography.

For each candidate cell, report at least:

- total claims and HCPCS service units;
- claims and services per known beneficiary;
- reconstructed Medicare and standardized payment;
- Medicare and standardized payment per known beneficiary;
- cohort-excluded median, Q75, IQR, MAD, empirical percentile, high-rank, ratio to median, robust z,
  and the MAD/IQR/degenerate scale label; and
- whether the row is `scoreable`, `unknown-beneficiary-descriptive`, or
  `insufficient-peer-descriptive` under the frozen peer minimum.

The maintained exact-cell algorithm publishes its name, version, and fixed thresholds with every
row. Under version `dmepos-supplier-service-exact-cell` / `1`, a cell passes the tail route only when
reconstructed raw Medicare payment is at least $100,000, both raw and standardized Q75 payment
distances are at least $100,000, and the same claims-or-services lane reaches the 97.5th percentile,
robust z 3.5, and twice the peer median. The stricter full route changes the percentile to 99 while
retaining the other gates. Raw and standardized payment percentile flags remain diagnostics and do
not replace independent utilization.

Use MAD when its scale exceeds `1e-12 × max(1, abs(peer median))`; otherwise use IQR as the declared
fallback when it exceeds that tolerance. If neither scale is usable, publish a null robust z and fail
the affected lane closed. A temporal route partitions by exact supplier NPI × HCPCS × rental
indicator × entity type and requires a qualifying maximum requested year plus at least one qualifying
earlier requested year. Missing years remain absent; do not manufacture zero-use cells or sum claims
or beneficiaries across cells. Changing these fixed thresholds or routing semantics requires a new
algorithm version.

Do not compare service-unit intensity across unlike HCPCS codes. When a code has an inherently small
national cohort, report the market's size and concentration but do not treat an extreme rank as robust
evidence. Suppressed beneficiary counts remain unknown and are excluded from per-beneficiary peer
statistics, not converted to zero.

Do not begin broad company, capacity, enrollment, ownership, or enforcement research merely because a
summary or percentile is large. Continue those lanes only for a material exact-cell survivor, or when
a prospectively identified authoritative source can independently change the case disposition. An
underpowered descriptive cell may receive an explicit revisit trigger without keeping an unlimited
identity-search route open.

National, address-state, candidate-cohort, top-supplier, and concentration totals use visible published
cells only. Supplier address is not beneficiary or furnishing geography. Never call a visible-cell
share an unsuppressed national market share.

## Summary reconciliation

Within each candidate/year, sum the selected exact-code cells separately for:

- service units;
- reconstructed submitted charge;
- reconstructed Medicare allowed amount;
- reconstructed Medicare payment; and
- reconstructed standardized payment.

Compare them with the corresponding supplier-summary totals. Publish coverage ratios and the
unrepresented residual. Allow for rounding from average-per-unit fields and for products outside the
selected code list. A close payment reconciliation supports product concentration; a material residual
requires additional code decomposition before interpreting the selected set as the supplier's whole
portfolio.

Do not reconcile beneficiaries by addition. Use the maximum visible code cell only as a labeled lower
bound when useful; overlap across products and rental states is unknown.

## Enrollment and revocation context

Apply the adopted **Treat provider-status files as point-in-time snapshots** data safeguard: preserve
release and observation context, enrollment IDs, provider types, effective dates, and each file's
inclusion rules; never turn snapshot presence or absence into unsupported historical status. Locate a
workspace learning record by title, type, and matching scope rather than a private numeric identifier.

Query current PPEF and Revoked Medicare Providers and Suppliers snapshots by a closed exact NPI list
under one-page, row, response-byte, and cumulative-byte caps. Retain the exact API inventory and a
manifest with the version UUID, observation timestamp, matched NPIs, and missing NPIs.
Use `cms-api-extract-in` for the bounded capture and `cms-provider-records-api-manifest` for the
source-specific validation and manifest.

For each published row, preserve:

- NPI and enrollment ID;
- organization or individual name;
- state and provider/enrollment type;
- revocation authority or reason;
- revocation effective date; and
- re-enrollment-bar expiration date.

Multiple rows for one NPI are separate enrollment records. Align claims year, enrollment type, and
effective date without assuming that a calendar-year claims total occurred on either side of the
action. PPEF describes the qualifying current published enrollment population at its observation; it
is not an enrollment history. The revocation file has its own active-bar, appeal, and publication-lag
criteria. Absence from either file cannot fill the other's role.

## Validation and stopping

Challenge high units, high payment, and enrollment context as distinct hypotheses. Test applicable
product-unit definitions, fee schedules, rental/resupply patterns, specialty and entity coding,
mail-order or centralized operations, market-wide geographic concentration, policy changes, NPI or
corporate transitions, suppression, and reporting lag.

State the nonpublic evidence needed to resolve remaining questions, including claim-line service dates,
HCPCS modifiers, orders and referring NPIs, clinical documentation, proof of delivery, inventory,
beneficiary confirmation, PTAN/TIN and billing records, remittance and payment recipients, enrollment
applications and actions, ownership/control records, appeals, and knowledge evidence.

Apply `validate-claims-anomalies` and the adopted **Stop when public actions cannot change
disposition** process safeguard. Continue case-specific public research only when a lawful, accessible,
bounded action can distinguish competing explanations and change a triage lane or disposition. After
two bounded no-change actions, before a bulk acquisition, or when annual aggregation makes timing
decisive, perform the cutoff review. Move general detector work to the method backlog rather than
keeping the case active. When a workspace learning record is available, find it by title, `process`
type, and public-healthcare-claims scope rather than a private numeric identifier.
