from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterator, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from drlf.sources.dmepos_manifest import KNOWN_SUPPLIER_RELEASES
from drlf.sources.manifest import load_and_validate_manifest

DATASET_NAME = "Medicare Durable Medical Equipment, Devices & Supplies - by Supplier"
DATASET_SLUG = "medicare-durable-medical-equipment-devices-supplies-by-supplier"
DATASET_TYPE_UUID = "a2d56d3f-3531-4315-9d87-e29986516b41"
AGGREGATION_KEYS = ("Suplr_NPI",)
DATA_FILE_ROLE = "data"
DATA_FILE_MEDIA_TYPE = "text/csv"

SOURCE_COLUMNS = (
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

TABLE_COLUMNS = (
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
    "supplier_ruca",
    "supplier_ruca_description",
    "supplier_country",
    "supplier_specialty_description",
    "supplier_specialty_source",
    "total_hcpcs_codes",
    "total_beneficiaries",
    "total_claims",
    "total_services",
    "total_submitted_charge",
    "total_medicare_allowed_amount",
    "total_medicare_payment_amount",
    "total_medicare_standardized_payment_amount",
    "dme_suppression_indicator",
    "dme_total_hcpcs_codes",
    "dme_total_beneficiaries",
    "dme_total_claims",
    "dme_total_services",
    "dme_submitted_charge",
    "dme_medicare_allowed_amount",
    "dme_medicare_payment_amount",
    "dme_medicare_standardized_payment_amount",
    "pos_suppression_indicator",
    "pos_total_hcpcs_codes",
    "pos_total_beneficiaries",
    "pos_total_claims",
    "pos_total_services",
    "pos_submitted_charge",
    "pos_medicare_allowed_amount",
    "pos_medicare_payment_amount",
    "pos_medicare_standardized_payment_amount",
    "drug_suppression_indicator",
    "drug_total_hcpcs_codes",
    "drug_total_beneficiaries",
    "drug_total_claims",
    "drug_total_services",
    "drug_submitted_charge",
    "drug_medicare_allowed_amount",
    "drug_medicare_payment_amount",
    "drug_medicare_standardized_payment_amount",
    "beneficiary_average_age",
    "beneficiary_age_lt_65_count",
    "beneficiary_age_65_74_count",
    "beneficiary_age_75_84_count",
    "beneficiary_age_gt_84_count",
    "beneficiary_female_count",
    "beneficiary_male_count",
    "beneficiary_race_white_count",
    "beneficiary_race_black_count",
    "beneficiary_race_api_count",
    "beneficiary_race_hispanic_count",
    "beneficiary_race_native_indian_count",
    "beneficiary_race_other_count",
    "beneficiary_nondual_count",
    "beneficiary_dual_count",
    "beneficiary_adhd_other_conduct_disorder_percent",
    "beneficiary_alcohol_drug_use_disorder_percent",
    "tobacco_pct",
    "beneficiary_alzheimer_non_alzheimer_dementia_percent",
    "beneficiary_anxiety_disorder_percent",
    "beneficiary_bipolar_disorder_percent",
    "beneficiary_mood_disorder_percent",
    "beneficiary_depression_percent",
    "beneficiary_personality_disorder_percent",
    "beneficiary_ptsd_percent",
    "beneficiary_schizophrenia_other_psychotic_disorder_percent",
    "asthma_pct",
    "beneficiary_atrial_fibrillation_percent",
    "beneficiary_cancer_percent",
    "beneficiary_chronic_kidney_disease_percent",
    "copd_pct",
    "beneficiary_diabetes_percent",
    "beneficiary_heart_failure_non_ischemic_heart_disease_percent",
    "beneficiary_hyperlipidemia_percent",
    "beneficiary_hypertension_percent",
    "beneficiary_ischemic_heart_disease_percent",
    "beneficiary_osteoporosis_percent",
    "beneficiary_parkinson_disease_percent",
    "beneficiary_arthritis_percent",
    "beneficiary_stroke_tia_percent",
    "average_risk_score",
)

_NPI_PATTERN = re.compile(r"[0-9]{10}")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _required_text(row: dict[str, Any], field: str) -> str:
    value = _text(row.get(field))
    if value is None:
        raise ValueError(f"DMEPOS supplier row requires {field}")
    return value


def _integer(value: Any) -> int | None:
    normalized = _text(value)
    if normalized is None or normalized in {"*", "#"}:
        return None
    try:
        return int(normalized)
    except ValueError:
        raise ValueError(f"DMEPOS supplier value is not an integer: {normalized!r}") from None


def _required_integer(row: dict[str, Any], field: str) -> int:
    value = _integer(row.get(field))
    if value is None:
        raise ValueError(f"DMEPOS supplier row requires numeric {field}")
    return value


def _decimal(value: Any) -> Decimal | None:
    normalized = _text(value)
    if normalized is None or normalized in {"*", "#"}:
        return None
    try:
        number = Decimal(normalized)
    except InvalidOperation:
        raise ValueError(f"DMEPOS supplier value is not numeric: {normalized!r}") from None
    if not number.is_finite():
        raise ValueError(f"DMEPOS supplier value is not finite: {normalized!r}")
    return number


def _required_decimal(row: dict[str, Any], field: str) -> Decimal:
    value = _decimal(row.get(field))
    if value is None:
        raise ValueError(f"DMEPOS supplier row requires numeric {field}")
    return value


def _row_values(
    row: dict[str, Any],
    source_release_id: int,
    data_year: int,
) -> tuple[Any, ...]:
    """Map one supplier row without coercing identifiers or Decimal measures."""
    return (
        source_release_id,
        data_year,
        _required_text(row, "Suplr_NPI"),
        _text(row.get("Suplr_Prvdr_Last_Name_Org")),
        _text(row.get("Suplr_Prvdr_First_Name")),
        _text(row.get("Suplr_Prvdr_MI")),
        _text(row.get("Suplr_Prvdr_Crdntls")),
        _required_text(row, "Suplr_Prvdr_Ent_Cd"),
        _text(row.get("Suplr_Prvdr_St1")),
        _text(row.get("Suplr_Prvdr_St2")),
        _text(row.get("Suplr_Prvdr_City")),
        _text(row.get("Suplr_Prvdr_State_Abrvtn")),
        _text(row.get("Suplr_Prvdr_State_FIPS")),
        _text(row.get("Suplr_Prvdr_Zip5")),
        _text(row.get("Suplr_Prvdr_RUCA")),
        _text(row.get("Suplr_Prvdr_RUCA_Desc")),
        _text(row.get("Suplr_Prvdr_Cntry")),
        _text(row.get("Suplr_Prvdr_Spclty_Desc")),
        _text(row.get("Suplr_Prvdr_Spclty_Srce")),
        _required_integer(row, "Tot_Suplr_HCPCS_Cds"),
        _integer(row.get("Tot_Suplr_Benes")),
        _required_integer(row, "Tot_Suplr_Clms"),
        _required_decimal(row, "Tot_Suplr_Srvcs"),
        _required_decimal(row, "Suplr_Sbmtd_Chrgs"),
        _required_decimal(row, "Suplr_Mdcr_Alowd_Amt"),
        _required_decimal(row, "Suplr_Mdcr_Pymt_Amt"),
        _required_decimal(row, "Suplr_Mdcr_Stdzd_Pymt_Amt"),
        _text(row.get("DME_Sprsn_Ind")),
        _integer(row.get("DME_Tot_Suplr_HCPCS_Cds")),
        _integer(row.get("DME_Tot_Suplr_Benes")),
        _integer(row.get("DME_Tot_Suplr_Clms")),
        _decimal(row.get("DME_Tot_Suplr_Srvcs")),
        _decimal(row.get("DME_Suplr_Sbmtd_Chrgs")),
        _decimal(row.get("DME_Suplr_Mdcr_Alowd_Amt")),
        _decimal(row.get("DME_Suplr_Mdcr_Pymt_Amt")),
        _decimal(row.get("DME_Suplr_Mdcr_Stdzd_Pymt_Amt")),
        _text(row.get("POS_Sprsn_Ind")),
        _integer(row.get("POS_Tot_Suplr_HCPCS_Cds")),
        _integer(row.get("POS_Tot_Suplr_Benes")),
        _integer(row.get("POS_Tot_Suplr_Clms")),
        _decimal(row.get("POS_Tot_Suplr_Srvcs")),
        _decimal(row.get("POS_Suplr_Sbmtd_Chrgs")),
        _decimal(row.get("POS_Suplr_Mdcr_Alowd_Amt")),
        _decimal(row.get("POS_Suplr_Mdcr_Pymt_Amt")),
        _decimal(row.get("POS_Suplr_Mdcr_Stdzd_Pymt_Amt")),
        _text(row.get("Drug_Sprsn_Ind")),
        _integer(row.get("Drug_Tot_Suplr_HCPCS_Cds")),
        _integer(row.get("Drug_Tot_Suplr_Benes")),
        _integer(row.get("Drug_Tot_Suplr_Clms")),
        _decimal(row.get("Drug_Tot_Suplr_Srvcs")),
        _decimal(row.get("Drug_Suplr_Sbmtd_Chrgs")),
        _decimal(row.get("Drug_Suplr_Mdcr_Alowd_Amt")),
        _decimal(row.get("Drug_Suplr_Mdcr_Pymt_Amt")),
        _decimal(row.get("Drug_Suplr_Mdcr_Stdzd_Pymt_Amt")),
        _decimal(row.get("Bene_Avg_Age")),
        _integer(row.get("Bene_Age_LT_65_Cnt")),
        _integer(row.get("Bene_Age_65_74_Cnt")),
        _integer(row.get("Bene_Age_75_84_Cnt")),
        _integer(row.get("Bene_Age_GT_84_Cnt")),
        _integer(row.get("Bene_Feml_Cnt")),
        _integer(row.get("Bene_Male_Cnt")),
        _integer(row.get("Bene_Race_Wht_Cnt")),
        _integer(row.get("Bene_Race_Black_Cnt")),
        _integer(row.get("Bene_Race_Api_Cnt")),
        _integer(row.get("Bene_Race_Hspnc_Cnt")),
        _integer(row.get("Bene_Race_Natind_Cnt")),
        _integer(row.get("Bene_Race_Othr_Cnt")),
        _integer(row.get("Bene_Ndual_Cnt")),
        _integer(row.get("Bene_Dual_Cnt")),
        _decimal(row.get("Bene_CC_BH_ADHD_OthCD_V1_Pct")),
        _decimal(row.get("Bene_CC_BH_Alcohol_Drug_V1_Pct")),
        _decimal(row.get("Bene_CC_BH_Tobacco_V1_Pct")),
        _decimal(row.get("Bene_CC_BH_Alz_NonAlzdem_V2_Pct")),
        _decimal(row.get("Bene_CC_BH_Anxiety_V1_Pct")),
        _decimal(row.get("Bene_CC_BH_Bipolar_V1_Pct")),
        _decimal(row.get("Bene_CC_BH_Mood_V2_Pct")),
        _decimal(row.get("Bene_CC_BH_Depress_V1_Pct")),
        _decimal(row.get("Bene_CC_BH_PD_V1_Pct")),
        _decimal(row.get("Bene_CC_BH_PTSD_V1_Pct")),
        _decimal(row.get("Bene_CC_BH_Schizo_OthPsy_V1_Pct")),
        _decimal(row.get("Bene_CC_PH_Asthma_V2_Pct")),
        _decimal(row.get("Bene_CC_PH_Afib_V2_Pct")),
        _decimal(row.get("Bene_CC_PH_Cancer6_V2_Pct")),
        _decimal(row.get("Bene_CC_PH_CKD_V2_Pct")),
        _decimal(row.get("Bene_CC_PH_COPD_V2_Pct")),
        _decimal(row.get("Bene_CC_PH_Diabetes_V2_Pct")),
        _decimal(row.get("Bene_CC_PH_HF_NonIHD_V2_Pct")),
        _decimal(row.get("Bene_CC_PH_Hyperlipidemia_V2_Pct")),
        _decimal(row.get("Bene_CC_PH_Hypertension_V2_Pct")),
        _decimal(row.get("Bene_CC_PH_IschemicHeart_V2_Pct")),
        _decimal(row.get("Bene_CC_PH_Osteoporosis_V2_Pct")),
        _decimal(row.get("Bene_CC_PH_Parkinson_V2_Pct")),
        _decimal(row.get("Bene_CC_PH_Arthritis_V2_Pct")),
        _decimal(row.get("Bene_CC_PH_Stroke_TIA_V2_Pct")),
        _decimal(row.get("Bene_Avg_Risk_Scre")),
    )


def _relative_manifest_path(manifest_path: Path) -> str:
    try:
        relative = manifest_path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"Manifest is outside the repository: {manifest_path}") from error
    if not relative.startswith("research/data-manifests/"):
        raise ValueError("DMEPOS supplier manifests must be under research/data-manifests/")
    return relative


