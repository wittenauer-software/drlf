---
name: manage-research-cases
description: Create and maintain auditable healthcare-claims research cases with hypotheses, provenance, decisions, cutoffs, and dispositions. Use for case administration; do not substitute it for statistical or legal judgment.
---

# Manage research cases

Maintain a record that another researcher can audit without relying on chat history.

Use the managed files in `assets/case-template/` for case structure and
`scripts/validate_cases.py` for validation. The template is copied by `drlf case-init`; do not edit it
as a shortcut for updating an existing case. See [the case schema](references/case-schema.md) when
reviewing fields, evidence links, or disposition invariants.

## Workspace gate

The public DRLF checkout accepts only registered synthetic cases. Before recording a real identity,
source observation, or lead, move to a separately bootstrapped private workspace and verify its
audience, Git identity, remotes, synchronization, backups, and marker. A hidden or ignored directory
inside the public checkout is not private.

## Case lifecycle

1. Use `drlf case-init` so identifiers are monotonic, templates are complete, and existing paths are
   never overwritten. Begin with a neutral title, observed fact, falsifiable question and hypothesis,
   evidence that would support or weaken it, one bounded next action, distinguishing outcomes,
   disposition consequence, and resource cap.
2. Commit or otherwise freeze intake before binding data loads or analyses to the case.
3. Search `foundations/catalog.yaml` and the private workspace's local-learning index by title, type,
   and full scope. Record every considered item and why it applies, conflicts, or is inapplicable.
   Prior knowledge guides method selection but is not case evidence.
4. Keep source register, evidence log, hypothesis, role/context review, decision log, cutoff review,
   dossier, report manifest, and retrospective synchronized. Classify facts, calculations,
   inferences, hypotheses, and unknowns.
5. Preserve evidence that weakens the hypothesis and plausible ordinary explanations with the same
   provenance as supporting evidence.
6. Use only supported dispositions: `triage`, `active`, `monitor`, `explained`, `dismissed`, and
   `counsel-review`. Run `drlf case-validate` after structural changes.

Continue public-source work only when a lawful, accessible, bounded action can change a lane or
disposition. Record precise revisit triggers and nonpublic evidence needs before stopping. Do not
contact a subject, publish an allegation, submit a report, or contact counsel without explicit
authorization for that action.
