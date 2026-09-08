from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any

from drlf.sources.manifest import load_and_validate_manifest

NPPES_API_VERSION = "2.1"
NPPES_API_PAGE = "https://npiregistry.cms.hhs.gov/api-page"
NPPES_API_URL = "https://npiregistry.cms.hhs.gov/api/"
NPPES_DOWNLOAD_PAGE = "https://download.cms.gov/nppes/NPI_Files.html"


def _repository_relative_raw_path(path: Path) -> str:
    try:
        relative_path = path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"NPPES snapshot is outside the repository: {path}") from error
    if not relative_path.startswith("data/raw/"):
        raise ValueError(f"NPPES snapshot must be retained under data/raw/: {path}")
    return relative_path


def _shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _shape(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        distinct_shapes: dict[str, Any] = {}
        for item in value:
            item_shape = _shape(item)
            shape_key = json.dumps(item_shape, separators=(",", ":"), sort_keys=True)
            distinct_shapes[shape_key] = item_shape
        return [distinct_shapes[key] for key in sorted(distinct_shapes)]
    return type(value).__name__


def profile_nppes_api_snapshot(snapshot_path: Path, *, expected_npi: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9]{10}", expected_npi):
        raise ValueError("NPI must contain exactly ten digits")

    snapshot_bytes = snapshot_path.read_bytes()
    try:
        payload = json.loads(snapshot_bytes)
    except json.JSONDecodeError as error:
        raise ValueError(f"NPPES snapshot is not valid JSON: {snapshot_path}") from error

    if not isinstance(payload, dict) or payload.get("result_count") != 1:
        raise ValueError("Expected an exact-NPI NPPES response with result_count=1")
    results = payload.get("results")
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        raise ValueError("NPPES response must contain exactly one provider object")

    provider = results[0]
    if str(provider.get("number", "")) != expected_npi:
        raise ValueError("NPPES response does not match the expected NPI")
    if provider.get("enumeration_type") not in {"NPI-1", "NPI-2"}:
        raise ValueError("NPPES response has an unsupported enumeration type")
    if not isinstance(provider.get("basic"), dict):
        raise ValueError("NPPES response is missing provider basic information")
    if not isinstance(provider.get("addresses"), list):
        raise ValueError("NPPES response is missing the address collection")
    if not isinstance(provider.get("taxonomies"), list):
        raise ValueError("NPPES response is missing the taxonomy collection")

    shape = json.dumps(_shape(payload), separators=(",", ":"), sort_keys=True).encode()
    return {
        "bytes": len(snapshot_bytes),
        "sha256": sha256(snapshot_bytes).hexdigest(),
        "schema_fingerprint": sha256(shape).hexdigest(),
        "enumeration_type": provider["enumeration_type"],
        "taxonomy_count": len(provider["taxonomies"]),
        "address_count": len(provider["addresses"]),
    }


def build_nppes_api_manifest(
    snapshot_path: Path,
    manifest_path: Path,
    *,
    npi: str,
    observed_at: str,
) -> dict[str, Any]:
    if manifest_path.exists():
        raise FileExistsError(f"Refusing to replace manifest: {manifest_path}")
    if not observed_at.endswith("Z") or "T" not in observed_at:
        raise ValueError("observed_at must be an ISO-8601 UTC timestamp ending in Z")

    profile = profile_nppes_api_snapshot(snapshot_path, expected_npi=npi)
    relative_path = _repository_relative_raw_path(snapshot_path)
    request_url = f"{NPPES_API_URL}?number={npi}&version={NPPES_API_VERSION}"
    manifest = {
        "manifest_version": 2,
        "status": "complete",
        "source": {
            "name": "Centers for Medicare & Medicaid Services NPPES",
            "type": "NPPES",
            "homepage": NPPES_DOWNLOAD_PAGE,
        },
        "dataset": {
            "name": "NPI Registry API exact-NPI response",
            "slug": "nppes-npi-registry-api",
            "dataset_id": "nppes-npi-registry-api",
            "version_id": NPPES_API_VERSION,
            "data_year": None,
            "population": "One public NPPES provider record matching the requested NPI",
            "aggregation_keys": ["number"],
        },
        "observation": {
            "published_at": None,
            "modified_at": None,
            "observed_at": observed_at,
            "accessed_at": observed_at[:10],
        },
        "documentation": {
            "landing_page": NPPES_API_PAGE,
            "methodology": None,
            "data_dictionary": NPPES_API_PAGE,
            "snapshots": [],
        },
        "terms": {
            "license_name": None,
            "license_url": None,
            "access_restrictions": "Public exact-NPI API; no authentication or DUA",
            "public_use_verified": True,
        },
        "retrieval": {
            "method": "api",
            "request_url": request_url,
            "parameters": {"number": npi, "version": NPPES_API_VERSION},
            "query": None,
        },
        "files": [
            {
                "relative_path": relative_path,
                "filename": snapshot_path.name,
                "role": "api-response",
                "bytes": profile["bytes"],
                "sha256": profile["sha256"],
                "media_type": "application/json",
                "compression": None,
                "schema_fingerprint": profile["schema_fingerprint"],
            }
        ],
        "semantics": {
            "monetary_fields": {},
            "utilization_fields": {},
            "suppression": "No documented suppression for an exact public NPI lookup.",
            "exclusions": [
                "Providers other than the requested NPI",
                "NPPES does not validate licensure and its self-reported fields may be stale",
                (
                    "Addresses do not establish a claim service, dispensing, administration, "
                    "billing, or payment location"
                ),
            ],
        },
        "validation": {
            "rows": 1,
            "columns": None,
            "aggregation_key_duplicates": 0,
            "notes": [
                "The response contained exactly one provider whose NPI matched the request.",
                (
                    f"Enumeration type {profile['enumeration_type']}; "
                    f"{profile['taxonomy_count']} taxonomies and "
                    f"{profile['address_count']} addresses were present."
                ),
            ],
        },
    }

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = manifest_path.with_name(f".{manifest_path.name}.part")
    try:
        temporary_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        load_and_validate_manifest(temporary_path)
        temporary_path.replace(manifest_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return manifest
