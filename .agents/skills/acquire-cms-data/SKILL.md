---
name: acquire-cms-data
description: Discover, acquire, version, and validate public CMS aggregate datasets under explicit resource bounds. Use for reproducible source capture; do not use for restricted or beneficiary-identifiable data.
---

# Acquire CMS data

Create an immutable public-source observation and a manifest that lets another researcher reproduce
and correctly interpret it.

Use [the source-manifest contract](references/source-manifest.md) when defining or reviewing a
retained observation.

## Boundary and preflight

- Retain a real observation only in a verified standalone private research workspace. In the public
  checkout, use only the registered offline synthetic acquisition exercise.
- Stop if access requires a data-use agreement, payment, beneficiary-identifiable information, a
  qualifying research-purpose assertion, or re-identification.
- Prefer an exact bounded API request over a bulk file. Declare byte, page, row, retry, worker,
  runtime, disk, and output caps before transfer. Run only one heavy transfer, extraction, load, or
  full transformation at a time.
- Use authoritative CMS landing pages, methodology, data dictionaries, and API metadata. Distinguish
  service year, publication date, release/version, refresh, and observation time.

## Capture and validate

1. Preflight any remote artifact using published size metadata or a bounded range probe. Refuse a
   transfer without an explicit maximum.
2. Write untouched bytes to a new versioned `data/raw/` path. Never replace an observation in place.
3. Record exact URL and parameters, access time, dataset and release identifiers, population, grain,
   suppression, exclusions, methodology and dictionary links, byte count, media type, and SHA-256.
4. For paged or batched APIs, freeze the target union and reconcile keys, pages, rows, bytes, and
   hashes. Stop on repeated pages, overlaps, omissions, or a cap.
5. Validate headers, parsing, duplicate aggregation keys, nulls, suppression indicators, and source
   semantics without modifying raw bytes. Put transformations in `data/derived/` with code lineage.
6. State exactly what each utilization and monetary field measures. Shared identifiers or calendar
   years do not prove aligned populations.

Acquisition proves provenance, not suspicious conduct. Run the offline synthetic suite to verify
limit and reconciliation behavior before adapting an acquisition route.
