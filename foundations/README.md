# DRLF public foundations

This directory is the read-only, public-baseline knowledge layer for DRLF. It contains generalized
research safeguards and engineering controls that stand on public documentation, formal reasoning,
and synthetic tests. It contains no case history, research-subject identity, private evidence, or
private learning lineage.

`catalog.yaml` is the canonical catalog. Every record has a stable `DRLF-FND-*` identifier, declared
scope and confidence, sources or formal support, limitations, reuse guidance, and invalidation
triggers. Records marked `experimental` are routing hypotheses, not validated detectors.

A private research workspace keeps case-derived learning under `research/knowledge/` in its own
local namespace. Agents may consult both layers, but must preserve each record's origin and must
never write case-derived learning into this directory. Run the maintained foundation validator after
changing the catalog.
