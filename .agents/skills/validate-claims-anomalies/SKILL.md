---
name: validate-claims-anomalies
description: Evaluate public healthcare-claims anomalies with aligned peers, denominators, time context, sensitivity checks, and ordinary explanations. Use for triage; never use it to declare fraud or medical necessity.
---

# Validate claims anomalies

Determine whether an unusual aggregate pattern remains a useful lead after the most important data
and operational explanations are tested.

Use [the triage rubric](references/triage-rubric.md) for the evidence lanes and disposition rules.

## Freeze the question

- State the candidate, program, source release, population, grain, measure, time window, threshold,
  peer definition, denominator, and decision consequence before identity-driven follow-up.
- Keep complete output separate from a deterministic shortlist. If evaluating a held-out identity,
  freeze the full output and configuration before reveal; later tuning is a new exploratory run.
- Preserve suppressed values as censored unknowns. Keep visible rows that lack a denominator or peer
  floor as explicitly unscoreable descriptive evidence.

## Validate

1. Resolve provider, organization, drug, service, code, and entity identity without merging distinct
   source assertions.
2. Match peers at the narrowest defensible program, code/product, place or rental state, entity type,
   specialty, population, and year grain. Publish peer count and exclusions.
3. Use aligned exposure denominators. Keep volume, reach, repeat intensity, submitted charge, allowed
   amount, program payment, beneficiary or third-party amounts, and total drug cost separate.
4. Compare multiple years and relevant coding, price, policy, clinical-guidance, and reporting changes.
5. Run sensitivity tests for peer definitions, thresholds, denominator choice, suppression, and
   robust-scale degeneracy. Version behavior-changing choices.
6. Build source-specific, effective-dated operational-role and affiliation lanes. Test ordinary
   explanations before expanding identity research.
7. State what public aggregates cannot resolve and what nonpublic evidence would confirm or refute
   each surviving hypothesis.

Escalate only when sources and calculations reproduce, identities and roles are sufficiently
resolved, peers and denominators are appropriate, temporal context is reviewed, lawful explanations
were tested, and the remaining evidence need is precise. Otherwise explain, dismiss, monitor, or
continue under a bounded decision-changing action. Never convert an anomaly score into a fraud label.
