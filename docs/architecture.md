# DRLF architecture

DRLF is a local, evidence-governed agent harness. It combines deterministic software with explicit
research instructions; the agent helps select and execute the maintained workflow, while files,
hashes, database records, tests, and human approvals remain the durable controls.

Version 0.1 is repository-first and supported on Windows 11 x64. It is not a hosted service,
autonomous fraud-decision system, or general-purpose claims warehouse.

## Two-workspace boundary

DRLF deliberately separates distribution from research:

```text
public DRLF checkout                         separate private workspace
--------------------                        --------------------------
policy, skills, foundations                  verified private policy
general methods and software   bootstrap     installed public baseline + hashes
synthetic examples              -------->    local cases and learning
tests and contribution history               immutable public-source observations
no real leads or case data                   PostgreSQL, derived files, reports
```

The bootstrap transfers reviewed file content from the root `baseline-manifest.json` inventory into a
new directory, then creates fresh local Git metadata without a remote. It does not copy `.git`, refs, history,
caches, data, generated output, or publication-control records. The operator must separately verify
the destination's filesystem, synchronization, backups, and remotes before finalizing it.

The command line classifies every registered command as public-safe or real-data. Real-data commands
fail closed unless the current directory is the private system of record or a finalized, passing
private workspace; case initialization also validates its explicit `--root`. A parity test prevents
new commands from shipping without a classification. This is an execution gate, not a universal
filesystem sandbox: the agent and operator must still review every explicit input and output path.

Public baseline files and private-local files have different owners:

| Ownership | Examples | Upgrade behavior |
|---|---|---|
| Public baseline | Agent policy, seven skills, 22 foundations, maintained methods, schemas, migrations, adapters, and synthetic examples. | Compared against the prior installed baseline; local drift is never silently overwritten. |
| Local research | Cases, evidence, observed manifests, raw and derived data, database rows, generated reports, and authorizations. | Never replaced or deleted by a baseline update. |
| Local extensions | User-approved skills, learning cards, adapters, methods, and configuration. | Preserved in separate namespaces; collisions require review. |
| Generated indexes | Combined public and local discovery views. | Rebuilt from validated sources while retaining origin and version. |

An upgrade is a bounded three-way comparison among the prior installed `baseline-manifest.json`
record, the current private workspace, and a new release's root `baseline-manifest.json`. Unchanged baseline paths may update after review; local
changes create a conflict; local-only content is preserved; upstream removals require an explicit
decision. Skill changes also require separate human permission.

## Research data flow

```text
authoritative public source
        |
        | bounded retrieval + source documentation
        v
data/raw/<source>/...                  immutable source bytes; ignored by Git
        |
        | manifest records request, release, size, SHA-256, grain, and semantics
        v
PostgreSQL
        |-- metadata                   releases, files, runs, checks, code lineage
        |-- claims                     source-specific public aggregate facts
        |-- reference                  provider, product, code, and geography assertions
        |-- relationships              source-specific affiliation or payment context
        `-- analytics                  reproducible run and artifact records
        |
        v
research/cases/<case-id>/              canonical narrative record in private Git
        |-- source and evidence logs
        |-- hypotheses and contrary evidence
        |-- context, decisions, cutoff, and retrospective
        `-- dossier and report manifest
        |
        v
reports/generated/<case-id>/           local generated output; ignored by Git
        |
        v
research/knowledge/local/              reviewed private learning, never case evidence
```

A manifest is a description of observed bytes, not the bytes themselves. A database volume is also
outside Git. Exact replay requires the manifest-identified raw artifact, matching code, parameters,
and compatible database lineage. Otherwise the defensible paths are a new observation or a narrative
audit, not silent substitution.

## Public baseline layout

