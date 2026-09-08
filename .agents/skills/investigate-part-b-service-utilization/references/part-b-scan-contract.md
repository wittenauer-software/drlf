# Part B scan contract

## Source contract

| Source | Required grain | Primary use |
|---|---|---|
| Physician by Provider | source release × year × rendering NPI | aligned total FFS beneficiary panel and limited case-mix context |
| Physician by Provider and Service | source release × year × rendering NPI × HCPCS × F/O place of service | code-specific utilization and average monetary fields |

Resolve releases through `drlf cms-version`, preflight through
`drlf cms-api-stats`, and retain exact-filter responses through
`drlf cms-api-extract`. A manifest must record the pinned version UUID, exact filters, page and
byte caps, every retained file hash, methodology, dictionary, population, suppression, and exclusions.
The database release must retain those filters, and analysis must fail unless exactly one registered
release covers every requested year/HCPCS selection cell and the requested provider type. An
unfiltered national release is allowed as a broader source; every present specialty/code filter must
match, and any other narrowing filter (including state or NPI) must fail coverage.

## Baseline scan contract

- Keep individual and organization entities separate.
- Keep facility and office cells separate.
- Default panel eligibility: at least 100 provider-summary beneficiaries.
- Default scoreable detailed cell: at least 20 visible service beneficiaries; retain 11–19 as an
  explicit insufficient-volume descriptive row that does not contribute to peer statistics.
- Publish peer count. Retain a small-stratum row as descriptive/insufficient-peer, then broaden strata
  through a prespecified sensitivity rather than silently dropping it or interpreting the unstable
  narrow comparison.
- Label that baseline peer statistics are conditional on scoreable visible service cells, not every
  provider in the specialty.
- Publish a percentile-only broader-specialty sensitivity for narrow strata with fewer than 100 peers;
  keep it descriptive unless the full robust flag contract is satisfied.
- Use national specialty/entity/POS/panel-band peers first. Geography and case-mix matches are
  robustness checks when their peer counts are adequate.
- Publish peer median, IQR, MAD, empirical percentile, candidate/median ratio, and absolute difference.
- Treat continuous-percentile floating residue at or below the maintained `1e-12 × max(1, |median|)`
  tolerance as a zero robust scale; tied distributions must be labeled degenerate and unflagged.
- Use non-additive lanes such as reach, repeat intensity, testing mix, and payment materiality. Do not
  turn correlated measures into a pseudo-fraud score.
- Confirm a utilization lane in at least two of three comparable years before high-priority routing.
  Segment code, entity, specialty, setting, and policy discontinuities.
- Publish comparable years, 97.5th-percentile tail years, full-flag years, latest-year presence, and
  the resulting temporal route at stable NPI/provider-type/entity/HCPCS/place-of-service grain.
- Derive the review shortlist mechanically from published flags and broader-peer descriptive tails.
  Its priority is workflow routing, not a fraud score, and its evidence record must link the full
  input analysis-run ID and hash to the shortlist hash.

These are versioned research-routing defaults, not universal clinical thresholds. Record the route
version with each run. Any deviation must be declared before names are reviewed, retained in the
analysis parameters, and treated as a new version or explicitly labeled exploratory route.

## Public-data ceiling

Annual aggregates do not reveal service dates, symptoms, diagnosis linkage, orders, results, modifiers,
same-claim combinations, adjustments, medical records, billing TIN, reassignment, remittance, or final
payment recipient. Medicare Advantage and other payers are absent. The strongest supported output is an
investigative lead plus a precise map of records an authorized investigator would need.
