from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from drlf.sources.manifest import load_and_validate_manifest

DATASET_NAME = "Medicare Durable Medical Equipment, Devices & Supplies - by Supplier and Service"
DATASET_SLUG = "medicare-durable-medical-equipment-devices-supplies-by-supplier-and-service"
DATASET_TYPE_UUID = "1746a83e-bb65-4300-8e02-21edbab77c6b"
LANDING_PAGE = (
    "https://data.cms.gov/provider-summary-by-type-of-service/"
    "medicare-durable-medical-equipment-devices-supplies/"
    "medicare-durable-medical-equipment-devices-supplies-by-supplier-and-service"
)
METHODOLOGY_URL = (
    "https://data.cms.gov/resources/medicare-durable-medical-equipment-devices-supplies-methodology"
)
DICTIONARY_URL = (
    "https://data.cms.gov/resources/"
    "medicare-durable-medical-equipment-devices-supplies-by-supplier-and-service-"
    "data-dictionary"
)

AGGREGATION_KEYS = ("Suplr_NPI", "HCPCS_Cd", "Suplr_Rentl_Ind")
REQUIRED_COLUMNS = (
    "Suplr_NPI",
    "Suplr_Prvdr_Last_Name_Org",
    "Suplr_Prvdr_First_Name",
    "Suplr_Prvdr_MI",
    "Suplr_Prvdr_Crdntls",
    "Suplr_Prvdr_Ent_Cd",
    "Suplr_Prvdr_St1",
    "Suplr_Prvdr_St2",
    "Suplr_Prvdr_City",
    "Suplr_Prvdr_State_Abrvtn",
    "Suplr_Prvdr_State_FIPS",
    "Suplr_Prvdr_Zip5",
    "Suplr_Prvdr_RUCA_Cat",
    "Suplr_Prvdr_RUCA",
    "Suplr_Prvdr_RUCA_Desc",
    "Suplr_Prvdr_Cntry",
    "Suplr_Prvdr_Spclty_Cd",
    "Suplr_Prvdr_Spclty_Desc",
    "Suplr_Prvdr_Spclty_Srce",
    "RBCS_Lvl",
    "RBCS_Id",
    "RBCS_Desc",
    "HCPCS_Cd",
    "HCPCS_Desc",
    "Suplr_Rentl_Ind",
    "Tot_Suplr_Benes",
    "Tot_Suplr_Clms",
    "Tot_Suplr_Srvcs",
    "Avg_Suplr_Sbmtd_Chrg",
    "Avg_Suplr_Mdcr_Alowd_Amt",
    "Avg_Suplr_Mdcr_Pymt_Amt",
    "Avg_Suplr_Mdcr_Stdzd_Amt",
)
SCHEMA_FINGERPRINT = sha256("\n".join(REQUIRED_COLUMNS).encode()).hexdigest()

MAX_INVENTORIES = 50
MAX_PAGE_BYTES = 4 * 1024 * 1024
MAX_RETAINED_BYTES = 16 * 1024 * 1024
MAX_PAGES = 5
MAX_ROWS = 5_000
MAX_BUNDLE_ROWS = 25_000
MAX_URL_LENGTH = 8_192

_NPI_PATTERN = re.compile(r"[0-9]{10}")
_HCPCS_PATTERN = re.compile(r"[A-Z0-9]{5}")


@dataclass(frozen=True)
class DmeposSupplierServiceRelease:
    data_year: int
    version_id: str
    modified_at: str
    total_rows: int
    full_csv_bytes: int


RELEASES = {
    2022: DmeposSupplierServiceRelease(
        2022,
        "dad703df-2e52-4ac6-a26f-51ecbf463d77",
        "2025-03-26",
        482_638,
        206_720_782,
    ),
    2023: DmeposSupplierServiceRelease(
        2023,
        "44235fd3-6cf9-487f-928d-12998cd84071",
        "2025-09-09",
        463_784,
        197_896_604,
    ),
    2024: DmeposSupplierServiceRelease(
        2024,
        "1dc6718a-e44b-403a-a735-ce01a6233822",
        "2026-07-23",
        440_670,
        188_599_281,
    ),
}


def _repository_relative_path(path: Path) -> str:
    try:
        relative_path = path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"Snapshot file is outside the repository: {path}") from error
    if not relative_path.startswith("data/raw/"):
        raise ValueError(f"Snapshot file must be retained under data/raw/: {path}")
    return relative_path


