from __future__ import annotations

import json
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from drlf.ingestion import part_b_common
from drlf.ingestion.part_b_common import (
    PartBLoadSpec,
    _page_rows,
    _validate_files,
    _validate_manifest_identity,
)
from drlf.ingestion.part_b_provider import (
    PROVIDER_COLUMNS,
)
from drlf.ingestion.part_b_provider import (
    _row_values as provider_row_values,
)
from drlf.ingestion.part_b_provider_service import (
    PROVIDER_SERVICE_COLUMNS,
)
from drlf.ingestion.part_b_provider_service import (
    _row_values as provider_service_row_values,
)


def _provider_row() -> dict[str, str]:
    return {
        "Rndrng_NPI": "1234567890",
        "Rndrng_Prvdr_Last_Org_Name": "Example",
        "Rndrng_Prvdr_First_Name": "Allergist",
        "Rndrng_Prvdr_MI": "Q",
        "Rndrng_Prvdr_Crdntls": "M.D.",
        "Rndrng_Prvdr_Ent_Cd": "I",
        "Rndrng_Prvdr_St1": "100 Example St",
        "Rndrng_Prvdr_City": "Example",
        "Rndrng_Prvdr_State_Abrvtn": "IL",
        "Rndrng_Prvdr_State_FIPS": "17",
        "Rndrng_Prvdr_Zip5": "60601",
        "Rndrng_Prvdr_Cntry": "US",
        "Rndrng_Prvdr_Type": "Allergy/ Immunology",
        "Rndrng_Prvdr_Mdcr_Prtcptg_Ind": "Y",
        "Tot_HCPCS_Cds": "17",
        "Tot_Benes": "123",
        "Tot_Srvcs": "456.750",
        "Tot_Sbmtd_Chrg": "10000.125",
        "Tot_Mdcr_Alowd_Amt": "7000.375",
        "Tot_Mdcr_Pymt_Amt": "5400.625",
        "Tot_Mdcr_Stdzd_Amt": "5300.875",
        "Drug_Sprsn_Ind": "*",
        "Drug_Tot_Srvcs": "",
        "Bene_CC_BH_Tobacco_V1_Pct": "12.5",
        "Bene_CC_PH_Asthma_V2_Pct": "44.4",
        "Bene_CC_PH_COPD_V2_Pct": "7.1",
        "Bene_Avg_Risk_Scre": "1.23456",
    }


def _provider_service_row() -> dict[str, str]:
    return {
        "Rndrng_NPI": "1234567890",
        "Rndrng_Prvdr_Last_Org_Name": "Example",
        "Rndrng_Prvdr_First_Name": "Allergist",
        "Rndrng_Prvdr_MI": "Q",
        "Rndrng_Prvdr_Crdntls": "M.D.",
        "Rndrng_Prvdr_Ent_Cd": "I",
        "Rndrng_Prvdr_St1": "100 Example St",
        "Rndrng_Prvdr_City": "Example",
        "Rndrng_Prvdr_State_Abrvtn": "IL",
        "Rndrng_Prvdr_State_FIPS": "17",
        "Rndrng_Prvdr_Zip5": "60601",
        "Rndrng_Prvdr_Cntry": "US",
        "Rndrng_Prvdr_Type": "Allergy/ Immunology",
        "Rndrng_Prvdr_Mdcr_Prtcptg_Ind": "Y",
        "HCPCS_Cd": "TSTAB",
        "HCPCS_Desc": "Synthetic test service B",
        "HCPCS_Drug_Ind": "N",
        "Place_Of_Srvc": "O",
        "Tot_Benes": "13",
        "Tot_Srvcs": "13.750",
        "Tot_Bene_Day_Srvcs": "13.250",
        "Avg_Sbmtd_Chrg": "100.123456",
        "Avg_Mdcr_Alowd_Amt": "37.595384615",
        "Avg_Mdcr_Pymt_Amt": "27.644615385",
        "Avg_Mdcr_Stdzd_Amt": "28.123456789",
    }