def _validate_files(files: Sequence[dict[str, Any]]) -> None:
    """Validate immutable inputs without materializing a complete file in memory."""
    for file_record in files:
        path = Path(file_record["relative_path"])
        if not path.is_file():
            raise FileNotFoundError(f"Manifest file is missing: {path}")
        if path.stat().st_size != file_record["bytes"]:
            raise ValueError(f"Manifest byte count does not match: {path}")
        digest = sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != file_record["sha256"]:
            raise ValueError(f"Manifest SHA-256 does not match: {path}")


def _validate_manifest_identity(manifest: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    dataset = manifest["dataset"]
    if dataset["dataset_id"] != DATASET_TYPE_UUID or dataset["slug"] != DATASET_SLUG:
        raise ValueError(f"Manifest is not for {DATASET_NAME}")
    if manifest["source"]["name"] != "Centers for Medicare & Medicaid Services":
        raise ValueError("DMEPOS supplier manifest has an unexpected source")
    if dataset["aggregation_keys"] != list(AGGREGATION_KEYS):
        raise ValueError("Manifest aggregation keys do not match supplier NPI grain")
    if manifest["status"] != "complete":
        raise ValueError("DMEPOS supplier loading requires a complete manifest")
    if manifest["retrieval"]["method"] != "download":
        raise ValueError("DMEPOS supplier loading requires a retained bulk CSV download")
    data_year = dataset["data_year"]
    if not isinstance(data_year, int) or not 2013 <= data_year <= 2200:
        raise ValueError("DMEPOS supplier manifests require a valid integer data_year")
    release = KNOWN_SUPPLIER_RELEASES.get(data_year)
    if release is None or dataset["version_id"] != release.version_id:
        raise ValueError("DMEPOS supplier manifest is not a pinned reviewed release")

    files = manifest["files"]
    data_files = [file_record for file_record in files if file_record["role"] == DATA_FILE_ROLE]
    if len(files) != 1 or len(data_files) != 1:
        raise ValueError("DMEPOS supplier manifests require exactly one data file")
    data_file = data_files[0]
    if data_file.get("media_type") != DATA_FILE_MEDIA_TYPE:
        raise ValueError("DMEPOS supplier data file must use text/csv media type")
    if data_file.get("compression") is not None:
        raise ValueError("DMEPOS supplier loader requires an extracted, uncompressed CSV")

    validation = manifest["validation"]
    expected_rows = validation.get("rows")
    if not isinstance(expected_rows, int) or expected_rows < 1:
        raise ValueError("DMEPOS supplier manifest requires a positive row count")
    if validation.get("aggregation_key_duplicates") != 0:
        raise ValueError("DMEPOS supplier manifest must report zero supplier NPI duplicates")
    return data_year, data_file


def _iter_supplier_rows(
    path: Path,
    *,
    file_record: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
) -> Iterator[dict[str, str | None]]:
    """Stream and validate one supplier CSV at its published NPI grain."""
    seen_npis: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames
        if fields is None:
            raise ValueError("DMEPOS supplier CSV is missing a header")
        if len(fields) != len(set(fields)):
            raise ValueError("DMEPOS supplier CSV has duplicate column names")
        missing = sorted(set(SOURCE_COLUMNS) - set(fields))
        unexpected = sorted(set(fields) - set(SOURCE_COLUMNS))
        if missing or unexpected:
            raise ValueError(
                "DMEPOS supplier CSV does not match the reviewed schema: "
                f"missing={missing}, unexpected={unexpected}"
            )

        fingerprint = sha256("\n".join(sorted(fields)).encode()).hexdigest()
        if file_record is not None:
            expected_fingerprint = file_record.get("schema_fingerprint")
            if expected_fingerprint != fingerprint:
                raise ValueError("DMEPOS supplier CSV schema fingerprint does not match manifest")
        if validation is not None and validation.get("columns") != len(fields):
            raise ValueError("DMEPOS supplier CSV column count does not match manifest")

        for row_number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"DMEPOS supplier CSV row {row_number} is malformed")
            npi = _required_text(row, "Suplr_NPI")
            if _NPI_PATTERN.fullmatch(npi) is None:
                raise ValueError(
                    f"DMEPOS supplier CSV row {row_number} has invalid supplier NPI {npi!r}"
                )
            if npi in seen_npis:
                raise ValueError(f"DMEPOS supplier CSV repeats supplier NPI {npi}")
            seen_npis.add(npi)
            yield row


