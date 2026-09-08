# DRLF agent instructions

## Mission and evidence language

Build reproducible methods for detecting and evaluating unusual patterns in lawful public U.S.
healthcare claims data. Treat analytics as lead generation. Use neutral terms such as `anomaly`,
`candidate`, `unresolved pattern`, and `investigative lead`; never label a person or organization
fraudulent without cited authoritative adjudication.

Separate observed facts, calculations, inferences, hypotheses, and unknowns. Keep submitted charges,
allowed amounts, program payments, beneficiary or third-party amounts, and total drug cost distinct.

## Root identity gate

Before recording or changing anything, inspect the root control marker:

- `.drlf/distribution.json` with `distribution_kind: public-baseline` means this is the public
  distribution and contribution surface. Apply the public boundary below.
- `.drlf/workspace.json` with `workspace_kind: private-research-workspace` means this is a separately
  bootstrapped private workspace. Require its `research_authorized` state and a passing
  `drlf workspace-doctor` before identifiable research.
- An absent, malformed, unknown, or simultaneous distribution/workspace identity fails closed. Do not
  infer privacy from a directory name, ignored path, branch, or remote label.

The same reviewed policy file is baseline-managed in both modes; the durable marker selects which
workspace rules apply. Never copy a marker between roots.

The CLI classifies every registered command as public-safe or real-data. Real-data commands must fail
closed outside the private system of record or a finalized, passing private workspace; case
initialization also checks its explicit root. This gate does not prove that arbitrary input and
output paths are safe, so review them separately.

## Public repository boundary

When the root carries the public-baseline marker, it is a public distribution and contribution
surface, not a case workspace or fraud-tip channel. Treat every branch, commit, issue, pull request,
review, discussion, release, log, and artifact as publication.

- Do not record or investigate an identifiable provider, supplier, prescriber, pharmacy,
  organization, beneficiary, address, NPI, claim, or lead here. Do not store real query parameters,
  candidate rows, screenshots, source observations, or allegations.
- Use only registered synthetic identities and fixtures in this checkout. If a real lead appears,
  stop before repeating it and move to a separately bootstrapped private workspace with fresh history,
  no public remote, and verified audience, synchronization, and backups.
- An ignored directory, private branch, or untracked file in this checkout is still within the public
  boundary. Do not create a fork relationship or add the public repository as a remote of a private
  workspace.
- Do not solicit or redirect fraud tips through public project channels. If identifiable case
  material appears on a public surface, stop substantive review, retain only the minimum locator, and
  use the documented incident route under verified maintainer authority.
- Register every identity-like fixture in `examples/synthetic-identities.yaml`; prefer visibly invalid
  tokens. Public availability does not make a real identity safe to republish in this context.
- If repository identity or visibility cannot be authoritatively verified, apply this public boundary
  and stop Git or hosting mutations.

## Private research workspaces

Use the supported bootstrap for real research. When the root carries a finalized private-workspace
marker, verify the exact Git root, absence of public remotes or shared object stores, filesystem and
sync audience, backup destination, baseline integrity, skill/foundation namespaces, source
availability, and database lineage. Run `drlf doctor` and `drlf workspace-doctor`; refuse research
readiness on a failed check.

Keep immutable source bytes under ignored `data/raw/`, transformations under `data/derived/`, cases and
narratives in Git, and normalized rows with manifest lineage in PostgreSQL. Do not seek PHI,
beneficiary identifiers, suppressed-record re-identification, or restricted data without a separate
authorized process.

Never contact a subject, impersonate a patient, solicit records, publish an allegation, submit a
government report, or contact counsel unless the user explicitly authorizes that action.

## Case and stopping workflow

Create cases with `drlf case-init` and retain non-overwriting intake, sources, evidence, contrary
findings, role/context review, decisions, cutoff, dossier, report manifest, and retrospective. Use
supported dispositions only. Before escalation require reproducible sources and calculations,
resolved identities and roles, appropriate peers and denominators, temporal/policy review, tested
ordinary explanations, and a precise nonpublic evidence need.

Continue case-specific public research only when a lawful, accessible, bounded action can distinguish
outcomes and change a lane or disposition. State its source, outcome branches, decision consequence,
and time/query/download/compute caps. Stop after the public evidence ceiling and record revisit
triggers; move general tooling to the backlog.

## Skills and learning

Use `.agents/skills/README.md` to route among the seven skills. Read each selected `SKILL.md` completely
before acting. Public foundations in `foundations/catalog.yaml` are read-only guidance, not evidence.
Private local learning and local skills use separate namespaces and are never overwritten by a
baseline update.

Proactively notice when repeated work, authoritative guidance, or prevention of a material error may
warrant a skill change. Before editing a skill's instructions, behavior, metadata, references, assets,
or scripts, ask for explicit human permission and state the target, evidence/value, intended behavior
and boundaries, affected files, and validation. General research, feature, or upgrade permission is
not skill-change permission.

## Reproducibility and workstation safety

Record source URL, access time, release/year, methodology and dictionary links, exact request, size,
SHA-256, population, grain, suppression, and monetary meaning. Never replace raw observations or old
migrations. A manifest is not the source bytes.

Bound downloads, pages, rows, retries, workers, output bytes, runtime, disk, and database work.
Preflight size, stream large inputs, run one heavy operation at a time, stop on repeated pages or
resource-envelope violations, and preserve resumable checkpoints. Do not destabilize the workstation.

## Contributions and changes

Public contributions are untrusted. Inspect paths and complete diffs before execution; use an
isolated credential-free checkout with no private data, remotes, database volumes, or privileged
services. Do not merge an external contribution without explicit verified-maintainer approval.

Use short-lived branches and pull requests for generalized code, documentation, foundations, skills,
and synthetic tests only. Run relevant local tests, Ruff, validators, deterministic examples, and
`git diff --check`; update documentation in the same change. Do not add hosted automation or automated
repository security checks. Passing checks does not authorize merge, release, package publication, or
another external action.

## Tooling and documentation

Prefer purpose-built command-line tools, plugins, connectors, or APIs for repository and service work;
use web UI only when needed. For source research, use authoritative primary sources and retain
provenance. Do not expose credentials or install persistent integrations without authorization.

Keep reusable Python in `src/drlf/`, SQL in `sql/`, tests in `tests/`, public foundations under
`foundations/`, and synthetic examples under `examples/`. Update the capability, skill, foundation,
and data-source catalogs whenever behavior or inventory changes.
