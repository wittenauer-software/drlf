from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

from drlf.sources.cms_file import (
    DEFAULT_CHUNK_BYTES,
    CmsFileDownload,
    repository_relative_raw_path,
    validate_cms_https_url,
)
from drlf.sources.manifest import load_and_validate_manifest

DATASET_NAME = "Medicare Durable Medical Equipment, Devices & Supplies - by Supplier"
DATASET_SLUG = "medicare-durable-medical-equipment-devices-supplies-by-supplier"
DATASET_TYPE_UUID = "a2d56d3f-3531-4315-9d87-e29986516b41"
RESOURCES_API_ROOT = "https://data.cms.gov/data-api/v1/dataset-resources"
LANDING_PAGE = (
    "https://data.cms.gov/provider-summary-by-type-of-service/"
    "medicare-durable-medical-equipment-devices-supplies/"
    "medicare-durable-medical-equipment-devices-supplies-by-supplier"
)
METHODOLOGY_URL = (
    "https://data.cms.gov/sites/default/files/2025-06/"
    "a837dcf7-3d06-4c1f-8d4a-82ee737b7932/"
    "MUP_DME_RY25_Methodology_20250617_508.pdf"
)
DICTIONARY_URL = (
    "https://data.cms.gov/sites/default/files/2025-03/"
    "9b42d94a-6e7d-473f-bacc-3a756d72f74e/"
    "MUP_DME_RY24_Data-Dictionary_20240620_supr-DY17-22.pdf"
)

EXPECTED_COLUMNS = (
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
    "Suplr_Prvdr_RUCA",
    "Suplr_Prvdr_RUCA_Desc",
    "Suplr_Prvdr_Cntry",
    "Suplr_Prvdr_Spclty_Desc",
    "Suplr_Prvdr_Spclty_Srce",
    "Tot_Suplr_HCPCS_Cds",
    "Tot_Suplr_Benes",
    "Tot_Suplr_Clms",
    "Tot_Suplr_Srvcs",
    "Suplr_Sbmtd_Chrgs",
    "Suplr_Mdcr_Alowd_Amt",
    "Suplr_Mdcr_Pymt_Amt",
    "Suplr_Mdcr_Stdzd_Pymt_Amt",
    "DME_Sprsn_Ind",
    "DME_Tot_Suplr_HCPCS_Cds",
    "DME_Tot_Suplr_Benes",
    "DME_Tot_Suplr_Clms",
    "DME_Tot_Suplr_Srvcs",
    "DME_Suplr_Sbmtd_Chrgs",
    "DME_Suplr_Mdcr_Alowd_Amt",
    "DME_Suplr_Mdcr_Pymt_Amt",
    "DME_Suplr_Mdcr_Stdzd_Pymt_Amt",
    "POS_Sprsn_Ind",
    "POS_Tot_Suplr_HCPCS_Cds",
    "POS_Tot_Suplr_Benes",
    "POS_Tot_Suplr_Clms",
    "POS_Tot_Suplr_Srvcs",
    "POS_Suplr_Sbmtd_Chrgs",
    "POS_Suplr_Mdcr_Alowd_Amt",
    "POS_Suplr_Mdcr_Pymt_Amt",
    "POS_Suplr_Mdcr_Stdzd_Pymt_Amt",
    "Drug_Sprsn_Ind",
    "Drug_Tot_Suplr_HCPCS_Cds",
    "Drug_Tot_Suplr_Benes",
    "Drug_Tot_Suplr_Clms",
    "Drug_Tot_Suplr_Srvcs",
    "Drug_Suplr_Sbmtd_Chrgs",
    "Drug_Suplr_Mdcr_Alowd_Amt",
    "Drug_Suplr_Mdcr_Pymt_Amt",
    "Drug_Suplr_Mdcr_Stdzd_Pymt_Amt",
    "Bene_Avg_Age",
    "Bene_Age_LT_65_Cnt",
    "Bene_Age_65_74_Cnt",
    "Bene_Age_75_84_Cnt",
    "Bene_Age_GT_84_Cnt",
    "Bene_Feml_Cnt",
    "Bene_Male_Cnt",
    "Bene_Race_Wht_Cnt",
    "Bene_Race_Black_Cnt",
    "Bene_Race_Api_Cnt",
    "Bene_Race_Hspnc_Cnt",
    "Bene_Race_Natind_Cnt",
    "Bene_Race_Othr_Cnt",
    "Bene_Ndual_Cnt",
    "Bene_Dual_Cnt",
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
)
AGGREGATION_KEYS = ("Suplr_NPI",)