def _register_source_release(
    connection: psycopg.Connection[Any],
    manifest: dict[str, Any],
    *,
    manifest_hash: str,
    manifest_relative_path: str,
    observed_at: datetime,
    data_year: int,
) -> int:
    dataset_id = connection.execute(
        """
        insert into metadata.source_dataset (source, slug, name, landing_page)
        values (%s, %s, %s, %s)
        on conflict (source, slug) do update
        set name = excluded.name, landing_page = excluded.landing_page
        returning dataset_id
        """,
        (
            manifest["source"]["name"],
            manifest["dataset"]["slug"],
            manifest["dataset"]["name"],
            manifest["documentation"]["landing_page"],
        ),
    ).fetchone()[0]

    existing_release = connection.execute(
        """
        select source_release_id, manifest_sha256
        from metadata.source_release
        where manifest_path = %s
        """,
        (manifest_relative_path,),
    ).fetchone()
    if existing_release:
        if existing_release[1].strip() != manifest_hash:
            raise ValueError(
                "Committed manifest content differs from the previously loaded manifest hash"
            )
        return int(existing_release[0])

    return int(
        connection.execute(
            """
            insert into metadata.source_release (
                dataset_id, data_year, version_id, published_at, modified_at,
                accessed_at, observed_at, population, aggregation_keys, suppression,
                exclusions, retrieval_filters, status, manifest_path, manifest_sha256
            )
            values (
                %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s,
                %s::jsonb, '{}'::jsonb, 'validated', %s, %s
            )
            returning source_release_id
            """,
            (
                dataset_id,
                data_year,
                manifest["dataset"]["version_id"],
                manifest["observation"]["published_at"],
                manifest["observation"]["modified_at"],
                manifest["observation"]["accessed_at"],
                observed_at,
                manifest["dataset"]["population"],
                json.dumps(manifest["dataset"]["aggregation_keys"]),
                manifest["semantics"]["suppression"],
                json.dumps(manifest["semantics"]["exclusions"]),
                manifest_relative_path,
                manifest_hash,
            ),
        ).fetchone()[0]
    )