def test_provider_mapping_preserves_units_money_and_context() -> None:
    values = provider_row_values(_provider_row(), source_release_id=11, data_year=2024)
    mapped = dict(zip(PROVIDER_COLUMNS, values, strict=True))

    assert mapped["rendering_npi"] == "1234567890"
    assert mapped["entity_code"] == "I"
    assert mapped["provider_type"] == "Allergy/ Immunology"
    assert mapped["total_services"] == Decimal("456.750")
    assert mapped["total_submitted_charge"] == Decimal("10000.125")
    assert mapped["total_medicare_allowed_amount"] == Decimal("7000.375")
    assert mapped["total_medicare_payment_amount"] == Decimal("5400.625")
    assert mapped["drug_total_services"] is None
    assert mapped["tobacco_pct"] == Decimal("12.5")
    assert mapped["asthma_pct"] == Decimal("44.4")
    assert mapped["copd_pct"] == Decimal("7.1")
    assert mapped["average_risk_score"] == Decimal("1.23456")


def test_provider_service_mapping_preserves_fractional_service_units() -> None:
    values = provider_service_row_values(
        _provider_service_row(), source_release_id=12, data_year=2024
    )
    mapped = dict(zip(PROVIDER_SERVICE_COLUMNS, values, strict=True))

    assert mapped["hcpcs_code"] == "TSTAB"
    assert mapped["place_of_service"] == "O"
    assert mapped["total_services"] == Decimal("13.750")
    assert mapped["total_beneficiary_day_services"] == Decimal("13.250")
    assert mapped["average_submitted_charge"] == Decimal("100.123456")
    assert mapped["average_medicare_allowed_amount"] == Decimal("37.595384615")
    assert mapped["average_medicare_payment_amount"] == Decimal("27.644615385")
    assert mapped["average_medicare_standardized_amount"] == Decimal("28.123456789")


def test_provider_service_mapping_requires_published_numeric_measures() -> None:
    row = _provider_service_row()
    row["Tot_Srvcs"] = ""

    with pytest.raises(ValueError, match="numeric Tot_Srvcs"):
        provider_service_row_values(row, source_release_id=12, data_year=2024)


def _spec() -> PartBLoadSpec:
    def mapper(row: dict[str, Any], release_id: int, year: int) -> tuple[Any, ...]:
        return (release_id, year, row)

    return PartBLoadSpec(
        dataset_label="Test Part B dataset",
        dataset_slug="test-part-b",
        dataset_type_uuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        aggregation_keys=("Rndrng_NPI",),
        table_name="test_part_b",
        columns=("source_release_id", "data_year", "row"),
        row_mapper=mapper,
    )


def _manifest() -> dict[str, Any]:
    return {
        "dataset": {
            "dataset_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "slug": "test-part-b",
            "aggregation_keys": ["Rndrng_NPI"],
            "data_year": 2024,
        },
        "retrieval": {
            "method": "api",
            "parameters": {"page_size": 500, "pagination": "offset"},
        },
        "files": [{"role": "api-page", "bytes": 2}],
    }


def test_manifest_identity_requires_exact_source_grain_and_bounded_pages() -> None:
    data_year, pages = _validate_manifest_identity(_manifest(), _spec())

    assert data_year == 2024
    assert len(pages) == 1

    wrong_grain = _manifest()
    wrong_grain["dataset"]["aggregation_keys"] = ["Rndrng_NPI", "HCPCS_Cd"]
    with pytest.raises(ValueError, match="source grain"):
        _validate_manifest_identity(wrong_grain, _spec())

    unbounded = _manifest()
    unbounded["retrieval"]["parameters"]["pagination"] = None
    with pytest.raises(ValueError, match="bounded offset pagination"):
        _validate_manifest_identity(unbounded, _spec())


def test_page_reader_and_integrity_validation_are_file_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page_path = tmp_path / "page.json"
    body = json.dumps([{"Rndrng_NPI": "1234567890"}]).encode()
    page_path.write_bytes(body)
    file_record = {
        "relative_path": page_path.as_posix(),
        "bytes": len(body),
        "sha256": sha256(body).hexdigest(),
    }

    _validate_files([file_record])
    assert list(_page_rows(page_path)) == [{"Rndrng_NPI": "1234567890"}]

    with pytest.raises(ValueError, match="SHA-256"):
        _validate_files([{**file_record, "sha256": "0" * 64}])

    monkeypatch.setattr(part_b_common, "MAX_API_PAGE_BYTES", len(body) - 1)
    with pytest.raises(ValueError, match="64 MiB"):
        list(_page_rows(page_path))