```text
.
|-- .agents/skills/             seven baseline agent workflows
|-- docs/                       setup, architecture, capabilities, sources, and methods
|-- examples/                   registered synthetic identities and offline exercises
|-- foundations/                22 public-only knowledge records
|-- research/
|   `-- data-manifests/         source-manifest schema and private-workspace guidance
|-- sql/
|   |-- migrations/             append-only PostgreSQL schema history
|   `-- analysis/               bounded, parameterized read-only analyses
|-- src/drlf/
|   |-- sources/                bounded acquisition and manifest logic
|   |-- ingestion/              source-specific validation and loading
|   |-- analysis/               peers, measures, screening, and orchestration
|   |-- reporting/              dossier and report assembly
|   |-- workspace_lifecycle.py  bootstrap, inspection, upgrade, and recovery
|   `-- synthetic_workflows.py  deterministic offline acceptance exercise
|-- tests/                      local unit, behavior, and integration checks
|-- compose.yaml                local PostgreSQL service
|-- baseline-manifest.json      exact managed-file inventory generated at release freeze
|-- pyproject.toml              DRLF package and command identity
`-- uv.lock                     locked Python dependency graph
```

Bootstrap additionally creates `research/cases/`, `research/knowledge/local/`,
`.agents/skills-local/`, ignored `data/raw/`, `data/derived/`, `data/cache/`, and
`reports/generated/` directories in the private workspace. It initializes the empty local-learning
index there. These private-owned namespaces are not tracked in the public baseline.

If a release omits an asset named by a skill or command, that behavior is unavailable in that release.
The repository is the complete distribution contract: a Python wheel by itself would not contain the
agent policy, skills, foundations, SQL, methods, schemas, templates, and examples needed for the
harness.

## Agent, method, and knowledge layers

The layers serve different purposes:

- `AGENTS.md` defines public/private boundaries, evidence language, authorization, workstation limits,
  change workflow, and the skill-change permission gate.
- `.agents/skills/` routes recurring tasks and defines how to carry them out.
- `docs/methods/` explains analytical contracts for humans and agents.
- `foundations/catalog.yaml` contains 22 public-only safeguards and engineering controls with stable
  `DRLF-FND-*` identifiers. Twenty are maintained; two are explicitly experimental routing
  hypotheses, not validated detectors.
- private learning records what a user's cases teach inside that user's workspace. It cannot modify a
  baseline skill silently or become evidence in another case.

The seven skills cover CMS acquisition, case management, general anomaly validation, Part B service
utilization, Part D pharmacy/protocol attribution, DMEPOS supplier outliers, and research-learning
synthesis. A task may route through several skills in that order. An agent must read a selected
skill's complete instructions before acting.

## PostgreSQL and provenance

PostgreSQL stores normalized queryable rows and execution provenance, never source-file blobs. Each
load must bind to a compatible source manifest and verified file hash. Source families retain their
own population, grain, attribution role, suppression rules, utilization units, and monetary meanings;
DRLF does not flatten them into a generic `claims` or `amount` field.

Migrations are append-only. A loader is bounded, transactional, and source-specific; it registers the
release, input files, ingestion run, validations, and code version. Suppressed or unavailable values
remain unknown, not zero. Identifiers such as NPI, HCPCS, NDC, ZIP, FIPS, and GEOID remain text.

Docker Compose supplies the local service. Every checkout needs a unique `COMPOSE_PROJECT_NAME`; two
simultaneously running stacks also need different host ports. Reusing a name can attach an existing
named volume, so database identity is always verified independently of Git identity.

## Evidence ceiling

Public aggregates can identify unusual utilization, payment, concentration, reach, repeat use,
persistence, or attribution. They ordinarily cannot establish:

- whether an individual claim was false or medically unnecessary;
- whether a service or product was delivered to a beneficiary;
- who knew what, or whether conduct was intentional;
- the dispensing, administering, billing, service-location, or payment-recipient role when the source
  does not publish it;
- that total drug cost or a published payment measure was income to an attributed provider; or
- beneficiary-level facts hidden by aggregation or suppression.

Those questions may require claim lines, orders, charts, inventory, contracts, ownership, enrollment,
reassignment, transaction routing, remittance, or investigative evidence. DRLF records that need; it
does not infer the missing facts.

## Extension boundaries

Add a source through the [data-source extension contract](data-sources/adding-a-source.md), and add or
revise analytical behavior through the [methods contract](methods/README.md). A new adapter does not
make ingestion or analysis available automatically. A skill change is a separate governed action:
the agent must propose its evidence, behavior, files, boundaries, and validation and receive explicit
human permission before editing the skill.

See [Capabilities](capabilities.md) for maturity and [Getting started](getting-started.md) for the
supported Windows workflow.