_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_NPI_PATTERN = re.compile(r"[0-9]{10}")
_FILENAME_PATTERN = re.compile(
    r"mup_dme_ry[0-9]{2}_p05_v[0-9]+_dy(?P<year>[0-9]{2})_supr\.csv",
    re.IGNORECASE,
)

_TOTAL_NUMERIC_COLUMNS = frozenset(
    {
        "Tot_Suplr_HCPCS_Cds",
        "Tot_Suplr_Benes",
        "Tot_Suplr_Clms",
        "Tot_Suplr_Srvcs",
        "Suplr_Sbmtd_Chrgs",
        "Suplr_Mdcr_Alowd_Amt",
        "Suplr_Mdcr_Pymt_Amt",
        "Suplr_Mdcr_Stdzd_Pymt_Amt",
    }
)
_REQUIRED_NUMERIC_COLUMNS = _TOTAL_NUMERIC_COLUMNS - {"Tot_Suplr_Benes"}
_OPTIONAL_NUMERIC_COLUMNS = frozenset(
    column
    for column in EXPECTED_COLUMNS
    if column.startswith(("DME_Tot_", "DME_Suplr_", "POS_Tot_", "POS_Suplr_"))
    or column.startswith(("Drug_Tot_", "Drug_Suplr_", "Bene_"))
) | {"Tot_Suplr_Benes"}
_NUMERIC_COLUMNS = _TOTAL_NUMERIC_COLUMNS | _OPTIONAL_NUMERIC_COLUMNS
_INTEGER_COUNT_COLUMNS = frozenset(
    column for column in _NUMERIC_COLUMNS if column.endswith(("_Cds", "_Benes", "_Clms", "_Cnt"))
)
_PERCENT_COLUMNS = frozenset(column for column in _NUMERIC_COLUMNS if column.endswith("_Pct"))
_ORDERED_NUMERIC_COLUMNS = tuple(
    column for column in EXPECTED_COLUMNS if column in _NUMERIC_COLUMNS
)


@dataclass(frozen=True)
class DmeposSupplierRelease:
    data_year: int
    version_id: str
    download_url: str
    filename: str
    modified_at: date
    expected_bytes: int
    expected_rows: int


KNOWN_SUPPLIER_RELEASES = {
    2022: DmeposSupplierRelease(
        data_year=2022,
        version_id="471f9c0d-12c4-4601-a547-559dcfa2d0e2",
        download_url=(
            "https://data.cms.gov/sites/default/files/2025-11/"
            "8a38cf50-1ab7-4c53-afe6-f1b12b842b1d/"
            "mup_dme_ry25_p05_v20_dy22_supr.csv"
        ),
        filename="mup_dme_ry25_p05_v20_dy22_supr.csv",
        modified_at=date(2025, 3, 26),
        expected_bytes=35_107_995,
        expected_rows=66_406,
    ),
    2023: DmeposSupplierRelease(
        data_year=2023,
        version_id="7bb52a09-9eba-43f9-b7ec-57f65fbbc86c",
        download_url=(
            "https://data.cms.gov/sites/default/files/2025-06/"
            "5b10992b-8290-4b93-b036-0c233020d7da/"
            "mup_dme_ry25_p05_v10_dy23_supr.csv"
        ),
        filename="mup_dme_ry25_p05_v10_dy23_supr.csv",
        modified_at=date(2025, 10, 29),
        expected_bytes=33_493_463,
        expected_rows=63_988,
    ),
    2024: DmeposSupplierRelease(
        data_year=2024,
        version_id="4c6cfc68-a149-4bfe-b934-db2b92d0f360",
        download_url=(
            "https://data.cms.gov/sites/default/files/2026-07/"
            "037d1a55-7ae6-4500-9a6c-f86603705cdd/"
            "mup_dme_ry26_p05_v10_dy24_supr.csv"
        ),
        filename="mup_dme_ry26_p05_v10_dy24_supr.csv",
        modified_at=date(2026, 7, 23),
        expected_bytes=31_326_606,
        expected_rows=60_060,
    ),
}


def get_dmepos_supplier_release(data_year: int) -> DmeposSupplierRelease:
    try:
        return KNOWN_SUPPLIER_RELEASES[data_year]
    except KeyError as error:
        available = ", ".join(str(year) for year in sorted(KNOWN_SUPPLIER_RELEASES))
        raise ValueError(
            f"No reviewed DMEPOS supplier release for {data_year}; available years: {available}"
        ) from error


