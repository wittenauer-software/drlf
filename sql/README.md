# SQL

DRLF keeps database definition and analysis SQL visible and reviewable:

- `migrations/` contains ordered, immutable schema changes. `drlf db-migrate` records normalized
  hashes and rejects modified or discontinuous history. The complete pending batch runs in one
  transaction so a later failure cannot leave the schema half advanced.
- `analysis/part_b/` separates provider panels from exact HCPCS and place-of-service cells, publishes
  peer sufficiency and suppression state, and keeps reach, repeat use, units, and money in distinct
  lanes.
- `analysis/part_d/` contains source-release-bound trajectory, portfolio, leader, adjacent-year churn,
  and compatible national-denominator queries.
- `analysis/dmepos/` implements anonymous summary screening, frozen identity joins, exact HCPCS and
  rental-state peers, cohort-excluded market context, and summary reconciliation.
- `analysis/open_payments/` treats organization-recipient transfers as a separate relationship source,
  never as claims reimbursement or proof of a claims role.

The PostgreSQL schemas have distinct purposes: `metadata` retains source and execution lineage;
`stage` supports rebuildable imports; `reference` holds versioned context; `claims` stores explicit
source-specific aggregate facts; `relationships` stores source-specific affiliations or transparency
records; and `analytics` registers derived runs and artifacts.

Do not create a generic all-purpose claims table. Every product family has its own population,
attribution role, aggregation grain, suppression rules, units, and monetary meanings. Analysis must
bind exact loaded source-release IDs, verify compatible filters and years, use bounded statement and
output limits, and retain its parameters, query hash, row count, and artifact hash.

The checked-in thresholds are versioned research-routing defaults, not CMS policy, clinical rules,
fraud probabilities, or estimates of improper payment. Freeze any changed route before identity
review and record it as a distinct algorithm version. Read the corresponding skill contract and
`docs/methods/README.md` before interpreting an output.

PostgreSQL integration tests are opt-in and must use a disposable migrated database:

```powershell
$env:DRLF_RUN_DB_TESTS = "1"
$env:DRLF_TEST_DATABASE_URL = $env:DATABASE_URL
.\.venv\Scripts\python.exe -m pytest tests -q
```

Never point `DRLF_TEST_DATABASE_URL` at a database whose contents must be preserved.
