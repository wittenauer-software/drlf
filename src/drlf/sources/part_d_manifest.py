from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any

from drlf.sources.manifest import load_and_validate_manifest

DATASET_NAME = "Medicare Part D Prescribers - by Provider and Drug"
DATASET_SLUG = "medicare-part-d-prescribers-by-provider-and-drug"
DATASET_TYPE_UUID = "9552739e-3d05-4c1b-8eff-ecabf391e2e5"
LANDING_PAGE = (
    "https://data.cms.gov/provider-summary-by-type-of-service/medicare-part-d-prescribers/"
    "medicare-part-d-prescribers-by-provider-and-drug"
)
METHODOLOGY_URL = "https://data.cms.gov/resources/medicare-part-d-prescribers-methodology"
DICTIONARY_URL = (
    "https://data.cms.gov/resources/"
    "medicare-part-d-prescribers-by-provider-and-drug-data-dictionary"
)

AGGREGATION_KEYS = ("Prscrbr_NPI", "Brnd_Name", "Gnrc_Name")
REQUIRED_COLUMNS = frozenset(
    {
        *AGGREGATION_KEYS,
        "Prscrbr_Last_Org_Name",
        "Prscrbr_First_Name",
        "Prscrbr_City",
        "Prscrbr_State_Abrvtn",
        "Prscrbr_State_FIPS",
        "Prscrbr_Type",
        "Tot_Clms",
        "Tot_30day_Fills",
        "Tot_Day_Suply",
        "Tot_Drug_Cst",
        "Tot_Benes",
        "GE65_Sprsn_Flag",
        "GE65_Tot_Clms",
        "GE65_Tot_30day_Fills",
        "GE65_Tot_Day_Suply",
        "GE65_Tot_Drug_Cst",
        "GE65_Bene_Sprsn_Flag",
        "GE65_Tot_Benes",
    }
)


def _repository_relative_path(path: Path) -> str:
    try:
        relative_path = path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"Snapshot file is outside the repository: {path}") from error
    if not relative_path.startswith("data/raw/"):
        raise ValueError(f"Snapshot file must be retained under data/raw/: {path}")
    return relative_path


def profile_api_pages(inventory_path: Path) -> dict[str, Any]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    snapshot_dir = inventory_path.parent
    rows = 0
    columns: set[str] = set()
    keys: set[tuple[str, str, str]] = set()
    duplicates = 0
    invalid_npis = 0
    filter_mismatches = 0
    rows_missing_required_columns = 0
    expected_filters = inventory["filters"]

    for page in inventory["pages"]:
        page_path = snapshot_dir / page["relative_filename"]
        body = page_path.read_bytes()
        if len(body) != page["bytes"] or sha256(body).hexdigest() != page["sha256"]:
            raise ValueError(f"Page inventory integrity failed: {page_path}")
        page_rows = json.loads(body)
        if not isinstance(page_rows, list):
            raise ValueError(f"API page is not a row array: {page_path}")
        if len(page_rows) != page["rows"]:
            raise ValueError(f"Page row count differs from inventory: {page_path}")
        for row in page_rows:
            if not isinstance(row, dict):
                raise ValueError(f"API page contains a non-object row: {page_path}")
            rows += 1
            columns.update(row)
            if not REQUIRED_COLUMNS.issubset(row) or not (
                "Prscrbr_Type_Src" in row or "Prscrbr_Type_src" in row
            ):
                rows_missing_required_columns += 1
            npi = str(row.get("Prscrbr_NPI", ""))
            if not re.fullmatch(r"[0-9]{10}", npi):
                invalid_npis += 1
            key = tuple(str(row.get(field, "")) for field in AGGREGATION_KEYS)
            if key in keys:
                duplicates += 1
            keys.add(key)
            for field, expected_value in expected_filters.items():
                if str(row.get(field, "")).casefold() != expected_value.casefold():
                    filter_mismatches += 1

    if rows != inventory["total_rows"]:
        raise ValueError("Inventory total row count does not match its pages")
    if invalid_npis or duplicates or filter_mismatches or rows_missing_required_columns:
        raise ValueError(
            "API page validation failed: "
            f"invalid_npis={invalid_npis}, duplicates={duplicates}, "
            f"filter_mismatches={filter_mismatches}, "
            f"rows_missing_required_columns={rows_missing_required_columns}"
        )

    column_list = sorted(columns)
    return {
        "rows": rows,
        "columns": column_list,
        "column_count": len(column_list),
        "aggregation_key_duplicates": duplicates,
        "schema_fingerprint": sha256("\n".join(column_list).encode()).hexdigest(),
    }


