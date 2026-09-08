---
name: investigate-part-b-service-utilization
description: Investigate Medicare Part B provider-service utilization with aligned reach, repeat-use, care-setting, and payment measures. Use for practitioner procedure leads; not Part D, DMEPOS, or beneficiary-level conclusions.
---

# Investigate Part B service utilization

Build a reproducible exact-cell Part B screen and separate unusual reach from unusual repeat use.

Use [the Part B scan contract](references/part-b-scan-contract.md) for source grain, scoring,
temporal confirmation, and the public-data ceiling.

1. Verify the Medicare Physician and Other Practitioners release, methodology, dictionary, FFS
   population, redaction, service year, and publication lag. Do not generalize results to the
   provider's entire practice.
2. Freeze the code family and clinical/coding rationale before looking at identities. Acquire both
   provider summary and provider-service cells for aligned years when available.
3. Use exact year × HCPCS × place-of-service × specialty × entity-type peers. Align the provider-level
   denominator with the service row and retain the full provider panel used for each calculation.
4. Report service beneficiaries divided by provider beneficiaries as reach; services divided by
   service beneficiaries as repeat intensity. Keep line-service count and all monetary measures
   separately labeled.
5. Require a declared peer minimum and known positive denominators. Retain suppressed and
   insufficient-peer rows as censored or descriptive, not zero or qualifying.
6. Publish medians, upper quantiles, empirical percentile, robust-scale status, and sensitivity to
   specialty, care setting, panel band, and thresholds. A secondary persistent multi-code route must
   remain experimental and cannot replace exact-cell results.
7. Freeze full output before joining an identity or checking a named lead. Classify the held-out
   result without retuning; preserve any post-hoc analysis separately.
8. Apply `validate-claims-anomalies`, document ordinary explanations and nonpublic needs, then perform
   the research cutoff.

Use the registered synthetic Part B workflow for public-checkout validation. Public aggregates cannot
establish diagnosis, medical necessity, beneficiary experience, or knowing submission.
