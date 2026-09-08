from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from drlf.sources.cms_api import ExactInFilter, build_data_url
from drlf.sources.manifest import load_and_validate_manifest

MAX_NPIS = 100
MAX_ROWS = 1_000
MAX_PAGES = 1
MAX_PAGE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024

_NPI_PATTERN = re.compile(r"[0-9]{10}\Z")
_ENROLLMENT_ID_PATTERN = re.compile(r"[A-Za-z0-9]{15}\Z")

ENROLLMENT_COLUMNS = (
    "NPI",
    "MULTIPLE_NPI_FLAG",
    "PECOS_ASCT_CNTL_ID",
    "ENRLMT_ID",
    "PROVIDER_TYPE_CD",
    "PROVIDER_TYPE_DESC",
    "STATE_CD",
    "FIRST_NAME",
    "MDL_NAME",
    "LAST_NAME",
    "ORG_NAME",
)
REVOCATION_COLUMNS = (
    "ENRLMT_ID",
    "NPI",
    "FIRST_NAME",
    "MDL_NAME",
    "LAST_NAME",
    "ORG_NAME",
    "MULTIPLE_NPI_FLAG",
    "STATE_CD",
    "PROVIDER_TYPE_DESC",
    "REVOCATION_RSN",
    "REVOCATION_EFCTV_DT",
    "REENROLLMENT_BAR_EXPRTN_DT",
)


@dataclass(frozen=True)
class CmsProviderRecordsRelease:
    key: str
    dataset_name: str
    dataset_slug: str
    dataset_type_id: str
    version_id: str
    release_label: str
    modified_at: date
    landing_page: str
    methodology_url: str
    data_dictionary_url: str
    expected_columns: tuple[str, ...]
    population: str
    exclusions: tuple[str, ...]

    @property
    def schema_fingerprint(self) -> str:
        return sha256("\n".join(self.expected_columns).encode()).hexdigest()


RELEASES = {
    "enrollment": CmsProviderRecordsRelease(
        key="enrollment",
        dataset_name="Medicare Fee-For-Service Public Provider Enrollment",
        dataset_slug="medicare-fee-for-service-public-provider-enrollment",
        dataset_type_id="2457ea29-fc82-48b0-86ec-3b0755de7515",
        version_id="903dcfa5-4924-4775-8676-4fa0f6a8aee9",
        release_label="Q3 2026",
        modified_at=date(2026, 7, 27),
        landing_page=(
            "https://data.cms.gov/provider-characteristics/"
            "medicare-provider-supplier-enrollment/"
            "medicare-fee-for-service-public-provider-enrollment"
        ),
        methodology_url=("https://data.cms.gov/sites/default/files/2026-07/PPEF_Data_Guidance.pdf"),
        data_dictionary_url=(
            "https://data.cms.gov/sites/default/files/2026-07/PPEF_Data_Dictionary.pdf"
        ),
        expected_columns=ENROLLMENT_COLUMNS,
        population=(
            "Provider enrollment applications approved to bill, or approved to order and "
            "refer, in Medicare as of the PECOS version used for this point-in-time release"
        ),
        exclusions=(
            "Enrollments outside the exact requested NPI set",
            "Historical enrollments that were not active in the point-in-time PPEF release",
            (
                "A small number of enrollments excluded by CMS for documented PECOS "
                "data-quality issues"
            ),
            "PECOS fields that CMS collects but does not publish in the PPEF API enrollment file",
        ),
    ),
    "revoked": CmsProviderRecordsRelease(
        key="revoked",
        dataset_name="Revoked Medicare Providers and Suppliers",
        dataset_slug="revoked-medicare-providers-and-suppliers",
        dataset_type_id="a6496a7d-4e19-479a-a9ad-d4c0a49e07c3",
        version_id="4a5e8b27-6787-477b-bba0-c2292c850e83",
        release_label="Q2 2026",
        modified_at=date(2026, 8, 4),
        landing_page=(
            "https://data.cms.gov/provider-characteristics/"
            "medicare-provider-supplier-enrollment/"
            "revoked-medicare-providers-and-suppliers"
        ),
        methodology_url=(
            "https://data.cms.gov/sites/default/files/2026-03/"
            "887c55c7-3dc1-4b14-aee5-970021b3b194/"
            "Revoked%20Medicare%20Provider%20and%20Supplier%20Methodology.pdf"
        ),
        data_dictionary_url=(
            "https://data.cms.gov/sites/default/files/2026-02/"
            "24f3ae9f-98ec-45a1-97af-ad6d0a72fc47/"
            "Revoked%20Medicare%20Provider%20and%20Supplier%20Data%20Dictionary.pdf"
        ),
        expected_columns=REVOCATION_COLUMNS,
        population=(
            "Providers and suppliers with a Medicare revocation under 42 CFR 424.535, an "
            "active re-enrollment bar, and a first-level appeal exhausted or not filed, as "
            "represented in this point-in-time PECOS-derived release"
        ),
        exclusions=(
            "Revocation records outside the exact requested NPI set",
            "Revocations whose re-enrollment bar was not active in this point-in-time release",
            "Recent actions not yet included because of the first-level appeal and publication lag",
            "Absence from this file does not establish current Medicare enrollment or eligibility",
        ),
    ),
}