def build_part_d_api_manifest(
    inventory_path: Path,
    manifest_path: Path,
    *,
    data_year: int,
    modified_at: str | None,
    documentation_snapshots: list[str],
) -> dict[str, Any]:
    if manifest_path.exists():
        raise FileExistsError(f"Refusing to replace manifest: {manifest_path}")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if not inventory.get("dataset_uuid"):
        raise ValueError("Inventory is missing its version-specific CMS dataset UUID")
    if not inventory.get("pages"):
        raise ValueError("Inventory does not contain any retained API pages")
    profile = profile_api_pages(inventory_path)
    snapshot_dir = inventory_path.parent
    filters = inventory["filters"]
    filter_description = ", ".join(f"{key}={value}" for key, value in sorted(filters.items()))

    file_records: list[dict[str, Any]] = []
    for page in inventory["pages"]:
        page_path = snapshot_dir / page["relative_filename"]
        file_records.append(
            {
                "relative_path": _repository_relative_path(page_path),
                "filename": page_path.name,
                "role": "api-page",
                "bytes": page["bytes"],
                "sha256": page["sha256"],
                "media_type": page["media_type"],
                "compression": None,
                "schema_fingerprint": profile["schema_fingerprint"],
            }
        )

    inventory_bytes = inventory_path.read_bytes()
    file_records.append(
        {
            "relative_path": _repository_relative_path(inventory_path),
            "filename": inventory_path.name,
            "role": "acquisition-inventory",
            "bytes": len(inventory_bytes),
            "sha256": sha256(inventory_bytes).hexdigest(),
            "media_type": "application/json",
            "compression": None,
            "schema_fingerprint": None,
        }
    )

    manifest = {
        "manifest_version": 2,
        "status": "complete",
        "source": {
            "name": "Centers for Medicare & Medicaid Services",
            "type": "CMS",
            "homepage": "https://data.cms.gov/",
        },
        "dataset": {
            "name": DATASET_NAME,
            "slug": DATASET_SLUG,
            "dataset_id": DATASET_TYPE_UUID,
            "version_id": inventory["dataset_uuid"],
            "data_year": data_year,
            "population": (
                "Medicare beneficiaries enrolled in Part D with final-action prescription "
                "drug events attributed to a valid prescriber NPI; targeted public detail rows"
            ),
            "aggregation_keys": list(AGGREGATION_KEYS),
        },
        "observation": {
            "published_at": None,
            "modified_at": modified_at,
            "observed_at": inventory["observed_at"],
            "accessed_at": inventory["observed_at"][:10],
        },
        "documentation": {
            "landing_page": LANDING_PAGE,
            "methodology": METHODOLOGY_URL,
            "data_dictionary": DICTIONARY_URL,
            "snapshots": documentation_snapshots,
        },
        "terms": {
            "license_name": None,
            "license_url": None,
            "access_restrictions": "CMS Public Use File (Free); no authentication or DUA",
            "public_use_verified": True,
        },
        "retrieval": {
            "method": "api",
            "request_url": inventory["pages"][0]["url"],
            "parameters": {
                "filters": filters,
                "page_size": inventory["page_size"],
                "max_pages": inventory.get("max_pages"),
                "pagination": "offset",
            },
            "query": None,
        },
        "files": file_records,
        "semantics": {
            "monetary_fields": {
                "Tot_Drug_Cst": (
                    "Ingredient cost, dispensing fee, sales tax, and applicable administration "
                    "fees paid by Part D plans, beneficiaries, subsidies, and third parties; "
                    "not Medicare payment, prescriber revenue, or estimated loss"
                )
            },
            "utilization_fields": {
                "Tot_Clms": "Dispensed original prescriptions and refills",
                "Tot_Benes": "Unique Part D beneficiaries when not suppressed",
            },
            "suppression": (
                "Provider-drug rows with 10 or fewer claims are omitted; beneficiary and subgroup "
                "values from 1 through 10 may be blank or counter-suppressed. Blank is not zero."
            ),
            "exclusions": [
                "Beneficiaries not enrolled in Medicare Part D",
                "Provider-drug combinations with 10 or fewer claims",
                f"Rows outside the targeted API filters: {filter_description}",
                "Manufacturer rebates from total drug cost",
            ],
        },
        "validation": {
            "rows": profile["rows"],
            "columns": profile["column_count"],
            "aggregation_key_duplicates": profile["aggregation_key_duplicates"],
            "notes": [
                "Every API page matched its inventory byte count and SHA-256 hash.",
                (
                    "All required columns were present, all NPIs contained ten digits, and "
                    "every returned row matched the requested filters."
                ),
                "This is a targeted extract and is not the complete annual provider-drug universe.",
            ],
        },
    }

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    load_and_validate_manifest(manifest_path)
    return manifest
