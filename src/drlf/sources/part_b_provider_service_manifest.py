from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from drlf.sources.manifest import load_and_validate_manifest

DATASET_NAME = "Medicare Physician & Other Practitioners - by Provider and Service"
DATASET_SLUG = "medicare-physician-other-practitioners-by-provider-and-service"
DATASET_TYPE_UUID = "92396110-2aed-4d63-a6a2-5d6207d46a29"
LANDING_PAGE = (
    "https://data.cms.gov/provider-summary-by-type-of-service/"
    "medicare-physician-other-practitioners/"
    "medicare-physician-other-practitioners-by-provider-and-service"
)
METHODOLOGY_URL = (
    "https://data.cms.gov/resources/medicare-physician-other-practitioners-methodology"
)
DICTIONARY_URL = (
    "https://data.cms.gov/resources/"
    "medicare-physician-other-practitioners-by-provider-and-service-data-dictionary"
)

AGGREGATION_KEYS = ("Rndrng_NPI", "HCPCS_Cd", "Place_Of_Srvc")
REQUIRED_COLUMNS = frozenset(
    {
        "Rndrng_NPI",
        "Rndrng_Prvdr_Last_Org_Name",
        "Rndrng_Prvdr_First_Name",
        "Rndrng_Prvdr_MI",
        "Rndrng_Prvdr_Crdntls",
        "Rndrng_Prvdr_Ent_Cd",
        "Rndrng_Prvdr_St1",
        "Rndrng_Prvdr_St2",
        "Rndrng_Prvdr_City",
        "Rndrng_Prvdr_State_Abrvtn",
        "Rndrng_Prvdr_State_FIPS",
        "Rndrng_Prvdr_Zip5",
        "Rndrng_Prvdr_RUCA",
        "Rndrng_Prvdr_RUCA_Desc",
        "Rndrng_Prvdr_Cntry",
        "Rndrng_Prvdr_Type",
        "Rndrng_Prvdr_Mdcr_Prtcptg_Ind",
        "HCPCS_Cd",
        "HCPCS_Desc",
        "HCPCS_Drug_Ind",
        "Place_Of_Srvc",
        "Tot_Benes",
        "Tot_Srvcs",
        "Tot_Bene_Day_Srvcs",
        "Avg_Sbmtd_Chrg",
        "Avg_Mdcr_Alowd_Amt",
        "Avg_Mdcr_Pymt_Amt",
        "Avg_Mdcr_Stdzd_Amt",
    }
)

_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_NPI_PATTERN = re.compile(r"[0-9]{10}")
_HCPCS_PATTERN = re.compile(r"[A-Z0-9]{5}", re.IGNORECASE)


def _repository_relative_path(path: Path) -> str:
    try:
        relative_path = path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"Snapshot file is outside the repository: {path}") from error
    if not relative_path.startswith("data/raw/"):
        raise ValueError(f"Snapshot file must be retained under data/raw/: {path}")
    return relative_path


def _page_path(snapshot_dir: Path, relative_filename: str) -> Path:
    path = (snapshot_dir / relative_filename).resolve()
    try:
        path.relative_to(snapshot_dir.resolve())
    except ValueError as error:
        raise ValueError(f"API page escapes its snapshot directory: {relative_filename}") from error
    return path


def _validate_inventory(inventory: dict[str, Any]) -> None:
    dataset_uuid = inventory.get("dataset_uuid")
    if not isinstance(dataset_uuid, str) or not _UUID_PATTERN.fullmatch(dataset_uuid):
        raise ValueError("Inventory requires a pinned, version-specific CMS dataset UUID")
    if dataset_uuid.casefold() == DATASET_TYPE_UUID.casefold():
        raise ValueError(
            "Inventory uses the dataset type UUID; a pinned version-specific UUID is required"
        )

    filters = inventory.get("filters")
    if not isinstance(filters, dict) or not filters:
        raise ValueError("A targeted Provider-and-Service inventory requires exact API filters")
    if any(
        not isinstance(key, str) or not key or str(value) == "" for key, value in filters.items()
    ):
        raise ValueError("Inventory filters must use non-empty field names and values")

    page_size = inventory.get("page_size")
    max_pages = inventory.get("max_pages")
    pages = inventory.get("pages")
    if not isinstance(page_size, int) or not 1 <= page_size <= 5_000:
        raise ValueError("Inventory page_size must be between 1 and 5000")
    if not isinstance(max_pages, int) or max_pages < 1:
        raise ValueError("Inventory max_pages must be a positive integer")
    if not isinstance(pages, list) or not pages:
        raise ValueError("Inventory does not contain any retained API pages")
    if len(pages) > max_pages:
        raise ValueError("Inventory contains more pages than its declared max_pages")

    expected_path = f"/data-api/v1/dataset/{dataset_uuid}/data"
    seen_filenames: set[str] = set()
    for ordinal, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            raise ValueError("Inventory page records must be objects")
        relative_filename = page.get("relative_filename")
        if not isinstance(relative_filename, str) or not relative_filename:
            raise ValueError("Inventory page is missing relative_filename")
        if relative_filename in seen_filenames:
            raise ValueError(f"Inventory repeats page filename: {relative_filename}")
        seen_filenames.add(relative_filename)
        if page.get("ordinal") != ordinal:
            raise ValueError("Inventory page ordinals must be consecutive and one-based")
        if page.get("offset") != (ordinal - 1) * page_size:
            raise ValueError("Inventory page offsets do not match its declared page size")
        url = page.get("url")
        if not isinstance(url, str) or urlparse(url).path != expected_path:
            raise ValueError("Inventory page URL does not use the pinned version-specific UUID")