def get_cms_provider_records_release(dataset_key: str) -> CmsProviderRecordsRelease:
    try:
        return RELEASES[dataset_key]
    except KeyError as error:
        choices = ", ".join(sorted(RELEASES))
        raise ValueError(f"dataset must be one of: {choices}") from error


def _repository_relative_raw_path(path: Path) -> str:
    try:
        relative_path = path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"Provider-record snapshot is outside the repository: {path}") from error
    if not relative_path.startswith("data/raw/"):
        raise ValueError(f"Provider-record snapshot must be retained under data/raw/: {path}")
    return relative_path


def _page_path(snapshot_dir: Path, relative_filename: str) -> Path:
    path = (snapshot_dir / relative_filename).resolve()
    try:
        path.relative_to(snapshot_dir.resolve())
    except ValueError as error:
        raise ValueError(f"API page escapes its snapshot directory: {relative_filename}") from error
    return path


def _exact_npis(inventory: dict[str, Any]) -> tuple[str, ...]:
    filters = inventory.get("filters")
    if not isinstance(filters, dict) or set(filters) != {"condition"}:
        raise ValueError("Provider-record inventory requires one structured exact IN filter")
    condition = filters["condition"]
    if not isinstance(condition, dict) or set(condition) != {"path", "operator", "values"}:
        raise ValueError("Provider-record inventory has an invalid structured filter")
    if condition.get("path") != "NPI" or condition.get("operator") != "IN":
        raise ValueError("Provider-record inventory must use an exact IN filter on NPI")
    values = condition.get("values")
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError("Provider-record NPI filter values must be strings")
    npis = tuple(values)
    if not npis or len(npis) > MAX_NPIS:
        raise ValueError(f"Provider-record snapshots require between 1 and {MAX_NPIS} NPIs")
    if npis != tuple(sorted(set(npis))):
        raise ValueError("Provider-record NPI filter values must be sorted and unique")
    if any(_NPI_PATTERN.fullmatch(npi) is None for npi in npis):
        raise ValueError("Provider-record NPI filter values must contain exactly ten digits")
    return npis


