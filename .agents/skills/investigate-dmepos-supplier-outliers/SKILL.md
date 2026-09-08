---
name: investigate-dmepos-supplier-outliers
description: Investigate public Medicare DMEPOS supplier outliers with blind screening, exact product peers, rental context, status snapshots, and bounded stopping. Do not use for practitioner or Part D attribution.
---

# Investigate DMEPOS supplier outliers

Screen supplier patterns without letting identity, product ambiguity, or incomplete detail determine
the result.

Use [the DMEPOS scan contract](references/dmepos-scan-contract.md) for the frozen-identity,
exact-product peer, portfolio-coverage, market-context, and provider-status rules.

1. Verify supplier-summary and supplier-service releases, FFS population, aggregation keys,
   suppression, monetary fields, and reporting lag. Freeze a full national discovery universe before
   identity review.
2. Generate a deterministic anonymous shortlist from declared payment, utilization, persistence, and
   peer routes. Retain the complete input, full output, selection reasons, run manifest, and hashes.
3. Resolve exact HCPCS meaning, service-unit definition, entity type, and rental indicator. Do not
   compare unit intensity across unlike codes or rental stages.
4. Use exact year × HCPCS × rental indicator × entity-type peer cells with known positive beneficiary
   denominators and a declared peer minimum. Exclude the complete candidate cohort from peers.
5. Report claims, service units, per-beneficiary measures, submitted, allowed, Medicare, and
   standardized payment separately. Publish empirical rank, robust-scale status, and whether a row is
   scoreable, unknown-beneficiary descriptive, or insufficient-peer descriptive.
6. Reconcile selected service cells with supplier summary totals, report the visible residual, and do
   not add beneficiaries across overlapping cells. Distinguish within-supplier component coverage,
   market share, portfolio coverage, and threshold-specific material-cell completeness.
7. Treat enrollment, revocation, and exclusion files as versioned point-in-time observations. A
   negative current search is not historical clearance, present eligibility, or claim validity.
8. Test rental/resupply, fee schedule, product mix, specialty/entity coding, mail order, centralized
   operations, transitions, suppression, and policy changes. State the claim-line, modifier, order,
   delivery, inventory, beneficiary, PTAN/TIN, remittance, ownership, and knowledge evidence needed.
9. Apply `validate-claims-anomalies` and stop when no bounded public action can change disposition.

Use the registered synthetic DMEPOS workflow in the public checkout. Never treat a large published
payment total or percentile as a fraud determination.
