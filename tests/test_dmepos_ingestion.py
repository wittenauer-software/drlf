from __future__ import annotations

import csv
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from drlf.ingestion.dmepos_supplier import (
    AGGREGATION_KEYS,
    DATASET_SLUG,
    DATASET_TYPE_UUID,
    SOURCE_COLUMNS,
    TABLE_COLUMNS,
    _iter_supplier_rows,
    _row_values,
    _validate_files,
    _validate_manifest_identity,
)
from drlf.sources.dmepos_manifest import EXPECTED_COLUMNS, KNOWN_SUPPLIER_RELEASES


def _supplier_row(npi: str = "1003000390") -> dict[str, str]:
    row = {column: "" for column in SOURCE_COLUMNS}
    row.update(
        {
            "Suplr_NPI": npi,
            "Suplr_Prvdr_Last_Name_Org": "Example Supplier",
            "Suplr_Prvdr_Ent_Cd": "O",
            "Suplr_Prvdr_State_Abrvtn": "IN",
            "Suplr_Prvdr_Zip5": "04603",
            "Suplr_Prvdr_Spclty_Desc": "General Surgery",
            "Suplr_Prvdr_Spclty_Srce": "Claim-Specialty",
            "Tot_Suplr_HCPCS_Cds": "14",
            "Tot_Suplr_Benes": "238",
            "Tot_Suplr_Clms": "244",
            "Tot_Suplr_Srvcs": "256.750",
            "Suplr_Sbmtd_Chrgs": "82895.123456789",
            "Suplr_Mdcr_Alowd_Amt": "63465.400000001",
            "Suplr_Mdcr_Pymt_Amt": "48799.370000002",
            "Suplr_Mdcr_Stdzd_Pymt_Amt": "48189.270000003",
            "DME_Sprsn_Ind": "*",
            "POS_Tot_Suplr_Srvcs": "256.125",
            "POS_Suplr_Mdcr_Pymt_Amt": "48799.370000002",
            "Bene_Avg_Age": "73.133891213",
            "Bene_Age_65_74_Cnt": "137",
            "Bene_Ndual_Cnt": "226",
            "Bene_Dual_Cnt": "12",
            "Bene_CC_BH_Tobacco_V1_Pct": "0.0714285714",
            "Bene_CC_PH_Asthma_V2_Pct": "0.1596638655",
            "Bene_CC_PH_COPD_V2_Pct": "0.1218487395",
            "Bene_CC_PH_Hypertension_V2_Pct": "1.1",
            "Bene_Avg_Risk_Scre": "0.8378870293",
        }
    )
    return row


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=SOURCE_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def test_row_mapping_preserves_identifiers_decimal_precision_and_suppression() -> None:
    values = _row_values(_supplier_row(), source_release_id=17, data_year=2024)
    mapped = dict(zip(TABLE_COLUMNS, values, strict=True))

    assert mapped["source_release_id"] == 17
    assert mapped["supplier_npi"] == "1003000390"
    assert mapped["supplier_zip5"] == "04603"
    assert mapped["total_services"] == Decimal("256.750")
    assert mapped["total_submitted_charge"] == Decimal("82895.123456789")
    assert mapped["total_medicare_payment_amount"] == Decimal("48799.370000002")
    assert mapped["dme_suppression_indicator"] == "*"
    assert mapped["dme_total_services"] is None
    assert mapped["pos_total_services"] == Decimal("256.125")
    assert mapped["tobacco_pct"] == Decimal("0.0714285714")
    assert mapped["beneficiary_hypertension_percent"] == Decimal("1.1")
    assert mapped["average_risk_score"] == Decimal("0.8378870293")


def test_row_mapping_treats_source_marked_optional_numeric_cells_as_unknown() -> None:
    row = _supplier_row()
    row["Tot_Suplr_Benes"] = ""
    row["DME_Tot_Suplr_Clms"] = "*"
    row["Bene_Age_65_74_Cnt"] = "#"

    mapped = dict(
        zip(TABLE_COLUMNS, _row_values(row, source_release_id=17, data_year=2024), strict=True)
    )

    assert mapped["total_beneficiaries"] is None
    assert mapped["dme_total_claims"] is None
    assert mapped["beneficiary_age_65_74_count"] is None


def test_loader_schema_matches_reviewed_acquisition_profile() -> None:
    assert DATASET_SLUG == ("medicare-durable-medical-equipment-devices-supplies-by-supplier")
    assert SOURCE_COLUMNS == EXPECTED_COLUMNS


def test_row_mapping_preserves_blank_specialty_as_null() -> None:
    row = _supplier_row()
    row["Suplr_Prvdr_Spclty_Desc"] = " "
    row["Suplr_Prvdr_Spclty_Srce"] = ""

    mapped = dict(
        zip(TABLE_COLUMNS, _row_values(row, source_release_id=17, data_year=2024), strict=True)
    )

    assert mapped["supplier_specialty_description"] is None
    assert mapped["supplier_specialty_source"] is None


def test_csv_reader_streams_valid_rows_and_rejects_duplicate_grain(tmp_path: Path) -> None:
    valid_path = tmp_path / "valid.csv"
    _write_csv(valid_path, [_supplier_row(), _supplier_row("2003000398")])

    rows = list(_iter_supplier_rows(valid_path))

    assert [row["Suplr_NPI"] for row in rows] == ["1003000390", "2003000398"]

    duplicate_path = tmp_path / "duplicate.csv"
    _write_csv(duplicate_path, [_supplier_row(), _supplier_row()])
    with pytest.raises(ValueError, match="repeats supplier NPI 1003000390"):
        list(_iter_supplier_rows(duplicate_path))