def _validate_inventory_envelope(inventory: dict[str, Any]) -> None:
    if inventory.get("page_size") not in range(1, MAX_ROWS + 1):
        raise ValueError(f"Provider-record API page size must be between 1 and {MAX_ROWS}")
    if inventory.get("max_pages") != MAX_PAGES:
        raise ValueError("Provider-record acquisition must use the one-page safety limit")
    if (
        not isinstance(inventory.get("max_response_bytes"), int)
        or not 0 < inventory["max_response_bytes"] <= MAX_PAGE_BYTES
    ):
        raise ValueError("Provider-record acquisition exceeded its per-page byte ceiling")
    if (
        not isinstance(inventory.get("max_total_bytes"), int)
        or not 0 < inventory["max_total_bytes"] <= MAX_TOTAL_BYTES
    ):
        raise ValueError("Provider-record acquisition exceeded its cumulative byte ceiling")
    if inventory.get("sort_fields") != ["NPI"]:
        raise ValueError("Provider-record acquisition must use NPI as its stable API sort")
    for field in ("expected_rows", "total_rows", "total_bytes"):
        if not isinstance(inventory.get(field), int) or inventory[field] < 0:
            raise ValueError(f"Provider-record inventory has an invalid {field}")
    if inventory["expected_rows"] != inventory["total_rows"]:
        raise ValueError("Provider-record API rows do not match the stats preflight")
    if inventory["total_rows"] > MAX_ROWS:
        raise ValueError(f"Provider-record snapshot exceeds the {MAX_ROWS}-row safety limit")
    if inventory["total_bytes"] > inventory["max_total_bytes"]:
        raise ValueError("Provider-record snapshot exceeds its recorded byte ceiling")


def _parse_observed_at(value: Any) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("Provider-record inventory observed_at must be UTC and end in Z")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Provider-record inventory observed_at is invalid") from error
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("Provider-record inventory observed_at must be UTC")
    return value


def _validate_rows(
    rows: list[Any],
    *,
    release: CmsProviderRecordsRelease,
    requested_npis: tuple[str, ...],
) -> tuple[list[dict[str, str]], int]:
    normalized: list[dict[str, str]] = []
    expected = set(release.expected_columns)
    requested = set(requested_npis)
    enrollment_ids: list[str] = []
    for position, item in enumerate(rows, start=1):
        if not isinstance(item, dict) or set(item) != expected:
            raise ValueError(
                f"Provider-record row {position} does not match the pinned {release.key} schema"
            )
        if not all(isinstance(value, str) for value in item.values()):
            raise ValueError(f"Provider-record row {position} contains a non-string value")
        row = dict(item)
        if row["NPI"] not in requested:
            raise ValueError(f"Provider-record row {position} violates its exact NPI filter")
        enrollment_id = row["ENRLMT_ID"]
        if _ENROLLMENT_ID_PATTERN.fullmatch(enrollment_id) is None:
            raise ValueError(f"Provider-record row {position} has an invalid enrollment ID")
        if row["MULTIPLE_NPI_FLAG"] not in {"Y", "N"}:
            raise ValueError(f"Provider-record row {position} has an invalid multiple-NPI flag")
        if release.key == "revoked":
            if not row["REVOCATION_RSN"]:
                raise ValueError(f"Revocation row {position} is missing its reason")
            for field in ("REVOCATION_EFCTV_DT", "REENROLLMENT_BAR_EXPRTN_DT"):
                try:
                    date.fromisoformat(row[field])
                except ValueError as error:
                    raise ValueError(f"Revocation row {position} has an invalid {field}") from error
        normalized.append(row)
        enrollment_ids.append(enrollment_id)
    duplicate_count = len(enrollment_ids) - len(set(enrollment_ids))
    if duplicate_count:
        raise ValueError("Provider-record snapshot repeats an enrollment ID")
    return normalized, duplicate_count


