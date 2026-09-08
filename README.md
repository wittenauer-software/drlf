# DRLF

**Detection, Research & Learning Framework** is an agent-ready, local-first framework for finding
and evaluating unusual patterns in public U.S. healthcare claims data. It combines bounded data
acquisition, reproducible analysis, operational-context research, structured case records, explicit
stopping rules, and governed learning so each investigation improves later work.

DRLF treats analytics as lead generation. A public aggregate can show that a provider, supplier,
service, product, or attribution pattern is unusual; it ordinarily cannot establish that a claim was
false, medically unnecessary, knowingly submitted, actually delivered, or paid to the attributed
party.

## What the first edition includes

- seven agent skills covering CMS acquisition, case management, anomaly validation, Part B,
  pharmacy/protocol attribution, DMEPOS, and research learning;
- 22 independently supported public foundations, including two clearly labeled experimental routes;
- deterministic offline synthetic exercises for the supported research families and safety limits;
- Python, SQL, PostgreSQL, source manifests, local validation, and report-source conventions;
- a safe bootstrap from this public baseline into a separate private research workspace; and
- non-destructive baseline upgrade planning that preserves local cases, data, learning, and skills.

The repository is the complete v0.1 distribution. It is not yet advertised as a standalone Python
library, hosted service, fraud-tip channel, or automated fraud decision system.

## Safe starting point

1. Read `AGENTS.md` and `docs/getting-started.md`.
2. Install the documented Windows prerequisites and run `drlf doctor`.
3. Run the offline synthetic workflow suite.
4. For a real or identifiable lead, use the supported workspace bootstrap to create a new private
   directory with fresh Git history and no public remote. Never put real case work in this checkout.
5. Start the case in that verified private workspace and keep facts, calculations, inferences,
   hypotheses, and unknowns distinct.

## Boundaries

Do not submit names, NPIs, claims, beneficiary information, screenshots, allegations, or fraud tips
through public issues, pull requests, discussions, logs, or generated artifacts. Do not use aggregate
rankings alone for payment denial, credentialing, discipline, reporting, or another adverse decision.

DRLF is independent and is not affiliated with or endorsed by CMS, HHS-OIG, NCPDP, or any other cited
agency or organization. It provides research software and methodology, not legal, medical,
reimbursement, compliance, audit, or enforcement advice. The Apache-2.0 project license does not
license third-party datasets, websites, filings, documentation, code systems, or derived data.

## Platform and distribution

The v0.1 repository-first release is validated on supported Windows environments using local checks.
It ships no hosted automation, telemetry, automatic security scanner, package publication, container
image, website, or cloud service. See `SUPPORT.md` and `SECURITY.md` for the support and disclosure
boundaries.
