from __future__ import annotations

from pathlib import Path
from typing import Any

from drlf.ingestion.part_b_common import (
    PartBLoadSpec,
    load_part_b_pages,
    required_decimal,
    required_integer,
    required_text,
    text,
)
from drlf.sources.part_b_provider_service_manifest import (
    AGGREGATION_KEYS,
    DATASET_SLUG,
    DATASET_TYPE_UUID,
)

PROVIDER_SERVICE_COLUMNS = (
    "source_release_id",
    "data_year",
    "rendering_npi",
    "provider_last_org_name",
    "provider_first_name",
    "provider_middle_initial",
    "provider_credentials",
    "entity_code",
    "provider_address_line1",
    "provider_address_line2",
    "provider_city",
    "provider_state",
    "provider_state_fips",
    "provider_zip5",
    "provider_ruca",
    "provider_ruca_description",
    "provider_country",
    "provider_type",
    "medicare_participation_indicator",
    "hcpcs_code",
    "hcpcs_description",
    "hcpcs_drug_indicator",
    "place_of_service",
    "total_beneficiaries",
    "total_services",
    "total_beneficiary_day_services",
    "average_submitted_charge",
    "average_medicare_allowed_amount",
    "average_medicare_payment_amount",
    "average_medicare_standardized_amount",
)


def _row_values(
    row: dict[str, Any],
    source_release_id: int,
    data_year: int,
) -> tuple[Any, ...]:
    """Map one published NPI/HCPCS/place-of-service row without rounding units."""
    return (
        source_release_id,
        data_year,
        required_text(row, "Rndrng_NPI"),
        text(row.get("Rndrng_Prvdr_Last_Org_Name")),
        text(row.get("Rndrng_Prvdr_First_Name")),
        text(row.get("Rndrng_Prvdr_MI")),
        text(row.get("Rndrng_Prvdr_Crdntls")),
        required_text(row, "Rndrng_Prvdr_Ent_Cd"),
        text(row.get("Rndrng_Prvdr_St1")),
        text(row.get("Rndrng_Prvdr_St2")),
        text(row.get("Rndrng_Prvdr_City")),
        text(row.get("Rndrng_Prvdr_State_Abrvtn")),
        text(row.get("Rndrng_Prvdr_State_FIPS")),
        text(row.get("Rndrng_Prvdr_Zip5")),
        text(row.get("Rndrng_Prvdr_RUCA")),
        text(row.get("Rndrng_Prvdr_RUCA_Desc")),
        text(row.get("Rndrng_Prvdr_Cntry")),
        required_text(row, "Rndrng_Prvdr_Type"),
        required_text(row, "Rndrng_Prvdr_Mdcr_Prtcptg_Ind"),
        required_text(row, "HCPCS_Cd"),
        required_text(row, "HCPCS_Desc"),
        required_text(row, "HCPCS_Drug_Ind"),
        required_text(row, "Place_Of_Srvc"),
        required_integer(row, "Tot_Benes"),
        required_decimal(row, "Tot_Srvcs"),
        required_decimal(row, "Tot_Bene_Day_Srvcs"),
        required_decimal(row, "Avg_Sbmtd_Chrg"),
        required_decimal(row, "Avg_Mdcr_Alowd_Amt"),
        required_decimal(row, "Avg_Mdcr_Pymt_Amt"),
        required_decimal(row, "Avg_Mdcr_Stdzd_Amt"),
    )


_LOAD_SPEC = PartBLoadSpec(
    dataset_label="CMS Medicare Physician & Other Practitioners by Provider and Service",
    dataset_slug=DATASET_SLUG,
    dataset_type_uuid=DATASET_TYPE_UUID,
    aggregation_keys=AGGREGATION_KEYS,
    table_name="part_b_provider_service",
    columns=PROVIDER_SERVICE_COLUMNS,
    row_mapper=_row_values,
)


def load_part_b_provider_service(
    database_url: str,
    manifest_path: Path,
    *,
    code_commit: str,
    pipeline_version: str,
    case_id: str | None = None,
) -> tuple[int, int]:
    """Load a complete retained Part B provider-and-service API manifest."""
    return load_part_b_pages(
        database_url,
        manifest_path,
        spec=_LOAD_SPEC,
        code_commit=code_commit,
        pipeline_version=pipeline_version,
        case_id=case_id,
    )