def _validate_release(release: DmeposSupplierRelease) -> None:
    if not 2014 <= release.data_year <= 2200:
        raise ValueError("DMEPOS supplier data_year must be between 2014 and 2200")
    if not _UUID_PATTERN.fullmatch(release.version_id):
        raise ValueError("DMEPOS supplier release requires a version-specific CMS UUID")
    if release.version_id.casefold() == DATASET_TYPE_UUID.casefold():
        raise ValueError("DMEPOS supplier release cannot use the moving dataset type UUID")
    validate_cms_https_url(release.download_url)
    parsed = urlsplit(release.download_url)
    if parsed.hostname != "data.cms.gov" or parsed.query or parsed.fragment:
        raise ValueError("DMEPOS supplier bulk URL must be an exact data.cms.gov file URL")
    if Path(parsed.path).name != release.filename:
        raise ValueError("DMEPOS supplier URL filename differs from the release filename")
    filename_match = _FILENAME_PATTERN.fullmatch(release.filename)
    if not filename_match or int(filename_match.group("year")) != release.data_year % 100:
        raise ValueError("DMEPOS supplier filename does not encode the declared data year")
    if release.expected_bytes < 1 or release.expected_rows < 1:
        raise ValueError("DMEPOS supplier release requires positive expected bytes and rows")


def _stream_file_integrity(path: Path, *, chunk_bytes: int) -> tuple[int, str]:
    if chunk_bytes < 1:
        raise ValueError("chunk_bytes must be at least 1")
    digest = sha256()
    total_bytes = 0
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_bytes):
            digest.update(chunk)
            total_bytes += len(chunk)
    return total_bytes, digest.hexdigest()


