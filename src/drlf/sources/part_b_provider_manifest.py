from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from drlf.sources.manifest import load_and_validate_manifest

DATASET_NAME = "Medicare Physician & Other Practitioners - by Provider"
DATASET_SLUG = "medicare-physician-other-practitioners-by-provider"
DATASET_TYPE_UUID = "8889d81e-2ee7-448f-8713-f071038289b5"
LANDING_PAGE = (
    "https://data.cms.gov/provider-summary-by-type-of-service/"
    "medicare-physician-other-practitioners/"
    "medicare-physician-other-practitioners-by-provider"
)
METHODOLOGY_URL = (
    "https://data.cms.gov/resources/medicare-physician-other-practitioners-methodology"
)
DICTIONARY_URL = (
    "https://data.cms.gov/resources/"
    "medicare-physician-other-practitioners-by-provider-data-dictionary"
)

AGGREGATION_KEYS = ("Rndrng_NPI",)
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
        "Tot_HCPCS_Cds",
        "Tot_Benes",
        "Tot_Srvcs",
        "Tot_Sbmtd_Chrg",
        "Tot_Mdcr_Alowd_Amt",
        "Tot_Mdcr_Pymt_Amt",
        "Tot_Mdcr_Stdzd_Amt",
        "Drug_Sprsn_Ind",
        "Drug_Tot_HCPCS_Cds",
        "Drug_Tot_Benes",
        "Drug_Tot_Srvcs",
        "Drug_Sbmtd_Chrg",
        "Drug_Mdcr_Alowd_Amt",
        "Drug_Mdcr_Pymt_Amt",
        "Drug_Mdcr_Stdzd_Amt",
        "Med_Sprsn_Ind",
        "Med_Tot_HCPCS_Cds",
        "Med_Tot_Benes",
        "Med_Tot_Srvcs",
        "Med_Sbmtd_Chrg",
        "Med_Mdcr_Alowd_Amt",
        "Med_Mdcr_Pymt_Amt",
        "Med_Mdcr_Stdzd_Amt",
        "Bene_Avg_Age",
        "Bene_Age_LT_65_Cnt",
        "Bene_Age_65_74_Cnt",
        "Bene_Age_75_84_Cnt",
        "Bene_Age_GT_84_Cnt",
        "Bene_Feml_Cnt",
        "Bene_Male_Cnt",
        "Bene_Race_Wht_Cnt",
        "Bene_Race_Black_Cnt",
        "Bene_Race_API_Cnt",
        "Bene_Race_Hspnc_Cnt",
        "Bene_Race_NatInd_Cnt",
        "Bene_Race_Othr_Cnt",
        "Bene_Dual_Cnt",
        "Bene_Ndual_Cnt",
        "Bene_CC_BH_ADHD_OthCD_V1_Pct",
        "Bene_CC_BH_Alcohol_Drug_V1_Pct",
        "Bene_CC_BH_Tobacco_V1_Pct",
        "Bene_CC_BH_Alz_NonAlzdem_V2_Pct",
        "Bene_CC_BH_Anxiety_V1_Pct",
        "Bene_CC_BH_Bipolar_V1_Pct",
        "Bene_CC_BH_Mood_V2_Pct",
        "Bene_CC_BH_Depress_V1_Pct",
        "Bene_CC_BH_PD_V1_Pct",
        "Bene_CC_BH_PTSD_V1_Pct",
        "Bene_CC_BH_Schizo_OthPsy_V1_Pct",
        "Bene_CC_PH_Asthma_V2_Pct",
        "Bene_CC_PH_Afib_V2_Pct",
        "Bene_CC_PH_Cancer6_V2_Pct",
        "Bene_CC_PH_CKD_V2_Pct",
        "Bene_CC_PH_COPD_V2_Pct",
        "Bene_CC_PH_Diabetes_V2_Pct",
        "Bene_CC_PH_HF_NonIHD_V2_Pct",
        "Bene_CC_PH_Hyperlipidemia_V2_Pct",
        "Bene_CC_PH_Hypertension_V2_Pct",
        "Bene_CC_PH_IschemicHeart_V2_Pct",
        "Bene_CC_PH_Osteoporosis_V2_Pct",
        "Bene_CC_PH_Parkinson_V2_Pct",
        "Bene_CC_PH_Arthritis_V2_Pct",
        "Bene_CC_PH_Stroke_TIA_V2_Pct",
        "Bene_Avg_Risk_Scre",
    }
)

_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_NPI_PATTERN = re.compile(r"[0-9]{10}")


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
        raise ValueError("A targeted Provider inventory requires exact API filters")
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


