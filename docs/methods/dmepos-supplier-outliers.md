# DMEPOS supplier screening method

This guide describes the maintained implementation shipped with DRLF. Its thresholds are versioned
research-routing choices, not clinical standards, estimates of improper payment, or proof of intent.
Real source observations and identifiable outputs require a verified private research workspace.
Use the registered synthetic workflow in the distribution checkout.

## Implementation and frozen inputs

The supplier-summary contract is `dmepos-supplier-summary-materiality` version `2`, implemented in
[supplier_summary_screen.sql](../../sql/analysis/dmepos/supplier_summary_screen.sql) and checked by
[dmepos_shortlist.py](../../src/drlf/analysis/dmepos_shortlist.py). Freeze the exact code, complete
national input universe, source-release IDs, parameters, anonymous output and hashes before identity
review. The SQL checks pinned versioned releases, successful load counts, complete year coverage and
valid unique supplier/year rows. A source manifest without matching source/load evidence is insufficient.

The current parameter gate accepts this exact contract; changing it requires a reviewed method and
algorithm-version change, not silently substituting a newer year or tuning after seeing identities.

| SQL parameter | Required value |
| --- | --- |
| `data_years` | `[2022, 2023, 2024]` |
| `latest_year` | `2024` |
| `min_panel_beneficiaries` | `100` |
| `min_peer_count` | `100` |
| `min_annual_payment` | `100000` |
| `min_benchmark_exposure` | `100000` |
| `tail_percentile` | `0.975` |
| `full_percentile` | `0.99` |
| `min_robust_z` | `3.5` |
| `min_ratio` | `2` |

The last two payment thresholds are dollar amounts. SQL input percentiles are fractions; output
percentile and threshold columns use a 0–100 scale. Retain the full run signature with the output.

## Supplier-summary comparisons and routes

Peers are grouped by year, entity type, supplier specialty description, broad category and beneficiary
volume band. Bands are 100–249, 250–499, 500–999, 1,000–4,999 and 5,000 or more beneficiaries. Report
the peer count and unscoreable status; these summary comparisons do not establish exact-product peers.

Broad-category version 2 first distinguishes component coverage from dominance. With no positive total
or component payment, use `none`. Combined DME/POS/Drug coverage below 80% is `unclassified`. With
sufficient coverage, a component supplying at least 80% of the total gives its category; otherwise use
`mixed`. Suppressed component values can limit coverage and do not establish a product mix.

Keep raw Medicare payment, standardized payment, claims and services per beneficiary in separate
lanes. Publish medians, Q75, IQR, MAD, empirical percentiles, ratios, differences and scale source.
The robust-scale tolerance is `1e-12 × max(1, abs(peer median))`; an unusable scale must not produce an
infinite score. Retain the implementation's MAD/IQR fallback and null/degenerate behavior.

All annual candidate routes require three magnitude gates: total Medicare payment and both raw and
standardized Q75 benchmark exposures must each reach $100,000. Q75 exposure is distance from a
conditional peer benchmark, not a causal counterfactual, supplier income or program loss.

- A **tail year** additionally requires standardized payment and either claims or services to reach
  the 97.5th percentile with sufficient peers. The summary tail flags do not require robust z or ratio.
- A **full year** additionally requires standardized payment and the same qualifying claims-or-services
  lane to reach the 99th percentile, robust z 3.5 and twice the median, with sufficient peers.
- **High-dollar-persistent** requires all three comparable years, at least two tail years, at least one
  full year and presence of the latest year. The temporal partition keeps supplier, entity, specialty
  description and broad category aligned; it does not require the latest year itself to qualify.
- **High-dollar-emerging** applies when the persistent route fails and the latest-year row is a full year.
- **High-spend-context** is descriptive when payment alone reaches $100,000; otherwise use
  **not-qualified**. Neither route qualifies for identity review through the maintained shortlist.

`dmepos-anonymous-shortlist` validates the SQL algorithm/version and complete row/run handshake,
recomputes the routing consistency checks, and selects only latest-year persistent/emerging rows.
Retain the full anonymous output and deterministic shortlist hashes before `dmepos-join-identities`.
Use the commands' explicit input/output row and byte caps; a narrow shortlist does not justify an
unbounded upstream extraction or a claim of a fully registered case run without its provenance.

## Exact-product follow-up and stopping

The summary route is a lead for decomposition. The
[DMEPOS skill contract](../../.agents/skills/investigate-dmepos-supplier-outliers/references/dmepos-scan-contract.md)
defines national code-universe completeness, rental/unit interpretation, cohort-excluded exact
year × HCPCS × rental × entity peers, separate address-state sensitivity, component reconciliation,
and enrollment snapshot limits. Its exact-cell algorithm is a separate contract; do not substitute
the summary tail rule for its utilization, robust-scale and materiality gates.

Do not sum beneficiaries across product cells or treat supplier address as service geography.
Preserve claims, units, submitted charges, allowed amounts, Medicare payments and standardized
payments separately. Test ordinary product, rental, fee-schedule, specialty, mail-order, transition,
suppression and policy explanations before extending identity or enrollment research.

Continue only when a bounded lawful public action can distinguish explanations and change disposition.
Otherwise record the cutoff, unresolved roles, required nonpublic evidence and precise revisit trigger.
The [common method contract](README.md) and public foundations govern evidence and learning discipline.
