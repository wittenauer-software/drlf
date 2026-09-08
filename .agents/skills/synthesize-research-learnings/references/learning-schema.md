# Research learning schema

## Memory origins and namespaces

This schema describes case-derived learning cards in a verified standalone private research
workspace. Those cards use the workspace's private-local namespace and remain separate from shipped
public foundation knowledge. Public foundations are read-only baseline inputs to this workflow; a
combined index may route to both origins but must preserve their identifiers, origin, and provenance
rules.

In a public distribution checkout, use this card workflow only for a project-registered synthetic
exercise and keep its output on the declared synthetic test or example surface. Reconstructing a
private lesson for public use is a separate review: state the generalized principle from independently
reviewable public support, assign a fresh public-foundation identifier, and do not carry across the
private ID, cases, evidence references, source manifests, measurements, or narrative lineage.

The public distribution must declare the synthetic exercise's input case, registered identity-like
values, output path, and validator. Use only those maintained surfaces. Do not write the private-local
layout below or modify public foundations from a public-checkout exercise; if the declaration is
missing or incomplete, stop without writing.

## Repository layout

```text
research/knowledge/local/
|-- README.md
|-- index.yaml
`-- cards/
    `-- LOCAL-KNOWLEDGE-NNNN-short-slug.md
```

This layout is the private-local write target in a verified standalone private research workspace.
Within that namespace, use immutable four-digit IDs. Renaming or superseding a card does not change
its ID.

## Card statuses

- `candidate`: a scoped lesson awaiting cross-case or authoritative-source review.
- `reviewed`: provenance, limits, counterevidence, and invalidation conditions have been checked.
- `adopted`: the lesson is encoded in maintained project instructions, methods, dataset guidance, or a skill.
- `superseded`: a newer card replaces this card; retain a forward link.
- `rejected`: review found the lesson unsupported, misleading, or too context-specific to reuse.

## Learning types

- `domain`: clinical, coding, payment, policy, or operational context.
- `data`: field semantics, population coverage, suppression, linkage, refresh, or quality behavior.
- `method`: an analytical comparison, denominator, test, or validation technique.
- `process`: a research-sequencing, tooling, documentation, or stopping-rule lesson.

## Required card metadata

Each card uses YAML front matter with these fields:

```yaml
id: LOCAL-KNOWLEDGE-NNNN
title: Short neutral title
status: candidate
type: data
created_at: YYYY-MM-DD
updated_at: YYYY-MM-DD
confidence: low
scope:
  programs: []
  datasets: []
  years: []
  codes: []
  populations: []
  geography: []
source_cases: []
evidence_ids: []
source_manifests: []
authoritative_sources: []
review_after: null
supersedes: []
conflicts_with: []
adopted_in: []
```

Confidence is `low`, `medium`, or `high`; it expresses support for the scoped statement, not a probability of fraud.

Evidence IDs are case-local, so every entry is qualified as `CASE-NNNN:E-NNNN`. A card spanning
several cases must retain the case prefix on every evidence reference; never validate against a union of
unqualified IDs.

## Required narrative

Every card states the lesson, why it matters, supporting evidence, counterevidence and limits, failed approaches, reuse guidance, invalidation triggers, and append-only history. The lesson must remain meaningful when read without its originating chat.

## Index invariants

`index.yaml` contains one entry per card with `id`, `title`, `status`, `type`, `path`, `source_cases`, and `updated_at`. IDs and paths are unique. A superseded or rejected card stays indexed.