def profile_dmepos_supplier_csv(
    raw_path: Path,
    *,
    expected_bytes: int | None = None,
    expected_sha256: str | None = None,
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
) -> dict[str, object]:
    """Stream-validate one complete annual DMEPOS supplier summary CSV."""
    if not raw_path.is_file():
        raise FileNotFoundError(f"DMEPOS supplier source file does not exist: {raw_path}")
    file_bytes, file_sha256 = _stream_file_integrity(raw_path, chunk_bytes=chunk_bytes)
    if expected_bytes is not None and file_bytes != expected_bytes:
        raise ValueError(
            f"DMEPOS supplier file size mismatch: expected {expected_bytes}, found {file_bytes}"
        )
    if expected_sha256 is not None and file_sha256 != expected_sha256.casefold():
        raise ValueError("DMEPOS supplier file SHA-256 does not match the acquisition record")

    rows = 0
    keys: set[str] = set()
    duplicate_keys = 0
    invalid_npis = 0
    invalid_entities = 0
    invalid_numeric_values = 0
    invalid_numeric_examples: list[str] = []
    suppressed_numeric_values = 0
    unknown_total_beneficiary_values = 0
    out_of_range_percentage_values = 0
    out_of_range_percentage_examples: list[str] = []
    malformed_rows = 0
    with raw_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = tuple(reader.fieldnames or ())
        if columns != EXPECTED_COLUMNS:
            missing = sorted(set(EXPECTED_COLUMNS) - set(columns))
            unexpected = sorted(set(columns) - set(EXPECTED_COLUMNS))
            raise ValueError(
                "DMEPOS supplier CSV header does not match the reviewed schema: "
                f"missing={missing}, unexpected={unexpected}, "
                f"expected_columns={len(EXPECTED_COLUMNS)}, found_columns={len(columns)}"
            )
        for row in reader:
            rows += 1
            if None in row or any(value is None for value in row.values()):
                malformed_rows += 1
                continue

            npi = row["Suplr_NPI"].strip()
            if not _NPI_PATTERN.fullmatch(npi):
                invalid_npis += 1
            if npi in keys:
                duplicate_keys += 1
            keys.add(npi)

            if row["Suplr_Prvdr_Ent_Cd"].strip() not in {"I", "O"}:
                invalid_entities += 1

            for column in _ORDERED_NUMERIC_COLUMNS:
                value = row[column].strip()
                if not value:
                    if column == "Tot_Suplr_Benes":
                        unknown_total_beneficiary_values += 1
                    elif column in _REQUIRED_NUMERIC_COLUMNS:
                        invalid_numeric_values += 1
                        if len(invalid_numeric_examples) < 5:
                            invalid_numeric_examples.append(f"{column}=<blank>")
                    continue
                if value in {"*", "#"} and column in _OPTIONAL_NUMERIC_COLUMNS:
                    suppressed_numeric_values += 1
                    if column == "Tot_Suplr_Benes":
                        unknown_total_beneficiary_values += 1
                    continue
                try:
                    number = Decimal(value)
                except InvalidOperation:
                    invalid_numeric_values += 1
                    if len(invalid_numeric_examples) < 5:
                        invalid_numeric_examples.append(f"{column}={value!r}")
                    continue
                if not number.is_finite():
                    invalid_numeric_values += 1
                    if len(invalid_numeric_examples) < 5:
                        invalid_numeric_examples.append(f"{column}={value!r}")
                    continue
                invalid_reason: str | None = None
                if number < 0:
                    invalid_reason = "negative"
                elif column in _INTEGER_COUNT_COLUMNS and number != number.to_integral_value():
                    invalid_reason = "fractional count"
                elif column in _PERCENT_COLUMNS and number > 1:
                    out_of_range_percentage_values += 1
                    if len(out_of_range_percentage_examples) < 5:
                        out_of_range_percentage_examples.append(f"{column}={value!r}")
                elif column == "Bene_Avg_Age" and number > 120:
                    invalid_reason = "average age outside 0..120"
                if invalid_reason is not None:
                    invalid_numeric_values += 1
                    if len(invalid_numeric_examples) < 5:
                        invalid_numeric_examples.append(f"{column}={value!r} ({invalid_reason})")

    if rows < 1:
        raise ValueError("DMEPOS supplier CSV has no data rows")
    if (
        duplicate_keys
        or invalid_npis
        or invalid_entities
        or invalid_numeric_values
        or malformed_rows
    ):
        raise ValueError(
            "DMEPOS supplier CSV validation failed: "
            f"duplicate_keys={duplicate_keys}, invalid_npis={invalid_npis}, "
            f"invalid_entities={invalid_entities}, "
            f"invalid_numeric_values={invalid_numeric_values}, malformed_rows={malformed_rows}, "
            f"invalid_numeric_examples={invalid_numeric_examples}"
        )

    return {
        "bytes": file_bytes,
        "sha256": file_sha256,
        "rows": rows,
        "columns": list(columns),
        "column_count": len(columns),
        "aggregation_key_duplicates": duplicate_keys,
        "suppressed_numeric_values": suppressed_numeric_values,
        "unknown_total_beneficiary_values": unknown_total_beneficiary_values,
        "out_of_range_percentage_values": out_of_range_percentage_values,
        "out_of_range_percentage_examples": out_of_range_percentage_examples,
        "schema_fingerprint": sha256("\n".join(sorted(columns)).encode()).hexdigest(),
    }


