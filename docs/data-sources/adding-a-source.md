# Adding or extending a data source

Use this contract when DRLF needs another release of a supported product or a genuinely new data
family. A schema enum, documentation mention, or one-off research file does not make a source an
implemented capability.

Real observations belong only in a verified private research workspace. A public contribution may
add generalized source software, documentation, and registered synthetic fixtures; it must not include
real query parameters, retained responses, candidate values, provider identities, case evidence, or
private research lineage.

## First classify the change

Use the existing-family path only when publisher product, population, grain, identifier role,
suppression, field semantics, and acquisition mode match the current catalog contract.

Treat the work as a new family when any of these differ materially:

- program or covered population;
- row grain or aggregation keys;
- provider, supplier, prescriber, dispenser, beneficiary, organization, or geography role;
- suppression, exclusion, redaction, or reporting-lag rules;
- utilization unit, rental state, or monetary meaning; or
- persistence and update model.

Do not clone an existing claims table and rename the columns.

## Existing family: another release or slice

1. Re-read the authoritative landing page, methodology, dictionary, release metadata, terms, update
   cadence, suppression rules, and reporting lag. Distinguish service year, publication date,
   modification date, and observation time.
2. Confirm that the current adapter supports the intended retrieval mode. Prefer the narrowest exact
   query that fully answers the question; use a complete annual source when the analysis requires a
   complete peer universe.
3. Preflight size and declare byte, page, row, retry, worker, runtime, output, raw, extracted, and
   database caps. Stop on repeated pages, overlaps, truncation, drift, or any exceeded limit.
4. Write untouched bytes to a new versioned `data/raw/` path. Never overwrite an observation, even if
   the publisher reused a label or filename.
5. Create a new manifest with the request, filters, observed version, documentation, population,
   grain, roles, suppression, exclusions, sizes, media types, hashes, and field meanings. Use `null`
   for unknown facts rather than guessing.
6. Load only when the catalog marks that acquisition mode compatible with a maintained loader. Verify
   the manifest and raw hash first and retain release, file, run, quality, and code lineage.
7. Run the family's adapter, manifest, loader, CLI, and analysis tests. Update its catalog scope and
   source guide when supported semantics or readiness changed.

If any premise fails, stop and follow the new-family path.

## New family contract

### 1. Establish authority and meaning

Record the authoritative publisher, stable product identity, landing page, version system,
methodology, dictionary, terms or license, public-use status, update cadence, and known lag. Define:

- population and time basis;
- row grain and unique aggregation key;
- every identifier's operational role;
- geography meaning;
- utilization units;
- suppression, redaction, and exclusions; and
- every monetary field's documented meaning.

Classify the source as claims, reference, relationship, policy, or other evidence. Shared NPIs, codes,
or years do not make two datasets semantically interchangeable.

Stop for user direction if access requires PHI, beneficiary identification, re-identification,
credentials not already authorized, payment, a data-use agreement, or an assertion of a qualifying
research purpose. Public DRLF's default acquisition boundary is lawful public-use aggregate data.

### 2. Define the resource envelope

Before transfer, obtain remote size from publisher metadata or a bounded range probe when practical.
Set hard limits for:

- bytes, pages, rows, retries, redirects, workers, and elapsed time;
- per-response and total output;
- raw, extracted, temporary, and database disk footprint; and
- memory, query statement time, and result rows.

Define stable ordering, pagination/repeated-page detection, streaming or chunking, resumable
checkpoints, and terminal stop conditions. Run only one heavy download, extraction, load, or complete
transformation at a time.

### 3. Implement bounded acquisition

Add the smallest source-specific adapter under `src/drlf/sources/`, reusing a bounded primitive only
when its pagination, ordering, filtering, and error contract matches. Require pinned versions where
available, deterministic new paths, explicit filters, immutable output, and refusal to overwrite.