def _profile_path(path: Path) -> str:
    """Use repository-relative provenance when available without constraining pure profiling."""
    try:
        return _repository_relative_path(path)
    except ValueError:
        return path.resolve().as_posix()


def _page_path(snapshot_dir: Path, relative_filename: str) -> Path:
    path = (snapshot_dir / relative_filename).resolve()
    try:
        path.relative_to(snapshot_dir.resolve())
    except ValueError as error:
        raise ValueError(f"API page escapes its snapshot directory: {relative_filename}") from error
    return path


def _filter_values(filters: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(filters, dict) or not filters:
        raise ValueError("DMEPOS supplier-service inventory requires exact API filters")
    if set(filters) == {"condition"}:
        condition = filters["condition"]
        if not isinstance(condition, dict) or set(condition) != {"path", "operator", "values"}:
            raise ValueError("DMEPOS inventory has an invalid structured filter")
        if condition.get("operator") != "IN":
            raise ValueError("DMEPOS inventory structured filter must use exact IN")
        field = condition.get("path")
        values = condition.get("values")
        if not isinstance(field, str) or not field:
            raise ValueError("DMEPOS inventory IN-filter path must be nonempty")
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError("DMEPOS inventory IN-filter values must be strings")
        if not values or any(not value for value in values):
            raise ValueError("DMEPOS inventory IN-filter values must be nonempty")
        if values != sorted(set(values)):
            raise ValueError("DMEPOS inventory IN-filter values must be sorted and unique")
        return {field: tuple(values)}
    normalized: dict[str, tuple[str, ...]] = {}
    for field, raw_values in filters.items():
        if not isinstance(field, str) or not field:
            raise ValueError("DMEPOS inventory filter fields must be nonempty strings")
        if isinstance(raw_values, str):
            values = (raw_values,)
        elif isinstance(raw_values, list) and all(isinstance(value, str) for value in raw_values):
            values = tuple(raw_values)
        else:
            raise ValueError("DMEPOS inventory filters must contain strings or string lists")
        if not values or any(not value for value in values):
            raise ValueError("DMEPOS inventory filter values must be nonempty")
        if values != tuple(sorted(set(values))):
            raise ValueError("DMEPOS inventory multi-value filters must be sorted and unique")
        normalized[field] = values
    return normalized


def _validate_inventory(
    inventory: dict[str, Any], *, release: DmeposSupplierServiceRelease
) -> None:
    if inventory.get("dataset_uuid") != release.version_id:
        raise ValueError("DMEPOS inventory does not use the pinned annual version UUID")
    filters = _filter_values(inventory.get("filters"))
    if set(filters) - {"Suplr_NPI", "HCPCS_Cd"}:
        raise ValueError("DMEPOS inventory uses an unsupported filter field")
    structured_filter = set(inventory.get("filters", {})) == {"condition"}
    if structured_filter and inventory.get("sort_fields") != list(AGGREGATION_KEYS):
        raise ValueError("DMEPOS exact-IN inventory requires the stable source-grain sort")

    page_size = inventory.get("page_size")
    max_pages = inventory.get("max_pages")
    max_response_bytes = inventory.get("max_response_bytes")
    max_total_bytes = inventory.get("max_total_bytes")
    expected_rows = inventory.get("expected_rows")
    total_rows = inventory.get("total_rows")
    total_bytes = inventory.get("total_bytes")
    pages = inventory.get("pages")
    if not isinstance(page_size, int) or not 1 <= page_size <= 5_000:
        raise ValueError("DMEPOS inventory page_size must be between 1 and 5000")
    if not isinstance(max_pages, int) or not 1 <= max_pages <= MAX_PAGES:
        raise ValueError(f"DMEPOS inventory max_pages must be between 1 and {MAX_PAGES}")
    if not isinstance(max_response_bytes, int) or not 1 <= max_response_bytes <= MAX_PAGE_BYTES:
        raise ValueError("DMEPOS inventory per-response byte ceiling is invalid")
    if not isinstance(max_total_bytes, int) or not 1 <= max_total_bytes <= MAX_RETAINED_BYTES:
        raise ValueError("DMEPOS inventory retained-byte ceiling is invalid")
    if max_response_bytes > max_total_bytes:
        raise ValueError("DMEPOS inventory response cap exceeds its total-byte cap")
    if not isinstance(expected_rows, int) or not 0 <= expected_rows <= MAX_ROWS:
        raise ValueError("DMEPOS inventory expected row count is outside the safety envelope")
    if total_rows != expected_rows:
        raise ValueError("DMEPOS inventory retained rows differ from the stats preflight")
    if not isinstance(total_bytes, int) or not 0 <= total_bytes <= max_total_bytes:
        raise ValueError("DMEPOS inventory total bytes are invalid")
    if not isinstance(pages, list) or not pages or len(pages) > max_pages:
        raise ValueError("DMEPOS inventory has an invalid retained page list")

    expected_path = f"/data-api/v1/dataset/{release.version_id}/data"
    seen_filenames: set[str] = set()
    page_rows = 0
    page_bytes = 0
    for ordinal, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            raise ValueError("DMEPOS inventory page records must be objects")
        relative_filename = page.get("relative_filename")
        if not isinstance(relative_filename, str) or not relative_filename:
            raise ValueError("DMEPOS inventory page is missing relative_filename")
        if relative_filename in seen_filenames:
            raise ValueError("DMEPOS inventory repeats a page filename")
        seen_filenames.add(relative_filename)
        if page.get("ordinal") != ordinal or page.get("offset") != (ordinal - 1) * page_size:
            raise ValueError("DMEPOS inventory page sequence is inconsistent")
        rows = page.get("rows")
        byte_count = page.get("bytes")
        if not isinstance(rows, int) or rows < 0:
            raise ValueError("DMEPOS inventory page has an invalid row count")
        if not isinstance(byte_count, int) or not 0 <= byte_count <= max_response_bytes:
            raise ValueError("DMEPOS inventory page has an invalid byte count")
        url = page.get("url")
        if not isinstance(url, str) or len(url.encode("utf-8")) > MAX_URL_LENGTH:
            raise ValueError("DMEPOS inventory page URL is invalid or too long")
        parsed_url = urlparse(url)
        if parsed_url.scheme != "https" or parsed_url.netloc != "data.cms.gov":
            raise ValueError("DMEPOS inventory page URL must use data.cms.gov HTTPS")
        if parsed_url.path != expected_path:
            raise ValueError("DMEPOS inventory page URL does not use the pinned annual UUID")
        query = parse_qs(parsed_url.query)
        expected_query = {
            "size": [str(page_size)],
            "offset": [str((ordinal - 1) * page_size)],
        }
        if structured_filter:
            field, values = next(iter(filters.items()))
            expected_query.update(
                {
                    "sort": [",".join(AGGREGATION_KEYS)],
                    "filter[condition][path]": [field],
                    "filter[condition][operator]": ["IN"],
                    "filter[condition][value][]": list(values),
                }
            )
        else:
            expected_query.update(
                {f"filter[{field}]": list(values) for field, values in sorted(filters.items())}
            )
        if query != expected_query:
            raise ValueError(
                "DMEPOS inventory page URL does not reproduce its recorded filter, sort, "
                "page size, and offset"
            )
        page_rows += rows
        page_bytes += byte_count
    if page_rows != total_rows or page_bytes != total_bytes:
        raise ValueError("DMEPOS inventory totals do not reproduce from retained pages")


def _required_nonnegative_decimal(row: dict[str, Any], field: str) -> Decimal:
    value = row.get(field)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"DMEPOS supplier-service row has invalid {field}") from None
    if not number.is_finite() or number < 0:
        raise ValueError(f"DMEPOS supplier-service row has invalid {field}")
    return number


