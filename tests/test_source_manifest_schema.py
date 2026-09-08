import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


def test_source_manifest_schema_requires_observation_and_file_provenance() -> None:
    schema_path = Path("research/data-manifests/source-manifest.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["properties"]["manifest_version"]["const"] == 2
    assert set(schema["properties"]["status"]["enum"]) == {"draft", "complete"}
    assert "observed_at" in schema["properties"]["observation"]["required"]
    assert "public_use_verified" in schema["properties"]["terms"]["required"]
    assert schema["properties"]["files"]["minItems"] == 1
    assert "sha256" in schema["properties"]["files"]["items"]["required"]

    Draft202012Validator.check_schema(schema)


def test_source_manifest_schema_accepts_a_complete_public_source() -> None:
    schema_path = Path("research/data-manifests/source-manifest.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    manifest = {
        "manifest_version": 2,
        "status": "complete",
        "source": {"name": "Example Agency", "type": "other", "homepage": None},
        "dataset": {
            "name": "Example public dataset",
            "slug": "example-public-dataset",
            "dataset_id": None,
            "version_id": None,
            "data_year": 2025,
            "population": "Documented public aggregate population",
            "aggregation_keys": ["provider_id"],
        },
        "observation": {
            "published_at": None,
            "modified_at": None,
            "observed_at": "2026-09-01T12:00:00Z",
            "accessed_at": "2026-09-01",
        },
        "documentation": {
            "landing_page": None,
            "methodology": None,
            "data_dictionary": None,
            "snapshots": [],
        },
        "terms": {
            "license_name": None,
            "license_url": None,
            "access_restrictions": "None documented",
            "public_use_verified": True,
        },
        "retrieval": {
            "method": "download",
            "request_url": "https://example.gov/data.csv",
            "parameters": {},
            "query": None,
        },
        "files": [
            {
                "relative_path": "data/raw/example/2025/data.csv",
                "filename": "data.csv",
                "role": "data",
                "bytes": 10,
                "sha256": "a" * 64,
                "media_type": "text/csv",
                "compression": None,
                "schema_fingerprint": None,
            }
        ],
        "semantics": {
            "monetary_fields": {},
            "utilization_fields": {},
            "suppression": "None documented",
            "exclusions": [],
        },
        "validation": {
            "rows": 1,
            "columns": 1,
            "aggregation_key_duplicates": 0,
            "notes": [],
        },
    }

    Draft202012Validator(schema).validate(manifest)

    incomplete = deepcopy(manifest)
    incomplete["files"][0]["sha256"] = None
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(incomplete)


def test_public_baseline_contains_no_observed_source_manifests() -> None:
    manifest_root = Path("research/data-manifests")
    manifests = sorted(manifest_root.glob("*.json"))
    manifests.remove(manifest_root / "source-manifest.schema.json")

    assert manifests == []
