from decimal import Decimal

import pytest

from drlf.ingestion.dmepos_supplier_service import (
    DMEPOS_SUPPLIER_SERVICE_COLUMNS,
    _row_values,
)
from drlf.sources.dmepos_supplier_service_manifest import REQUIRED_COLUMNS


def _source_row() -> dict[str, str]:
    row = dict.fromkeys(REQUIRED_COLUMNS, "")
    row.update(
        {
            "Suplr_NPI": "1234567890",
            "Suplr_Prvdr_Last_Name_Org": "Example Supply",
            "Suplr_Prvdr_Ent_Cd": "O",
            "Suplr_Prvdr_Spclty_Desc": "Other Medical Supply Company",
            "Suplr_Prvdr_Spclty_Srce": "DME",
            "HCPCS_Cd": "a6023",
            "HCPCS_Desc": "Collagen dressing, each",
            "Suplr_Rentl_Ind": "N",
            "Tot_Suplr_Benes": "",
            "Tot_Suplr_Clms": "20",
            "Tot_Suplr_Srvcs": "100.5",
            "Avg_Suplr_Sbmtd_Chrg": "260",
            "Avg_Suplr_Mdcr_Alowd_Amt": "250",
            "Avg_Suplr_Mdcr_Pymt_Amt": "200",
            "Avg_Suplr_Mdcr_Stdzd_Amt": "201",
        }
    )
    return row


def test_row_mapper_preserves_source_units_and_suppression() -> None:
    values = _row_values(_source_row(), 17, 2024)
    mapped = dict(zip(DMEPOS_SUPPLIER_SERVICE_COLUMNS, values, strict=True))

    assert mapped["source_release_id"] == 17
    assert mapped["data_year"] == 2024
    assert mapped["hcpcs_code"] == "A6023"
    assert mapped["total_beneficiaries"] is None
    assert mapped["total_services"] == Decimal("100.5")
    assert mapped["average_medicare_payment_amount"] == Decimal("200")


@pytest.mark.parametrize("marker", ["", "*", "#"])
def test_row_mapper_preserves_beneficiary_suppression_markers_as_null(marker: str) -> None:
    row = _source_row()
    row["Tot_Suplr_Benes"] = marker

    values = _row_values(row, 17, 2024)
    mapped = dict(zip(DMEPOS_SUPPLIER_SERVICE_COLUMNS, values, strict=True))

    assert mapped["total_beneficiaries"] is None


def test_row_mapper_rejects_nonfinite_and_negative_values() -> None:
    row = _source_row()
    row["Tot_Suplr_Srvcs"] = "NaN"
    with pytest.raises(ValueError, match="invalid Tot_Suplr_Srvcs"):
        _row_values(row, 17, 2024)

    row = _source_row()
    row["Tot_Suplr_Clms"] = "-1"
    with pytest.raises(ValueError, match="invalid Tot_Suplr_Clms"):
        _row_values(row, 17, 2024)