def _register_release_files(
    connection: psycopg.Connection[Any],
    source_release_id: int,
    files: Sequence[dict[str, Any]],
    observed_at: datetime,
) -> None:
    for ordinal, file_record in enumerate(files, start=1):
        content_artifact_id = connection.execute(
            """
            insert into metadata.content_artifact (bytes, sha256, media_type)
            values (%s, %s, %s)
            on conflict (sha256) do update set sha256 = excluded.sha256
            returning content_artifact_id
            """,
            (file_record["bytes"], file_record["sha256"], file_record["media_type"]),
        ).fetchone()[0]
        connection.execute(
            """
            insert into metadata.source_release_file (
                source_release_id, content_artifact_id, relative_path, filename,
                role, ordinal, retrieved_at, validation
            )
            values (%s, %s, %s, %s, %s, %s, %s, '{}'::jsonb)
            on conflict (source_release_id, relative_path) do nothing
            """,
            (
                source_release_id,
                content_artifact_id,
                file_record["relative_path"],
                file_record["filename"],
                file_record["role"],
                ordinal,
                observed_at,
            ),
        )


def load_dmepos_supplier(
    database_url: str,
    manifest_path: Path,
    *,
    code_commit: str,
    pipeline_version: str,
    case_id: str | None = None,
) -> tuple[int, int]:
    """Stream one complete, immutable annual DMEPOS supplier CSV into PostgreSQL."""
    manifest, manifest_hash = load_and_validate_manifest(manifest_path)
    data_year, data_file = _validate_manifest_identity(manifest)
    manifest_relative_path = _relative_manifest_path(manifest_path)
    observed_at = datetime.fromisoformat(
        manifest["observation"]["observed_at"].replace("Z", "+00:00")
    )
    files = manifest["files"]
    _validate_files(files)

    with psycopg.connect(database_url) as connection, connection.transaction():
        source_release_id = _register_source_release(
            connection,
            manifest,
            manifest_hash=manifest_hash,
            manifest_relative_path=manifest_relative_path,
            observed_at=observed_at,
            data_year=data_year,
        )
        _register_release_files(connection, source_release_id, files, observed_at)

        ingestion_run_id = connection.execute(
            """
            insert into metadata.ingestion_run (
                source_release_id, case_id, pipeline_version, code_commit, status
            )
            values (%s, %s, %s, %s, 'running')
            returning ingestion_run_id
            """,
            (source_release_id, case_id, pipeline_version, code_commit),
        ).fetchone()[0]
        connection.execute(
            """
            insert into metadata.ingestion_run_file (
                ingestion_run_id, source_release_id, source_release_file_id, role, ordinal
            )
            select %s, source_release_id, source_release_file_id, role, ordinal
            from metadata.source_release_file
            where source_release_id = %s
            order by ordinal
            """,
            (ingestion_run_id, source_release_id),
        )

        connection.execute(
            "delete from claims.dmepos_supplier where source_release_id = %s",
            (source_release_id,),
        )
        copy_statement = sql.SQL("copy {} ({}) from stdin").format(
            sql.Identifier("claims", "dmepos_supplier"),
            sql.SQL(", ").join(map(sql.Identifier, TABLE_COLUMNS)),
        )
        loaded_rows = 0
        with connection.cursor().copy(copy_statement) as copy:
            for row in _iter_supplier_rows(
                Path(data_file["relative_path"]),
                file_record=data_file,
                validation=manifest["validation"],
            ):
                copy.write_row(_row_values(row, source_release_id, data_year))
                loaded_rows += 1

        expected_rows = manifest["validation"]["rows"]
        if loaded_rows != expected_rows:
            raise ValueError(
                f"Loaded row count {loaded_rows} does not match manifest row count {expected_rows}"
            )

        connection.execute(
            """
            insert into metadata.data_quality_result (
                ingestion_run_id, check_name, status, observed, details
            )
            values
                (%s, 'manifest-file-integrity', 'pass', %s::jsonb,
                 'The retained CSV matched its manifest byte count and SHA-256 hash.'),
                (%s, 'loaded-row-count', 'pass', %s::jsonb,
                 'Loaded rows matched the complete manifest validation count.'),
                (%s, 'dmepos-supplier-published-grain', 'pass', %s::jsonb,
                 'Rows retained the annual source-release and supplier-NPI grain.')
            """,
            (
                ingestion_run_id,
                json.dumps({"validated_files": len(files)}),
                ingestion_run_id,
                json.dumps({"expected": expected_rows, "loaded": loaded_rows}),
                ingestion_run_id,
                json.dumps(
                    {
                        "table": "claims.dmepos_supplier",
                        "aggregation_keys": list(AGGREGATION_KEYS),
                    }
                ),
            ),
        )
        connection.execute(
            """
            update metadata.ingestion_run
            set status = 'succeeded', completed_at = now(), source_rows = %s,
                loaded_rows = %s, rejected_rows = 0
            where ingestion_run_id = %s
            """,
            (loaded_rows, loaded_rows, ingestion_run_id),
        )
        connection.execute(
            "update metadata.source_release set status = 'loaded' where source_release_id = %s",
            (source_release_id,),
        )

    return source_release_id, loaded_rows