def _validate_row(
    row: dict[str, Any],
    *,
    filters: dict[str, tuple[str, ...]],
    row_number: int,
) -> tuple[str, str, str, bool]:
    if tuple(row) != REQUIRED_COLUMNS:
        raise ValueError(
            f"DMEPOS supplier-service row {row_number} has an unexpected ordered schema"
        )
    npi = str(row["Suplr_NPI"])
    hcpcs = str(row["HCPCS_Cd"]).upper()
    rental = str(row["Suplr_Rentl_Ind"])
    if _NPI_PATTERN.fullmatch(npi) is None:
        raise ValueError(f"DMEPOS supplier-service row {row_number} has an invalid NPI")
    if _HCPCS_PATTERN.fullmatch(hcpcs) is None:
        raise ValueError(f"DMEPOS supplier-service row {row_number} has an invalid HCPCS code")
    if rental not in {"Y", "N"}:
        raise ValueError(f"DMEPOS supplier-service row {row_number} has an invalid rental flag")
    if row.get("Suplr_Prvdr_Ent_Cd") not in {"I", "O"}:
        raise ValueError(f"DMEPOS supplier-service row {row_number} has an invalid entity code")
    for field in ("Suplr_Prvdr_Spclty_Desc", "Suplr_Prvdr_Spclty_Srce", "HCPCS_Desc"):
        if not isinstance(row.get(field), str) or not str(row[field]).strip():
            raise ValueError(f"DMEPOS supplier-service row {row_number} requires {field}")
    for field in ("Tot_Suplr_Clms",):
        value = str(row.get(field, ""))
        if not value.isdigit() or int(value) < 0:
            raise ValueError(f"DMEPOS supplier-service row {row_number} has invalid {field}")
    for field in (
        "Tot_Suplr_Srvcs",
        "Avg_Suplr_Sbmtd_Chrg",
        "Avg_Suplr_Mdcr_Alowd_Amt",
        "Avg_Suplr_Mdcr_Pymt_Amt",
        "Avg_Suplr_Mdcr_Stdzd_Amt",
    ):
        _required_nonnegative_decimal(row, field)
    beneficiary_value = row.get("Tot_Suplr_Benes")
    beneficiary_suppressed = beneficiary_value is None or str(beneficiary_value).strip() in {
        "",
        "*",
        "#",
    }
    if not beneficiary_suppressed:
        value = str(beneficiary_value)
        if not value.isdigit() or int(value) < 0:
            raise ValueError(
                f"DMEPOS supplier-service row {row_number} has invalid Tot_Suplr_Benes"
            )
    for field, expected_values in filters.items():
        actual = npi if field == "Suplr_NPI" else hcpcs
        if actual not in {
            value.upper() if field == "HCPCS_Cd" else value for value in expected_values
        }:
            raise ValueError(f"DMEPOS supplier-service row {row_number} violates its API filter")
    return npi, hcpcs, rental, beneficiary_suppressed