def profile_cms_provider_records_api_inventory(
    inventory_path: Path,
    *,
    dataset_key: str,
) -> dict[str, Any]:
    release = get_cms_provider_records_release(dataset_key)
    try:
        inventory_bytes = inventory_path.read_bytes()
        inventory = json.loads(inventory_bytes)
    except json.JSONDecodeError as error:
        raise ValueError(f"Provider-record inventory is not JSON: {inventory_path}") from error
    if not isinstance(inventory, dict):
        raise ValueError("Provider-record inventory must be a JSON object")
    if inventory.get("inventory_version") != 1:
        raise ValueError("Provider-record inventory version must be 1")
    if inventory.get("dataset_uuid") != release.version_id:
        raise ValueError(
            f"Provider-record inventory does not use the pinned {release.release_label} UUID"
        )
    requested_npis = _exact_npis(inventory)
    _validate_inventory_envelope(inventory)
    observed_at = _parse_observed_at(inventory.get("observed_at"))

    pages = inventory.get("pages")
    if not isinstance(pages, list) or len(pages) != 1 or not isinstance(pages[0], dict):
        raise ValueError("Provider-record inventory must retain exactly one API page")
    page = pages[0]
    required_page_fields = {
        "ordinal",
        "offset",
        "rows",
        "url",
        "relative_filename",
        "bytes",
        "sha256",
        "media_type",
    }
    if set(page) != required_page_fields:
        raise ValueError("Provider-record inventory page has an unexpected shape")
    if page["ordinal"] != 1 or page["offset"] != 0:
        raise ValueError("Provider-record inventory must start with page 1 at offset 0")
    if not isinstance(page["rows"], int) or page["rows"] < 0:
        raise ValueError("Provider-record inventory page has an invalid row count")
    if not isinstance(page["bytes"], int) or page["bytes"] < 0:
        raise ValueError("Provider-record inventory page has an invalid byte count")
    if not isinstance(page["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", page["sha256"]) is None:
        raise ValueError("Provider-record inventory page has an invalid SHA-256")
    if inventory["total_bytes"] != page["bytes"]:
        raise ValueError("Provider-record inventory total bytes do not match its retained page")
    expected_url = build_data_url(
        release.version_id,
        ExactInFilter("NPI", requested_npis),
        offset=0,
        page_size=inventory["page_size"],
        sort_fields=("NPI",),
    )
    if page["url"] != expected_url:
        raise ValueError("Provider-record API URL does not reproduce the recorded exact query")
    if not isinstance(page["relative_filename"], str) or not page["relative_filename"]:
        raise ValueError("Provider-record inventory page filename is invalid")
    snapshot_dir = inventory_path.resolve().parent
    page_path = _page_path(snapshot_dir, page["relative_filename"])
    page_bytes = page_path.read_bytes()
    if page["bytes"] != len(page_bytes) or page["sha256"] != sha256(page_bytes).hexdigest():
        raise ValueError("Provider-record API page bytes or SHA-256 differ from the inventory")
    if page["media_type"] != "application/json":
        raise ValueError("Provider-record API page must be application/json")
    if page["bytes"] > inventory["max_response_bytes"]:
        raise ValueError("Provider-record API page exceeds its recorded byte ceiling")
    try:
        raw_rows = json.loads(page_bytes)
    except json.JSONDecodeError as error:
        raise ValueError("Provider-record API page is not valid JSON") from error
    if not isinstance(raw_rows, list):
        raise ValueError("Provider-record API page must contain a row array")
    if page["rows"] != len(raw_rows) or inventory["total_rows"] != len(raw_rows):
        raise ValueError("Provider-record API row counts do not match the retained page")
    rows, duplicate_count = _validate_rows(
        raw_rows,
        release=release,
        requested_npis=requested_npis,
    )
    matched_npis = tuple(sorted({row["NPI"] for row in rows}))
    missing_npis = tuple(sorted(set(requested_npis) - set(matched_npis)))
    return {
        "release": release,
        "inventory_path": inventory_path.resolve(),
        "inventory_relative_path": _repository_relative_raw_path(inventory_path),
        "inventory_bytes": len(inventory_bytes),
        "inventory_sha256": sha256(inventory_bytes).hexdigest(),
        "page_path": page_path,
        "page_relative_path": _repository_relative_raw_path(page_path),
        "page": page,
        "rows": len(rows),
        "columns": len(release.expected_columns),
        "aggregation_key_duplicates": duplicate_count,
        "requested_npis": requested_npis,
        "matched_npis": matched_npis,
        "missing_npis": missing_npis,
        "observed_at": observed_at,
        "request_url": expected_url,
        "page_size": inventory["page_size"],
        "max_pages": inventory["max_pages"],
        "max_response_bytes": inventory["max_response_bytes"],
        "max_total_bytes": inventory["max_total_bytes"],
    }


def build_cms_provider_records_api_manifest(
    inventory_path: Path,
    manifest_path: Path,
    *,
    dataset_key: str,
    documentation_snapshots: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    if manifest_path.exists():
        raise FileExistsError(f"Refusing to replace manifest: {manifest_path}")
    profile = profile_cms_provider_records_api_inventory(
        inventory_path,
        dataset_key=dataset_key,
    )
    release: CmsProviderRecordsRelease = profile["release"]
    requested_npis = list(profile["requested_npis"])
    matched_npis = list(profile["matched_npis"])
    missing_npis = list(profile["missing_npis"])
    page = profile["page"]
    validation_notes = [
        f"Exact NPI scope: {', '.join(requested_npis)}.",
        f"Matched NPIs: {', '.join(matched_npis) if matched_npis else 'none'}.",
        (
            "NPIs without a published row in this snapshot: "
            f"{', '.join(missing_npis) if missing_npis else 'none'}."
        ),
        (
            "The API returned no field names for an empty row array; the recorded column count "
            "and schema fingerprint come from the pinned CMS data dictionary."
            if profile["rows"] == 0
            else "Every retained row matched the pinned CMS data-dictionary field set."
        ),
        (
            "NPI absence is a point-in-time public-file observation, not evidence that the NPI "
            "was never enrolled, never revoked, or currently eligible."
        ),
    ]
    manifest = {
        "manifest_version": 2,
        "status": "complete",
        "source": {
            "name": "Centers for Medicare & Medicaid Services",
            "type": "CMS",
            "homepage": "https://data.cms.gov/",
        },
        "dataset": {
            "name": release.dataset_name,
            "slug": release.dataset_slug,
            "dataset_id": release.dataset_type_id,
            "version_id": release.version_id,
            "data_year": None,
            "population": release.population,
            "aggregation_keys": ["ENRLMT_ID"],
        },
        "observation": {
            "published_at": None,
            "modified_at": release.modified_at.isoformat(),
            "observed_at": profile["observed_at"],
            "accessed_at": profile["observed_at"][:10],
        },
        "documentation": {
            "landing_page": release.landing_page,
            "methodology": release.methodology_url,
            "data_dictionary": release.data_dictionary_url,
            "snapshots": sorted(set(documentation_snapshots)),
        },
        "terms": {
            "license_name": None,
            "license_url": None,
            "access_restrictions": "Public-use CMS Data API; no authentication or DUA",
            "public_use_verified": True,
        },
        "retrieval": {
            "method": "api",
            "request_url": profile["request_url"],
            "parameters": {
                "dataset_snapshot": release.release_label,
                "filter": {"NPI": requested_npis},
                "sort_fields": ["NPI"],
                "page_size": profile["page_size"],
                "max_pages": profile["max_pages"],
                "max_response_bytes": profile["max_response_bytes"],
                "max_total_bytes": profile["max_total_bytes"],
                "matched_npis": matched_npis,
                "missing_npis": missing_npis,
            },
            "query": None,
        },
        "files": [
            {
                "relative_path": profile["page_relative_path"],
                "filename": profile["page_path"].name,
                "role": "api-page",
                "bytes": page["bytes"],
                "sha256": page["sha256"],
                "media_type": page["media_type"],
                "compression": None,
                "schema_fingerprint": release.schema_fingerprint,
            },
            {
                "relative_path": profile["inventory_relative_path"],
                "filename": profile["inventory_path"].name,
                "role": "acquisition-inventory",
                "bytes": profile["inventory_bytes"],
                "sha256": profile["inventory_sha256"],
                "media_type": "application/json",
                "compression": None,
                "schema_fingerprint": None,
            },
        ],
        "semantics": {
            "monetary_fields": {},
            "utilization_fields": {},
            "suppression": (
                "CMS documentation does not describe claims-style cell suppression for this file. "
                "A missing exact-NPI row is governed by the point-in-time population, publication "
                "lag, and documented exclusions; it is not a historical-status finding."
            ),
            "exclusions": list(release.exclusions),
        },
        "validation": {
            "rows": profile["rows"],
            "columns": profile["columns"],
            "aggregation_key_duplicates": profile["aggregation_key_duplicates"],
            "notes": validation_notes,
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