def profile_provider_api_pages(inventory_path: Path) -> dict[str, Any]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if not isinstance(inventory, dict):
        raise ValueError("Inventory must be a JSON object")
    _validate_inventory(inventory)

    snapshot_dir = inventory_path.parent
    rows = 0
    columns: set[str] = set()
    keys: set[str] = set()
    duplicates = 0
    invalid_npis = 0
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
            if npi in keys:
                duplicates += 1
            keys.add(npi)
            for field, expected_value in expected_filters.items():
                if str(row.get(field, "")).casefold() != str(expected_value).casefold():
                    filter_mismatches += 1

    if rows != inventory.get("total_rows"):
        raise ValueError("Inventory total row count does not match its pages")
    if invalid_npis or duplicates or filter_mismatches or rows_missing_required_columns:
        raise ValueError(
            "Provider API page validation failed: "
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


def build_part_b_provider_api_manifest(
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
        raise ValueError("Provider data_year must be between 2013 and 2200")

    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if not isinstance(inventory, dict):
        raise ValueError("Inventory must be a JSON object")
    _validate_inventory(inventory)
    profile = profile_provider_api_pages(inventory_path)
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
                "Rendering providers with valid NPIs and Original Medicare fee-for-service Part B "
                "final-action physician/supplier non-institutional line items, excluding DMEPOS; "
                "targeted public provider summary rows"
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
                "Tot_Sbmtd_Chrg": "Total charges submitted for all provider services; not payment.",
                "Tot_Mdcr_Alowd_Amt": (
                    "Total allowed amount: Medicare payment plus beneficiary deductible and "
                    "coinsurance and any third-party responsibility; not provider net revenue."
                ),
                "Tot_Mdcr_Pymt_Amt": (
                    "Total Medicare fee-for-service line-item payment after deductible and "
                    "coinsurance; not proof that the rendering individual received or retained it."
                ),
                "Tot_Mdcr_Stdzd_Amt": (
                    "Total standardized Medicare payment with geographic payment differences "
                    "removed; not the actual paid amount."
                ),
                "Drug_Sbmtd_Chrg": "Submitted-charge subtotal for drug services; not payment.",
                "Drug_Mdcr_Alowd_Amt": (
                    "Medicare allowed-amount subtotal for drug services; not provider net revenue."
                ),
                "Drug_Mdcr_Pymt_Amt": (
                    "Medicare fee-for-service payment subtotal for drug services; not proof that "
                    "the rendering individual received or retained it."
                ),
                "Drug_Mdcr_Stdzd_Amt": (
                    "Standardized Medicare payment subtotal for drug services; not actual payment."
                ),
                "Med_Sbmtd_Chrg": (
                    "Submitted-charge subtotal for non-drug medical services; not payment."
                ),
                "Med_Mdcr_Alowd_Amt": (
                    "Medicare allowed-amount subtotal for non-drug medical services; not provider "
                    "net revenue."
                ),
                "Med_Mdcr_Pymt_Amt": (
                    "Medicare fee-for-service payment subtotal for non-drug medical services; not "
                    "proof that the rendering individual received or retained it."
                ),
                "Med_Mdcr_Stdzd_Amt": (
                    "Standardized Medicare payment subtotal for non-drug medical services; not "
                    "actual payment."
                ),
            },
            "utilization_fields": {
                "Rndrng_NPI": (
                    "Rendering-provider NPI on the claims; not necessarily the billing entity, "
                    "service-location owner, or payment recipient."
                ),
                "Tot_Benes": "Distinct Part B fee-for-service beneficiaries for the provider.",
                "Tot_Srvcs": (
                    "Total provider services; counting units vary by HCPCS and are not "
                    "uniform visits."
                ),
                "Bene_CC_PH_Asthma_V2_Pct": (
                    "Provider-level percentage meeting the CMS asthma chronic-condition algorithm; "
                    "values from 75% through 100% are top-coded at 75% and small counts may be "
                    "suppressed; not the indication, diagnosis, or symptoms for a particular test."
                ),
                "Bene_Avg_Risk_Scre": (
                    "Average CMS risk score for the provider's beneficiary panel; contextual case "
                    "mix rather than a service-specific medical-necessity measure."
                ),
            },
            "suppression": (
                "Provider summary totals are based on all qualifying Part B non-institutional "
                "lines, not only visible Provider-and-Service rows. Demographic and condition "
                "subgroups may be blank when suppressed. Drug and medical subtotals use '*' for "
                "fewer than 11 beneficiaries and '#' for counter-suppression. Blank or "
                "suppressed is not zero."
            ),
            "exclusions": [
                "Medicare Advantage beneficiaries",
                "Patients without Original Medicare Part B fee-for-service coverage",
                "Institutional Part A claims and DMEPOS claims",
                f"Rows outside the targeted API filters: {filter_description}",
                (
                    "Service-specific diagnoses, claim dates, modifiers, test results, and "
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
                    "All current required columns were present, NPIs were valid, and every row "
                    "matched the requested filters at the one-row-per-NPI grain."
                ),
                (
                    "Provider names and addresses are NPPES-derived assertions and do not "
                    "establish the service location, billing entity, or payment recipient."
                ),
                "This is a targeted extract and is not the complete annual provider universe.",
            ],
        },
    }

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    load_and_validate_manifest(manifest_path)
    return manifest
