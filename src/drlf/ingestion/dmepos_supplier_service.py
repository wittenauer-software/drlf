from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from drlf.ingestion.part_b_common import (
    PartBLoadSpec,
    integer,
    load_part_b_pages,
    required_decimal,
    required_integer,
    required_text,
    text,
)
from drlf.sources.dmepos_supplier_service_manifest import (
    AGGREGATION_KEYS,
    DATASET_SLUG,
    DATASET_TYPE_UUID,
    RELEASES,
)
from drlf.sources.manifest import load_and_validate_manifest

DMEPOS_SUPPLIER_SERVICE_COLUMNS = (
    "source_release_id",
    "data_year",
    "supplier_npi",
    "supplier_last_org_name",
    "supplier_first_name",
    "supplier_middle_initial",
    "supplier_credentials",
    "entity_code",
    "supplier_address_line1",
    "supplier_address_line2",
    "supplier_city",
    "supplier_state",
    "supplier_state_fips",
    "supplier_zip5",
    "supplier_ruca_category",
    "supplier_ruca",
    "supplier_ruca_description",
    "supplier_country",
    "supplier_specialty_code",
    "supplier_specialty_description",
    "supplier_specialty_source",
    "rbcs_level",
    "rbcs_id",
    "rbcs_description",
    "hcpcs_code",
    "hcpcs_description",
    "supplier_rental_indicator",
    "total_beneficiaries",
    "total_claims",
    "total_services",
    "average_submitted_charge",
    "average_medicare_allowed_amount",
    "average_medicare_payment_amount",
    "average_medicare_standardized_amount",
)


def _nonnegative_decimal(row: dict[str, Any], field: str) -> Decimal:
    value = required_decimal(row, field)
    if not value.is_finite() or value < 0:
        raise ValueError(f"DMEPOS supplier-service row has invalid {field}")
    return value


def _nonnegative_integer(row: dict[str, Any], field: str) -> int:
    value = required_integer(row, field)
    if value < 0:
        raise ValueError(f"DMEPOS supplier-service row has invalid {field}")
    return value


def _optional_nonnegative_integer(row: dict[str, Any], field: str) -> int | None:
    raw_value = text(row.get(field))
    if raw_value in {None, "*", "#"}:
        return None
    value = integer(raw_value)
    if value is not None and value < 0:
        raise ValueError(f"DMEPOS supplier-service row has invalid {field}")
    return value


def _row_values(
    row: dict[str, Any],
    source_release_id: int,
    data_year: int,
) -> tuple[Any, ...]:
    """Map one published supplier/HCPCS/rental row without collapsing units."""
    return (
        source_release_id,
        data_year,
        required_text(row, "Suplr_NPI"),
        text(row.get("Suplr_Prvdr_Last_Name_Org")),
        text(row.get("Suplr_Prvdr_First_Name")),
        text(row.get("Suplr_Prvdr_MI")),
        text(row.get("Suplr_Prvdr_Crdntls")),
        required_text(row, "Suplr_Prvdr_Ent_Cd"),
        text(row.get("Suplr_Prvdr_St1")),
        text(row.get("Suplr_Prvdr_St2")),
        text(row.get("Suplr_Prvdr_City")),
        text(row.get("Suplr_Prvdr_State_Abrvtn")),
        text(row.get("Suplr_Prvdr_State_FIPS")),
        text(row.get("Suplr_Prvdr_Zip5")),
        text(row.get("Suplr_Prvdr_RUCA_Cat")),
        text(row.get("Suplr_Prvdr_RUCA")),
        text(row.get("Suplr_Prvdr_RUCA_Desc")),
        text(row.get("Suplr_Prvdr_Cntry")),
        text(row.get("Suplr_Prvdr_Spclty_Cd")),
        required_text(row, "Suplr_Prvdr_Spclty_Desc"),
        required_text(row, "Suplr_Prvdr_Spclty_Srce"),
        text(row.get("RBCS_Lvl")),
        text(row.get("RBCS_Id")),
        text(row.get("RBCS_Desc")),
        required_text(row, "HCPCS_Cd").upper(),
        required_text(row, "HCPCS_Desc"),
        required_text(row, "Suplr_Rentl_Ind"),
        _optional_nonnegative_integer(row, "Tot_Suplr_Benes"),
        _nonnegative_integer(row, "Tot_Suplr_Clms"),
        _nonnegative_decimal(row, "Tot_Suplr_Srvcs"),
        _nonnegative_decimal(row, "Avg_Suplr_Sbmtd_Chrg"),
        _nonnegative_decimal(row, "Avg_Suplr_Mdcr_Alowd_Amt"),
        _nonnegative_decimal(row, "Avg_Suplr_Mdcr_Pymt_Amt"),
        _nonnegative_decimal(row, "Avg_Suplr_Mdcr_Stdzd_Amt"),
    )


_LOAD_SPEC = PartBLoadSpec(
    dataset_label="CMS Medicare DMEPOS by Supplier and Service",
    dataset_slug=DATASET_SLUG,
    dataset_type_uuid=DATASET_TYPE_UUID,
    aggregation_keys=AGGREGATION_KEYS,
    table_name="dmepos_supplier_service",
    columns=DMEPOS_SUPPLIER_SERVICE_COLUMNS,
    row_mapper=_row_values,
)


def _validate_release_identity(manifest_path: Path) -> None:
    manifest, _ = load_and_validate_manifest(manifest_path)
    if manifest["status"] != "complete":
        raise ValueError("DMEPOS supplier-service loading requires a complete manifest")
    if manifest["source"]["name"] != "Centers for Medicare & Medicaid Services":
        raise ValueError("DMEPOS supplier-service manifest has an unexpected source")
    data_year = manifest["dataset"]["data_year"]
    release = RELEASES.get(data_year)
    if release is None or manifest["dataset"]["version_id"] != release.version_id:
        raise ValueError("DMEPOS supplier-service manifest is not a pinned reviewed release")


def load_dmepos_supplier_service(
    database_url: str,
    manifest_path: Path,
    *,
    code_commit: str,
    pipeline_version: str,
    case_id: str | None = None,
) -> tuple[int, int]:
    """Load one retained, targeted DMEPOS supplier-service API manifest."""
    _validate_release_identity(manifest_path)
    return load_part_b_pages(
        database_url,
        manifest_path,
        spec=_LOAD_SPEC,
        code_commit=code_commit,
        pipeline_version=pipeline_version,
        case_id=case_id,
    )
