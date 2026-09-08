# Source manifests

Store one immutable manifest for each source observation. The manifest records the exact request,
release identity, observation time, population, grain, suppression, monetary meanings, retained-file
size and SHA-256, and validation results while large source bytes remain ignored under `data/raw/`.

Use `source-manifest.schema.json` for every product family. A clean checkout contains the schema and
reproducible tooling, not prior source observations or another researcher's raw data. In a verified
private workspace, create a new manifest for every reobservation; never place new bytes under an old
manifest or overwrite retained raw data.

`drlf release-list` inventories local manifests and `drlf source-status` checks whether their retained
files exist. Neither command proves that a manifest is committed, that a database was restored, or
that two releases describe compatible populations.