def test_csv_reader_validates_exact_header_fingerprint_and_column_count(tmp_path: Path) -> None:
    path = tmp_path / "supplier.csv"
    _write_csv(path, [_supplier_row()])
    fingerprint = sha256("\n".join(sorted(SOURCE_COLUMNS)).encode()).hexdigest()

    assert (
        len(
            list(
                _iter_supplier_rows(
                    path,
                    file_record={"schema_fingerprint": fingerprint},
                    validation={"columns": len(SOURCE_COLUMNS)},
                )
            )
        )
        == 1
    )
    with pytest.raises(ValueError, match="schema fingerprint"):
        list(
            _iter_supplier_rows(
                path,
                file_record={"schema_fingerprint": "0" * 64},
                validation={"columns": len(SOURCE_COLUMNS)},
            )
        )

    missing_path = tmp_path / "missing.csv"
    with missing_path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=SOURCE_COLUMNS[:-1],
            lineterminator="\n",
        )
        writer.writeheader()
    with pytest.raises(ValueError, match="does not match the reviewed schema"):
        list(_iter_supplier_rows(missing_path))

    unexpected_path = tmp_path / "unexpected.csv"
    with unexpected_path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=(*SOURCE_COLUMNS, "Unexpected_New_Field"),
            lineterminator="\n",
        )
        writer.writeheader()
    with pytest.raises(ValueError, match="Unexpected_New_Field"):
        list(_iter_supplier_rows(unexpected_path))


def test_csv_reader_rejects_a_short_malformed_row(tmp_path: Path) -> None:
    path = tmp_path / "short.csv"
    path.write_text(",".join(SOURCE_COLUMNS) + "\n1003000390\n", encoding="utf-8")

    with pytest.raises(ValueError, match="row 2 is malformed"):
        list(_iter_supplier_rows(path))


def test_file_integrity_validation_hashes_in_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "supplier.csv"
    path.write_bytes(b"header\nvalue\n")

    def fail_read_bytes(_path: Path) -> bytes:
        raise AssertionError("whole-file reads are not allowed")

    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)
    _validate_files(
        [
            {
                "relative_path": path.as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256(b"header\nvalue\n").hexdigest(),
            }
        ]
    )


def test_manifest_identity_requires_complete_uncompressed_supplier_csv() -> None:
    manifest = {
        "status": "complete",
        "source": {"name": "Centers for Medicare & Medicaid Services"},
        "dataset": {
            "dataset_id": DATASET_TYPE_UUID,
            "slug": DATASET_SLUG,
            "aggregation_keys": list(AGGREGATION_KEYS),
            "version_id": KNOWN_SUPPLIER_RELEASES[2024].version_id,
            "data_year": 2024,
        },
        "retrieval": {"method": "download"},
        "files": [
            {
                "role": "data",
                "media_type": "text/csv",
                "compression": None,
            }
        ],
        "validation": {
            "rows": 10,
            "aggregation_key_duplicates": 0,
        },
    }

    assert _validate_manifest_identity(manifest)[0] == 2024

    empty = {
        **manifest,
        "validation": {
            **manifest["validation"],
            "rows": 0,
        },
    }
    with pytest.raises(ValueError, match="positive row count"):
        _validate_manifest_identity(empty)

    invalid = {**manifest, "retrieval": {"method": "api"}}
    with pytest.raises(ValueError, match="bulk CSV download"):
        _validate_manifest_identity(invalid)

    invalid = {
        **manifest,
        "files": [
            *manifest["files"],
            {
                "role": "documentation",
                "media_type": "application/pdf",
                "compression": None,
            },
        ],
    }
    with pytest.raises(ValueError, match="exactly one data file"):
        _validate_manifest_identity(invalid)


def test_manifest_identity_requires_reviewed_cms_source_and_annual_release() -> None:
    manifest = {
        "status": "complete",
        "source": {"name": "Centers for Medicare & Medicaid Services"},
        "dataset": {
            "dataset_id": DATASET_TYPE_UUID,
            "slug": DATASET_SLUG,
            "aggregation_keys": list(AGGREGATION_KEYS),
            "version_id": KNOWN_SUPPLIER_RELEASES[2024].version_id,
            "data_year": 2024,
        },
        "retrieval": {"method": "download"},
        "files": [{"role": "data", "media_type": "text/csv", "compression": None}],
        "validation": {"rows": 10, "aggregation_key_duplicates": 0},
    }

    wrong_source = {**manifest, "source": {"name": "Example CMS Mirror"}}
    with pytest.raises(ValueError, match="unexpected source"):
        _validate_manifest_identity(wrong_source)

    wrong_version = {
        **manifest,
        "dataset": {**manifest["dataset"], "version_id": KNOWN_SUPPLIER_RELEASES[2023].version_id},
    }
    with pytest.raises(ValueError, match="pinned reviewed release"):
        _validate_manifest_identity(wrong_version)

    unreviewed_year = {
        **manifest,
        "dataset": {**manifest["dataset"], "data_year": 2021},
    }
    with pytest.raises(ValueError, match="pinned reviewed release"):
        _validate_manifest_identity(unreviewed_year)
