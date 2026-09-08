# Case workspace schema

## Required layout

Interpret every path in this reference relative to the active research workspace root.

Create new workspaces with `drlf case-init`; use a manual copy of
`assets/case-template/` only when the command is unavailable. The initializer is non-overwriting and
assigns the next sequential ID after the highest case number present. An explicit ID is accepted only
when it is that same next sequential value. Its lock is checkout-local, so concurrent histories can
choose the same ID; the validator detects that collision only after both histories are present in one
checkout. Coordinate case pull requests, incorporate the current target branch, and run
`drlf case-validate` before merge. Resolve a collision by preserving the target branch's case
and recreating only the other, still-unmerged intake with the new next ID. Update and search every
reference before removing its old workspace; never renumber a merged, cited, published, or
database-registered case.

```text
research/cases/CASE-NNNN-short-slug/
|-- case.yaml
|-- hypothesis.md
|-- evidence-log.csv
|-- sources.md
|-- decision-log.md
|-- retrospective.md
|-- analysis/
|   |-- context-review.md  role, denominator, timeline, and evidence-gap review when applicable
|   `-- research-cutoff.md continuation value, public ceiling, and revisit triggers
`-- dossier/        report source and manifest; not an authorization to transmit
```

## Status meanings

- `triage`: identity, data semantics, or basic magnitude is still being established.
- `active`: a reproducible anomaly remains after initial checks.
- `monitor`: unresolved, but presently too weak, small, stale, or incomplete to escalate.
- `explained`: a supported ordinary explanation accounts for the observation.
- `dismissed`: the premise, evidence, identity resolution, or payment relevance failed.
- `counsel-review`: internal work satisfies the escalation gates and is ready for user review before any external contact.

## Research-cutoff invariants

Adopted process safeguard: **Stop when public actions cannot change disposition**. Resolve its current
local ID and confirm its scope through the active workspace learning catalog.

Every `case.yaml` contains a `research_cutoff` block mirrored by `analysis/research-cutoff.md`.

- `triage` and `active` require `decision: continue` or `pivot` and a populated
  `next_bounded_action` with `question`, `source`, `distinguishing_outcomes`, `triage_lane`,
  `disposition_change`, and `effort_cap`. `next_actions` contains exactly one nonempty summary of that
  bounded work package.
- `monitor`, `explained`, and `dismissed` require `decision: stop`, a null
  `next_bounded_action`, an empty `next_actions` list, and at least one specific revisit trigger.
- `monitor` requires at least one unresolved `residual_routes` entry. `explained` and `dismissed`
  require an empty `residual_routes` list because no case-specific anomaly route remains active.
- `counsel-review` requires `decision: escalate` and no public next action.
- `residual_routes`, `public_data_ceiling`, and `revisit_triggers` are lists. A cutoff basis states why
  continued public work can or cannot change the disposition.
- An `active` case cannot be justified only by records that are restricted, private, unavailable, or
  outside current authorization. Broad method development belongs in the method or engineering
  backlog and does not keep a candidate case active.

## Evidence directions

Use `supports`, `weakens`, `context`, or `unresolved`. Reliability is `primary`, `authoritative-secondary`, `secondary`, or `unverified`. A search-result snippet is not a primary source; log and cite the underlying page.

## Case manifest invariants

- `id` is immutable.
- Dates use ISO `YYYY-MM-DD`.
- `subjects` contains public provider or organization identifiers, never beneficiary identifiers.
- `applied_learnings` records prior learning IDs considered during this case and does not turn those
  cards into case evidence. Each entry contains exactly `id`, `applicability`, `outcome`, and
  `evidence_ids`. Outcome is `pending`, `supported`, `partial`, `contradicted`, or `not-applicable`;
  evidence IDs belong to the current case. Keep the matching context-review row explanatory and aligned.
- Exposure is an estimate with a named measure and basis, not a claim that funds were lost or recoverable.
- External-contact flags remain false unless the user expressly authorizes the named action.

## Retrospective invariants

- Complete the retrospective at material milestones and before a terminal or escalation disposition.
- Include methods that failed or produced no useful distinction, not only successful findings.
- Scope candidate lessons to the relevant program, dataset, year, code, population, or operational setting.
- Link every transferable claim to case evidence or source manifests. A recollection from chat is not provenance.
- Candidate lessons remain case notes until reviewed through `synthesize-research-learnings`; they do not automatically become project guidance.
- A future case must search the learning index at intake and feed material confirmation or contradiction
  back into its retrospective and the affected learning card's review process.

## Dossier invariants

- `dossier/report.md` is the human-maintained report source; a PDF is a generated view.
- `dossier/report.yaml` records the source commit, input artifacts, output path and hash, and transmission authorization.
- Report claims must trace to evidence IDs and reproducible analysis artifacts.
- Generating a report does not change any external-contact authorization flag.
