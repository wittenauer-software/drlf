# Source manifest

From the active research workspace root, commit one manifest under `research/data-manifests/` for every
observed source release. The manifest must conform to
[`research/data-manifests/source-manifest.schema.json`](../../../../research/data-manifests/source-manifest.schema.json).
Use this CMS-oriented template and retain the same common fields for other public sources.

```yaml
manifest_version: 2
status: draft
source:
  name: Centers for Medicare & Medicaid Services
  type: CMS
  homepage: https://data.cms.gov/
dataset:
  name: ""
  slug: ""
  dataset_id: null
  version_id: null
  data_year: null
  population: ""
  aggregation_keys: []
observation:
  published_at: null
  modified_at: null
  observed_at: "YYYY-MM-DDTHH:MM:SSZ"
  accessed_at: "YYYY-MM-DD"
documentation:
  landing_page: null
  methodology: null
  data_dictionary: null
  snapshots: []
terms:
  license_name: null
  license_url: null
  access_restrictions: ""
  public_use_verified: false
retrieval:
  method: download
  request_url: null
  parameters: {}
  query: null
files:
  - relative_path: data/raw/cms/...
    filename: ""
    role: data
    bytes: null
    sha256: null
    media_type: null
    compression: null
    schema_fingerprint: null
semantics:
  monetary_fields: {}
  utilization_fields: {}
  suppression: ""
  exclusions: []
validation:
  rows: null
  columns: null
  aggregation_key_duplicates: null
  notes: []
```

Use `null` for values not yet verified; do not guess identifiers or dates. Keep `status: draft` while acquisition or validation is incomplete. Set `status: complete` only after verifying public-use access, the exact retrieval URL or request, file sizes and hashes, media types, semantics, and validation results. Only complete manifests may back a database load.

Describe each monetary field in plain language and name who may have paid or received it when the source documentation permits that conclusion.

`observed_at` identifies this retrieval observation and is not the upstream publication date. If a source changes content without changing its public version label, create a new manifest with a new observation timestamp and file hash; never replace the earlier manifest.