Validate response identity, headers, schema fingerprint, requested filters, aggregation-key
uniqueness, null and suppression representation, identifier formats, byte and row counts, and hashes.
For batched APIs, freeze the requested union and reconcile keys across every batch; fail on omissions,
overlaps, duplicates, or drift.

### 4. Define the source manifest

Use the maintained source-manifest schema and the `acquire-cms-data` skill's contract. The common
schema supplies provenance fields but cannot prove the correct dataset, grain, filters, or semantics;
add source-specific validation in code.

A complete record includes exact request and access time, publisher dataset/release, population,
grain, roles, suppression, exclusions, methodology and dictionary references, file names, byte and
row counts, media types, SHA-256 values, and validation outcomes. Keep source files immutable under
`data/raw/` and transformations under `data/derived/` with code lineage.

### 5. Add PostgreSQL persistence only when needed

- Add the next append-only migration under `sql/migrations/`; never edit a migration already applied
  to a database.
- Model the source explicitly in `claims`, `reference`, or `relationships`, with a
  `metadata.source_release` link and a primary key matching the source grain.
- Store NPI, NDC, HCPCS, ZIP, FIPS, GEOID, and similar identifiers as text. Preserve suppressed and
  unavailable values as unknown, not zero.
- Keep utilization and monetary measures distinct. Add appropriate checks, indexes, comments, and
  effective dates supported by the source.
- Implement a bounded transactional loader under `src/drlf/ingestion/`. It verifies dataset identity,
  manifest compatibility and hash, raw-file integrity, expected schema, and quality rules before
  marking a release loaded.

PostgreSQL stores normalized rows and provenance. It is not the source-byte archive or the canonical
case narrative.

### 6. Add analysis deliberately

Expose a narrow CLI route only after its inputs, bounds, and failure behavior are understood. Put
reusable read-only SQL under `sql/analysis/` and orchestration under `src/drlf/analysis/`.

An analysis must bind exact source releases, declare its population, grain, peers, denominator,
suppression behavior, thresholds, output cap, and stopping condition. Record parameters, code
revision, row counts, execution status, and artifact hashes. Acquisition, persistence, and analysis
readiness remain separate catalog fields.

### 7. Test the full contract

Use only small registered synthetic fixtures in the public repository. Cover, as applicable:

- manifest schema, release identity, source grain, fields, schema drift, and canonical bytes;
- filters, stable pagination, repeated pages, reconciled batches, bytes/rows/time limits, immutable
  output, malformed identifiers, duplicates, nulls, suppression, and numeric precision;
- row mapping, transactions, repeat loads, release/file/run/quality lineage, and failure cleanup;
- new migrations and focused queries against a disposable database;
- CLI routing and safe defaults; and
- peer, denominator, temporal, source-coverage, output-limit, and provenance behavior.

Never put raw claims data, PHI, credentials, real NPIs, or large source extracts in tests or Git.

## Catalog, methods, skills, and documentation

In the same change:

1. add or update the stable product contract in its source module and the public data-source catalog;
2. add a source guide with authoritative references and evidence limits;
3. update [Architecture](../architecture.md) for new storage or runtime behavior;
4. update [Capabilities](../capabilities.md) only for functionality proven in the change;
5. update [Methods](../methods/README.md) for a new analytical contract; and
6. update every command example and cross-reference affected by the interface.

Adding a source does not automatically justify a skill change. If repeated work, authoritative
guidance, or prevention of a material error warrants one, the agent must first propose the exact
skill, evidence/value, behavior and boundaries, files, and validation. Wait for explicit human
permission before editing any skill instruction, metadata, reference, asset, or script.

## Review checklist

From the repository root on Windows:

```powershell
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

Also run every relevant manifest, adapter, migration, loader, CLI, database, analysis, catalog, link,
and synthetic-workflow check. Use a disposable test database, inspect the complete diff, confirm that
no real observations or generated outputs are staged, and document the source-rights and dependency
impact in the pull request.