def _observed_at(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def build_dmepos_supplier_manifest(
    download: CmsFileDownload,
    manifest_path: Path,
    *,
    release: DmeposSupplierRelease,
    observed_at: datetime,
    documentation_snapshots: tuple[str, ...] = (),
) -> dict[str, object]:
    """Build a complete manifest for one reviewed annual DMEPOS supplier bulk file."""
    if manifest_path.exists():
        raise FileExistsError(f"Refusing to replace manifest: {manifest_path}")
    _validate_release(release)
    observed_at_text = _observed_at(observed_at)
    if download.request_url != release.download_url:
        raise ValueError("DMEPOS download URL differs from the reviewed annual release")
    validate_cms_https_url(download.final_url)
    if download.path.name != release.filename:
        raise ValueError("Retained DMEPOS filename differs from the reviewed annual release")
    if download.compression is not None:
        raise ValueError("DMEPOS supplier bulk source must be the uncompressed CMS CSV")
    if download.bytes != release.expected_bytes:
        raise ValueError(
            "DMEPOS acquired byte count differs from the reviewed CMS resource metadata"
        )
    if download.max_bytes < download.bytes:
        raise ValueError("DMEPOS acquisition record has an invalid byte ceiling")
    if (
        download.preflight_total_bytes is not None
        and download.preflight_total_bytes != release.expected_bytes
    ):
        raise ValueError("DMEPOS preflight size differs from reviewed CMS resource metadata")

    profile = profile_dmepos_supplier_csv(
        download.path,
        expected_bytes=download.bytes,
        expected_sha256=download.sha256,
    )
    if profile["rows"] != release.expected_rows:
        raise ValueError(
            "DMEPOS supplier row count differs from the reviewed CMS API stats: "
            f"expected {release.expected_rows}, found {profile['rows']}"
        )

    manifest: dict[str, object] = {
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
            "data_year": release.data_year,
            "population": (
                "Suppliers with valid NPIs represented in 100% final-action Original Medicare "
                "fee-for-service Part B non-institutional DMEPOS claim line items; complete "
                "public annual supplier summary"
            ),
            "aggregation_keys": list(AGGREGATION_KEYS),
        },
        "observation": {
            "published_at": None,
            "modified_at": release.modified_at.isoformat(),
            "observed_at": observed_at_text,
            "accessed_at": observed_at_text[:10],
        },
        "documentation": {
            "landing_page": LANDING_PAGE,
            "methodology": METHODOLOGY_URL,
            "data_dictionary": DICTIONARY_URL,
            "snapshots": list(documentation_snapshots),
        },
        "terms": {
            "license_name": "U.S. Government Work",
            "license_url": "https://www.usa.gov/government-works",
            "access_restrictions": "CMS Public Use File (Free); no authentication or DUA",
            "public_use_verified": True,
        },
        "retrieval": {
            "method": "download",
            "request_url": release.download_url,
            "parameters": {
                "final_url": download.final_url,
                "expected_bytes": release.expected_bytes,
                "expected_rows": release.expected_rows,
                "max_download_bytes": download.max_bytes,
                **(
                    {"disk_reserve_bytes": download.disk_reserve_bytes}
                    if download.disk_reserve_bytes is not None
                    else {}
                ),
                "preflight_total_bytes": download.preflight_total_bytes,
                "preflight_size_source": download.preflight_size_source,
                "version_resources_api": f"{RESOURCES_API_ROOT}/{release.version_id}",
                "resumed_from": download.resumed_from,
                "etag": download.etag,
                "last_modified": download.last_modified,
            },
            "query": None,
        },
        "files": [
            {
                "relative_path": repository_relative_raw_path(download.path),
                "filename": download.path.name,
                "role": "data",
                "bytes": profile["bytes"],
                "sha256": profile["sha256"],
                "media_type": download.media_type,
                "compression": None,
                "schema_fingerprint": profile["schema_fingerprint"],
            }
        ],
        "semantics": {
            "monetary_fields": {
                "Suplr_Sbmtd_Chrgs": (
                    "Total charges suppliers submitted for qualifying DMEPOS products and "
                    "services; not an allowed or paid amount."
                ),
                "Suplr_Mdcr_Alowd_Amt": (
                    "Medicare allowed amount: Medicare payment plus beneficiary deductible and "
                    "coinsurance and any third-party responsibility; not supplier net revenue."
                ),
                "Suplr_Mdcr_Pymt_Amt": (
                    "Amount Medicare paid after deductible and coinsurance for qualifying DMEPOS "
                    "line items attributed to the supplier NPI; not an estimate of improper "
                    "payment, loss, damages, or the ultimate corporate recipient."
                ),
                "Suplr_Mdcr_Stdzd_Pymt_Amt": (
                    "Medicare payment after geographic standardization; a comparison measure, "
                    "not the actual amount paid."
                ),
                "DME_Suplr_Sbmtd_Chrgs": "Submitted-charge subtotal for DME products.",
                "DME_Suplr_Mdcr_Alowd_Amt": "Medicare allowed-amount subtotal for DME products.",
                "DME_Suplr_Mdcr_Pymt_Amt": "Medicare payment subtotal for DME products.",
                "DME_Suplr_Mdcr_Stdzd_Pymt_Amt": (
                    "Geographically standardized Medicare payment subtotal for DME products."
                ),
                "POS_Suplr_Sbmtd_Chrgs": (
                    "Submitted-charge subtotal for prosthetic and orthotic products."
                ),
                "POS_Suplr_Mdcr_Alowd_Amt": (
                    "Medicare allowed-amount subtotal for prosthetic and orthotic products."
                ),
                "POS_Suplr_Mdcr_Pymt_Amt": (
                    "Medicare payment subtotal for prosthetic and orthotic products."
                ),
                "POS_Suplr_Mdcr_Stdzd_Pymt_Amt": (
                    "Geographically standardized Medicare payment subtotal for prosthetic and "
                    "orthotic products."
                ),
                "Drug_Suplr_Sbmtd_Chrgs": (
                    "Submitted-charge subtotal for drug and nutritional products."
                ),
                "Drug_Suplr_Mdcr_Alowd_Amt": (
                    "Medicare allowed-amount subtotal for drug and nutritional products."
                ),
                "Drug_Suplr_Mdcr_Pymt_Amt": (
                    "Medicare payment subtotal for drug and nutritional products."
                ),
                "Drug_Suplr_Mdcr_Stdzd_Pymt_Amt": (
                    "Geographically standardized Medicare payment subtotal for drug and "
                    "nutritional products."
                ),
            },
            "utilization_fields": {
                "Suplr_NPI": (
                    "Supplier NPI on the DMEPOS claim; it may identify an individual or "
                    "organization and does not by itself establish a furnishing location, "
                    "corporate parent, referring provider, remittance account, or ultimate payee."
                ),
                "Tot_Suplr_HCPCS_Cds": "Distinct DMEPOS HCPCS codes attributed to the supplier.",
                "Tot_Suplr_Benes": (
                    "Distinct Original Medicare fee-for-service beneficiaries associated with "
                    "the supplier's qualifying DMEPOS claims."
                ),
                "Tot_Suplr_Clms": "Qualifying DMEPOS claims submitted by the supplier.",
                "Tot_Suplr_Srvcs": (
                    "DMEPOS product or service units; counting units vary by HCPCS and are not "
                    "uniform visits or people."
                ),
                "DME/POS/Drug subtotals": (
                    "Separate utilization subtotals for durable equipment, prosthetic/orthotic, "
                    "and drug/nutritional product categories."
                ),
                "Bene_Avg_Risk_Scre": (
                    "Average CMS risk score for the supplier beneficiary panel; contextual case "
                    "mix rather than a product-specific medical-necessity measure."
                ),
            },
            "suppression": (
                "The supplier summary is aggregated from all qualifying claims and is not limited "
                "to visible detailed supplier-service cells. Category suppression indicators use "
                "'*' for 1-10 category claims and '#' for counter-suppression; demographic and "
                "condition values may also be suppressed, and condition percentages from 75% "
                "through 100% are top-coded at 75%. Blank or suppressed is not zero."
            ),
            "exclusions": [
                "Medicare Advantage beneficiaries",
                "Patients without Original Medicare Part B fee-for-service coverage",
                "Institutional Part A claims",
                "Supplier-service cells, HCPCS modifiers, and claim dates",
                "Ordering or referring provider identities and relationships",
                "Billing TIN, reassignment, remittance account, and ultimate payment recipient",
            ],
        },
        "validation": {
            "rows": profile["rows"],
            "columns": profile["column_count"],
            "aggregation_key_duplicates": profile["aggregation_key_duplicates"],
            "notes": [
                "The retained CSV matched the acquisition byte count and SHA-256 hash.",
                (
                    "The byte count and row count matched the reviewed CMS resource metadata and "
                    "API stats for this pinned annual version."
                ),
                (
                    "The exact 93-column header was present; supplier NPIs, entity codes, numeric "
                    "fields, and the one-row-per-supplier-NPI grain were validated by streaming."
                ),
                (
                    f"Preserved {profile['suppressed_numeric_values']} source-marked optional "
                    "numeric cells ('*' or '#') as unknown rather than zero."
                ),
                (
                    f"Preserved {profile['unknown_total_beneficiary_values']} suppressed or "
                    "blank total-beneficiary values as unknown rather than zero."
                ),
                (
                    f"Retained {profile['out_of_range_percentage_values']} source-published "
                    "condition percentage values above 1 exactly as provided. These conflict "
                    "with proportion semantics and are not eligible for case-mix analysis until "
                    "a source-specific validation or correction rule is established. Examples: "
                    f"{profile['out_of_range_percentage_examples']}."
                ),
                (
                    "CMS's version-specific resources API associates the recorded RY25 methodology "
                    "and supplier dictionary with this release. The dictionary filename says "
                    "DY17-22 even for the 2023 and 2024 release listings; treat that as an "
                    "upstream documentation-lag limitation and recheck definitions before "
                    "relying on a year-specific change."
                ),
                (
                    "Provider names, addresses, and specialty are source assertions and do not "
                    "establish a furnishing location, corporate ownership, or ultimate payee."
                ),
            ],
        },
    }

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = manifest_path.with_name(f".{manifest_path.name}.new")
    if temporary_path.exists():
        raise FileExistsError(f"Refusing to replace temporary manifest: {temporary_path}")
    try:
        temporary_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        load_and_validate_manifest(temporary_path)
        temporary_path.replace(manifest_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return manifest