def profile_dmepos_supplier_service_api_pages(
    inventory_paths: list[Path],
    *,
    data_year: int,
) -> dict[str, Any]:
    release = RELEASES.get(data_year)
    if release is None:
        raise ValueError("DMEPOS supplier-service data_year has no reviewed pinned release")
    if not inventory_paths or len(inventory_paths) > MAX_INVENTORIES:
        raise ValueError(f"DMEPOS supplier-service requires 1 to {MAX_INVENTORIES} inventories")
    resolved_paths = [path.resolve() for path in inventory_paths]
    if len(resolved_paths) != len(set(resolved_paths)):
        raise ValueError("DMEPOS supplier-service repeats an inventory path")

    inventories: list[tuple[Path, dict[str, Any]]] = []
    declared_total_bytes = 0
    for inventory_path in sorted(resolved_paths, key=lambda path: path.as_posix()):
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        if not isinstance(inventory, dict):
            raise ValueError("DMEPOS inventory must be a JSON object")
        _validate_inventory(inventory, release=release)
        declared_total_bytes += inventory["total_bytes"]
        inventories.append((inventory_path, inventory))
    if declared_total_bytes > MAX_RETAINED_BYTES:
        raise ValueError("DMEPOS supplier-service bundle exceeds its retained-byte ceiling")

    observed_at: str | None = None
    grains: set[tuple[str, str, str]] = set()
    all_filter_values: dict[str, set[str]] = {}
    total_rows = 0
    total_bytes = 0
    suppressed_beneficiary_rows = 0
    request_profiles: list[dict[str, Any]] = []
    acquisition_settings: dict[str, int] | None = None
    for inventory_path, inventory in inventories:
        inventory_observed_at = inventory.get("observed_at")
        if not isinstance(inventory_observed_at, str) or not inventory_observed_at:
            raise ValueError("DMEPOS inventory requires an observed_at timestamp")
        if observed_at is None:
            observed_at = inventory_observed_at
        elif observed_at != inventory_observed_at:
            raise ValueError("DMEPOS bundled inventories must share one observation timestamp")
        filters = _filter_values(inventory["filters"])
        for field, values in filters.items():
            all_filter_values.setdefault(field, set()).update(values)

        inventory_settings = {
            "page_size": inventory["page_size"],
            "max_pages": inventory["max_pages"],
            "max_response_bytes": inventory["max_response_bytes"],
            "max_total_bytes": inventory["max_total_bytes"],
        }
        if acquisition_settings is None:
            acquisition_settings = inventory_settings
        elif acquisition_settings != inventory_settings:
            raise ValueError(
                "DMEPOS bundled inventories must use identical acquisition safety settings"
            )

        snapshot_dir = inventory_path.parent
        inventory_rows = 0
        for page in inventory["pages"]:
            page_path = _page_path(snapshot_dir, page["relative_filename"])
            body = page_path.read_bytes()
            if len(body) != page["bytes"] or sha256(body).hexdigest() != page["sha256"]:
                raise ValueError(f"DMEPOS API page inventory integrity failed: {page_path}")
            page_rows = json.loads(body)
            if not isinstance(page_rows, list):
                raise ValueError(f"DMEPOS API page is not a row array: {page_path}")
            if len(page_rows) != page["rows"]:
                raise ValueError(f"DMEPOS API page row count differs from inventory: {page_path}")
            for row in page_rows:
                if not isinstance(row, dict):
                    raise ValueError(f"DMEPOS API page contains a non-object row: {page_path}")
                total_rows += 1
                inventory_rows += 1
                if total_rows > MAX_BUNDLE_ROWS:
                    raise ValueError("DMEPOS supplier-service bundle exceeds its row ceiling")
                npi, hcpcs, rental, suppressed = _validate_row(
                    row,
                    filters=filters,
                    row_number=total_rows,
                )
                grain = (npi, hcpcs, rental)
                if grain in grains:
                    raise ValueError("DMEPOS supplier-service bundle repeats a published grain")
                grains.add(grain)
                suppressed_beneficiary_rows += suppressed
        if inventory_rows != inventory["total_rows"]:
            raise ValueError("DMEPOS inventory row count does not reproduce from its pages")
        total_bytes += inventory["total_bytes"]
        request_profiles.append(
            {
                "inventory_path": _profile_path(inventory_path),
                "filters": {
                    field: list(values) if len(values) > 1 else values[0]
                    for field, values in sorted(filters.items())
                },
                "expected_rows": inventory["expected_rows"],
                "retained_rows": inventory["total_rows"],
                "retained_bytes": inventory["total_bytes"],
                "sort_fields": inventory.get("sort_fields", []),
                **inventory_settings,
            }
        )
    if not total_rows:
        raise ValueError("DMEPOS supplier-service bundle contains no data rows")
    if total_bytes > MAX_RETAINED_BYTES:
        raise ValueError("DMEPOS supplier-service bundle exceeds its retained-byte ceiling")
    return {
        "rows": total_rows,
        "columns": len(REQUIRED_COLUMNS),
        "aggregation_key_duplicates": 0,
        "schema_fingerprint": SCHEMA_FINGERPRINT,
        "beneficiary_suppressed_rows": suppressed_beneficiary_rows,
        "observed_at": observed_at,
        "filters": {field: sorted(values) for field, values in sorted(all_filter_values.items())},
        "requests": request_profiles,
        "acquisition_settings": acquisition_settings,
        "total_bytes": total_bytes,
    }


