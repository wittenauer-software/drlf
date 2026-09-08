from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


def load_and_validate_manifest(manifest_path: Path) -> tuple[dict[str, Any], str]:
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    schema_path = Path("research/data-manifests/source-manifest.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    if manifest["status"] != "complete":
        raise ValueError(f"Only complete manifests may be loaded: {manifest_path}")
    return manifest, sha256(manifest_bytes).hexdigest()