def profile_provider_service_api_pages(inventory_path: Path) -> dict[str, Any]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if not isinstance(inventory, dict):
        raise ValueError("Inventory must be a JSON object")
    _validate_inventory(inventory)

    snapshot_dir = inventory_path.parent
    rows = 0
    columns: set[str] = set()
    keys: set[tuple[str, str, str]] = set()
    duplicates = 0
    invalid_npis = 0
    invalid_hcpcs_codes = 0
    invalid_places_of_service = 0
    filter_mismatches = 0
    rows_missing_required_columns = 0
    expected_filters = inventory["filters"]

    for page in inventory["pages"]:
        page_path = _page_path(snapshot_dir, page["relative_filename"])
        body = page_path.read_bytes()
        if len(body) != page.get("bytes") or sha256(body).hexdigest() != page.get("sha256"):
            raise ValueError(f"Page inventory integrity failed: {page_path}")
        page_rows = json.loads(body)
        if not isinstance(page_rows, list):
            raise ValueError(f"API page is not a row array: {page_path}")
        if len(page_rows) != page.get("rows"):
            raise ValueError(f"Page row count differs from inventory: {page_path}")
        for row in page_rows:
            if not isinstance(row, dict):
                raise ValueError(f"API page contains a non-object row: {page_path}")
            rows += 1
            columns.update(row)
            if not REQUIRED_COLUMNS.issubset(row):
                rows_missing_required_columns += 1

            npi = str(row.get("Rndrng_NPI", ""))
            if not _NPI_PATTERN.fullmatch(npi):
                invalid_npis += 1
            hcpcs_code = str(row.get("HCPCS_Cd", ""))
            if not _HCPCS_PATTERN.fullmatch(hcpcs_code):
                invalid_hcpcs_codes += 1
            if row.get("Place_Of_Srvc") not in {"F", "O"}:
                invalid_places_of_service += 1

            key = tuple(str(row.get(field, "")) for field in AGGREGATION_KEYS)
            if key in keys:
                duplicates += 1
            keys.add(key)
            for field, expected_value in expected_filters.items():
                if str(row.get(field, "")).casefold() != str(expected_value).casefold():
                    filter_mismatches += 1

    if rows != inventory.get("total_rows"):
        raise ValueError("Inventory total row count does not match its pages")
    if (
        invalid_npis
        or invalid_hcpcs_codes
        or invalid_places_of_service
        or duplicates
        or filter_mismatches
        or rows_missing_required_columns
    ):
        raise ValueError(
            "Provider-and-Service API page validation failed: "
            f"invalid_npis={invalid_npis}, invalid_hcpcs_codes={invalid_hcpcs_codes}, "
            f"invalid_places_of_service={invalid_places_of_service}, duplicates={duplicates}, "
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


def build_part_b_provider_service_api_manifest(
    inventory_path: Path,
    manifest_path: Path,
    *,
    data_year: int,
    modified_at: str | None,
    documentation_snapshots: list[str],
) -> dict[str, Any]:
    if manifest_path.exists():
        raise FileExistsError(f"Refusing to replace manifest: {manifest_path}")
    if not 2013 <= data_year <= 2200:
        raise ValueError("Provider-and-Service data_year must be between 2013 and 2200")

    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if not isinstance(inventory, dict):
        raise ValueError("Inventory must be a JSON object")
    _validate_inventory(inventory)
    profile = profile_provider_service_api_pages(inventory_path)
    snapshot_dir = inventory_path.parent
    filters = inventory["filters"]
    filter_description = ", ".join(f"{key}={value}" for key, value in sorted(filters.items()))

    file_records: list[dict[str, Any]] = []
    for page in inventory["pages"]:
        page_path = _page_path(snapshot_dir, page["relative_filename"])
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
                "Original Medicare fee-for-service Part B beneficiaries represented in "
                "final-action physician/supplier non-institutional line items, excluding DMEPOS; "
                "targeted public detail rows for rendering providers with valid NPIs"
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
            "license_name": "CMS AMA CPT end-user agreement (CPT material only)",
            "license_url": "https://www.cms.gov/license/ama",
            "access_restrictions": (
                "Publicly accessible CMS data; CPT codes and descriptions are AMA material. "
                "Review the CMS AMA agreement for permitted use; public access does not grant "
                "unrestricted redistribution or an Apache-2.0 license to that material."
            ),
            "public_use_verified": True,
        },
        "retrieval": {
            "method": "api",
            "request_url": inventory["pages"][0]["url"],
            "parameters": {
                "filters": filters,
                "page_size": inventory["page_size"],
                "max_pages": inventory["max_pages"],
                "max_response_bytes": inventory.get("max_response_bytes"),
                "max_total_bytes": inventory.get("max_total_bytes"),
                "expected_rows": inventory.get("expected_rows"),
                "total_bytes": inventory.get("total_bytes"),
                "pagination": "offset",
            },
            "query": None,
        },
        "files": file_records,
        "semantics": {
            "monetary_fields": {
                "Avg_Sbmtd_Chrg": (
                    "Average charge submitted for the line-item service; not payment."
                ),
                "Avg_Mdcr_Alowd_Amt": (
                    "Average Medicare allowed amount: Medicare payment plus beneficiary deductible "
                    "and coinsurance and any third-party responsibility; not provider net revenue."
                ),
                "Avg_Mdcr_Pymt_Amt": (
                    "Average Medicare fee-for-service line-item payment after deductible and "
                    "coinsurance; not proof that the rendering individual received or retained it."
                ),
                "Avg_Mdcr_Stdzd_Amt": (
                    "Average standardized Medicare payment with geographic payment differences "
                    "removed; not the actual paid amount."
                ),
            },
            "utilization_fields": {
                "Rndrng_NPI": (
                    "Rendering-provider NPI on the claim; not necessarily the billing entity, "
                    "service-location owner, or payment recipient."
                ),
                "Tot_Benes": (
                    "Distinct beneficiaries for the rendering NPI, HCPCS code, and "
                    "place of service."
                ),
                "Tot_Srvcs": (
                    "Number of services; the counting unit varies by HCPCS code and must not be "
                    "treated as a uniform encounter count."
                ),
                "Tot_Bene_Day_Srvcs": (
                    "Distinct beneficiary-per-day services, reducing same-day unit double-counting."
                ),
                "Place_Of_Srvc": (
                    "CMS facility (F) or non-facility (O) grouping; not an exact service address."
                ),
            },
            "suppression": (
                "Rows at rendering-NPI, HCPCS, and place-of-service grain derived from 10 or fewer "
                "distinct beneficiaries are excluded. An absent row means 0 through 10 or "
                "otherwise not present in the public data; it is not an observed zero."
            ),
            "exclusions": [
                "Medicare Advantage beneficiaries",
                "Patients without Original Medicare Part B fee-for-service coverage",
                "Institutional Part A claims and DMEPOS claims",
                "Provider-service-place rows derived from 10 or fewer beneficiaries",
                f"Rows outside the targeted API filters: {filter_description}",
                (
                    "Diagnoses, claim dates, modifiers, medical records, test results, and "
                    "billing TINs"
                ),
            ],
        },
        "validation": {
            "rows": profile["rows"],
            "columns": profile["column_count"],
            "aggregation_key_duplicates": profile["aggregation_key_duplicates"],
            "notes": [
                "Every API page matched its inventory byte count and SHA-256 hash.",
                (
                    "All current required columns were present, NPIs and HCPCS codes were valid, "
                    "places of service were F or O, and every row matched the requested filters."
                ),
                (
                    "Provider names and addresses are NPPES-derived assertions and do not "
                    "establish the service location, billing entity, or payment recipient."
                ),
                (
                    "This is a targeted extract and is not the complete annual "
                    "provider-service universe."
                ),
            ],
        },
    }

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    load_and_validate_manifest(manifest_path)
    return manifest