def build_dmepos_supplier_service_api_manifest(
    inventory_paths: list[Path],
    manifest_path: Path,
    *,
    data_year: int,
    documentation_snapshots: list[str],
) -> dict[str, Any]:
    if manifest_path.exists():
        raise FileExistsError(f"Refusing to replace manifest: {manifest_path}")
    release = RELEASES.get(data_year)
    if release is None:
        raise ValueError("DMEPOS supplier-service data_year has no reviewed pinned release")
    profile = profile_dmepos_supplier_service_api_pages(
        inventory_paths,
        data_year=data_year,
    )
    acquisition_settings = profile["acquisition_settings"]
    if not isinstance(acquisition_settings, dict):
        raise ValueError("DMEPOS supplier-service profile is missing acquisition settings")
    sorted_inventory_paths = sorted(
        (path.resolve() for path in inventory_paths), key=lambda path: path.as_posix()
    )
    file_records: list[dict[str, Any]] = []
    first_request_url: str | None = None
    for inventory_path in sorted_inventory_paths:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        snapshot_dir = inventory_path.parent
        for page in inventory["pages"]:
            page_path = _page_path(snapshot_dir, page["relative_filename"])
            first_request_url = first_request_url or page["url"]
            file_records.append(
                {
                    "relative_path": _repository_relative_path(page_path),
                    "filename": page_path.name,
                    "role": "api-page",
                    "bytes": page["bytes"],
                    "sha256": page["sha256"],
                    "media_type": page["media_type"],
                    "compression": None,
                    "schema_fingerprint": SCHEMA_FINGERPRINT,
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
            "version_id": release.version_id,
            "data_year": data_year,
            "population": (
                "Targeted visible supplier-service cells from 100% final-action Original "
                "Medicare fee-for-service Part B non-institutional DMEPOS claim lines"
            ),
            "aggregation_keys": list(AGGREGATION_KEYS),
        },
        "observation": {
            "published_at": None,
            "modified_at": release.modified_at,
            "observed_at": profile["observed_at"],
            "accessed_at": str(profile["observed_at"])[:10],
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
            "request_url": first_request_url,
            "parameters": {
                "filters": profile["filters"],
                "requests": profile["requests"],
                "page_size": acquisition_settings["page_size"],
                "max_pages": acquisition_settings["max_pages"],
                "max_response_bytes": acquisition_settings["max_response_bytes"],
                "max_total_bytes": acquisition_settings["max_total_bytes"],
                "bundle_max_total_bytes": MAX_RETAINED_BYTES,
                "bundle_max_rows": MAX_BUNDLE_ROWS,
                "expected_rows": profile["rows"],
                "total_bytes": profile["total_bytes"],
                "pagination": "offset",
                "aggregation_order": list(AGGREGATION_KEYS),
            },
            "query": None,
        },
        "files": file_records,
        "semantics": {
            "monetary_fields": {
                "Avg_Suplr_Sbmtd_Chrg": (
                    "Average submitted charge per HCPCS-defined service unit; not payment."
                ),
                "Avg_Suplr_Mdcr_Alowd_Amt": (
                    "Average Medicare allowed amount per unit, including Medicare payment "
                    "and beneficiary or third-party responsibility."
                ),
                "Avg_Suplr_Mdcr_Pymt_Amt": (
                    "Average Medicare fee-for-service payment per unit after deductible and "
                    "coinsurance; not supplier income or an improper-payment estimate."
                ),
                "Avg_Suplr_Mdcr_Stdzd_Amt": (
                    "Average standardized Medicare payment per unit with geographic payment "
                    "differences removed; not actual payment."
                ),
            },
            "utilization_fields": {
                "Suplr_NPI": (
                    "Supplier NPI reported on the claim; not necessarily the referring provider, "
                    "corporate parent, furnishing location, remittance account, or ultimate payee."
                ),
                "Tot_Suplr_Benes": (
                    "Distinct beneficiaries at the published cell. Blank is suppressed or "
                    "unavailable, not zero; counts cannot be summed across cells."
                ),
                "Tot_Suplr_Clms": (
                    "Claims at the published cell; claims can overlap across HCPCS cells."
                ),
                "Tot_Suplr_Srvcs": (
                    "HCPCS-defined product or service units; unit meaning varies by code and "
                    "must not be treated as visits."
                ),
                "Suplr_Rentl_Ind": (
                    "Indicator based on an RR modifier in either of the first two claim-line "
                    "modifier positions."
                ),
            },
            "suppression": (
                "CMS suppresses some detail beneficiary counts and omits low-volume detail "
                "cells. The methodology and dictionary are not fully consistent on the exact "
                "published-row suppression description. Supplier-summary totals are built from "
                "all qualifying claims and therefore need not reconcile to visible detail rows."
            ),
            "exclusions": [
                "Medicare Advantage beneficiaries",
                "Institutional Part A claims",
                "Public detail cells omitted by CMS suppression",
                "Rows outside the exact API filters recorded in retrieval parameters",
                (
                    "Referring-provider NPIs, diagnoses, modifiers beyond the rental indicator, "
                    "claim dates, billing TINs, medical records, shipping records, and "
                    "remittance accounts"
                ),
            ],
        },
        "validation": {
            "rows": profile["rows"],
            "columns": profile["columns"],
            "aggregation_key_duplicates": 0,
            "notes": [
                "Every retained API page matched its inventory byte count and SHA-256 hash.",
                (
                    "Every nonempty row matched the exact 32-column ordered source schema and "
                    "its recorded filter."
                ),
                (
                    "Blank or unavailable beneficiary counts were preserved in "
                    f"{profile['beneficiary_suppressed_rows']} rows."
                ),
                (
                    "This is a targeted union of complete filtered API responses, not the "
                    "complete annual supplier-service universe."
                ),
                (
                    "The version resources endpoints currently point 2022-2024 releases to "
                    "common RY25 documentation; this documentation lag is preserved rather "
                    "than silently resolved."
                ),
            ],
        },
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    load_and_validate_manifest(manifest_path)
    return manifest
