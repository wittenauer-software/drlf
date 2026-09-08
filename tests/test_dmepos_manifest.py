from __future__ import annotations

import csv
import shutil
from dataclasses import replace
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from drlf.sources.cms_file import CmsFileDownload
from drlf.sources.dmepos_manifest import (
    DATASET_SLUG,
    EXPECTED_COLUMNS,
    KNOWN_SUPPLIER_RELEASES,
    DmeposSupplierRelease,
    build_dmepos_supplier_manifest,
    get_dmepos_supplier_release,
    profile_dmepos_supplier_csv,
)


def _row(npi: str = "1234567890") -> dict[str, str]:
    row = {column: "" for column in EXPECTED_COLUMNS}
    row.update(
        {
            "Suplr_NPI": npi,
            "Suplr_Prvdr_Last_Name_Org": "Example Supplier",
            "Suplr_Prvdr_Ent_Cd": "O",
            "Suplr_Prvdr_State_Abrvtn": "TX",
            "Suplr_Prvdr_Cntry": "US",
            "Suplr_Prvdr_Spclty_Desc": "Medical Supply Company",
            "Suplr_Prvdr_Spclty_Srce": "Claim-Specialty",
            "Tot_Suplr_HCPCS_Cds": "4",
            "Tot_Suplr_Benes": "100",
            "Tot_Suplr_Clms": "150",
            "Tot_Suplr_Srvcs": "175.5",
            "Suplr_Sbmtd_Chrgs": "250000.00",
            "Suplr_Mdcr_Alowd_Amt": "125000.00",
            "Suplr_Mdcr_Pymt_Amt": "100000.00",
            "Suplr_Mdcr_Stdzd_Pymt_Amt": "99000.00",
            "DME_Sprsn_Ind": "",
            "DME_Tot_Suplr_HCPCS_Cds": "4",
            "DME_Tot_Suplr_Benes": "100",
            "DME_Tot_Suplr_Clms": "150",
            "DME_Tot_Suplr_Srvcs": "175.5",
            "DME_Suplr_Sbmtd_Chrgs": "250000.00",
            "DME_Suplr_Mdcr_Alowd_Amt": "125000.00",
            "DME_Suplr_Mdcr_Pymt_Amt": "100000.00",
            "DME_Suplr_Mdcr_Stdzd_Pymt_Amt": "99000.00",
            "POS_Sprsn_Ind": "*",
            "Drug_Sprsn_Ind": "#",
            "Bene_Avg_Age": "74",
            "Bene_Avg_Risk_Scre": "1.25",
        }
    )
    return row


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=EXPECTED_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _release(path: Path, *, rows: int) -> DmeposSupplierRelease:
    return DmeposSupplierRelease(
        data_year=2024,
        version_id="4c6cfc68-a149-4bfe-b934-db2b92d0f360",
        download_url=f"https://data.cms.gov/sites/default/files/2026-07/example/{path.name}",
        filename=path.name,
        modified_at=date(2026, 7, 23),
        expected_bytes=path.stat().st_size,
        expected_rows=rows,
    )


def _download(path: Path, release: DmeposSupplierRelease) -> CmsFileDownload:
    body_sha256 = sha256(path.read_bytes()).hexdigest()
    return CmsFileDownload(
        path=path,
        request_url=release.download_url,
        final_url=release.download_url,
        bytes=path.stat().st_size,
        sha256=body_sha256,
        media_type="text/csv",
        compression=None,
        resumed_from=0,
        etag='"release"',
        last_modified="Thu, 23 Jul 2026 00:00:00 GMT",
        max_bytes=1024 * 1024,
        preflight_total_bytes=path.stat().st_size,
        preflight_size_source="content-range",
        disk_reserve_bytes=64 * 1024 * 1024,
    )


def _temporary_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repository = tmp_path / "repository"
    schema_dir = repository / "research" / "data-manifests"
    schema_dir.mkdir(parents=True)
    source_schema = (
        Path(__file__).parents[1] / "research" / "data-manifests" / "source-manifest.schema.json"
    )
    shutil.copy2(source_schema, schema_dir / source_schema.name)
    monkeypatch.chdir(repository)
    return repository


@pytest.mark.parametrize(
    ("year", "version_id", "rows", "file_bytes"),
    [
        (2022, "471f9c0d-12c4-4601-a547-559dcfa2d0e2", 66_406, 35_107_995),
        (2023, "7bb52a09-9eba-43f9-b7ec-57f65fbbc86c", 63_988, 33_493_463),
        (2024, "4c6cfc68-a149-4bfe-b934-db2b92d0f360", 60_060, 31_326_606),
    ],
)
def test_reviewed_releases_pin_year_version_url_and_resource_counts(
    year: int,
    version_id: str,
    rows: int,
    file_bytes: int,
) -> None:
    release = get_dmepos_supplier_release(year)

    assert release is KNOWN_SUPPLIER_RELEASES[year]
    assert release.version_id == version_id
    assert release.expected_rows == rows
    assert release.expected_bytes == file_bytes
    assert release.download_url.startswith("https://data.cms.gov/sites/default/files/")
    assert release.download_url.endswith(release.filename)
    assert f"dy{year % 100:02d}_supr.csv" in release.filename


def test_profile_streams_integrity_and_validates_one_row_per_supplier(tmp_path: Path) -> None:
    raw_path = tmp_path / "mup_dme_ry26_p05_v10_dy24_supr.csv"
    _write_csv(raw_path, [_row(), _row("9876543210")])
    digest = sha256(raw_path.read_bytes()).hexdigest()

    profile = profile_dmepos_supplier_csv(
        raw_path,
        expected_bytes=raw_path.stat().st_size,
        expected_sha256=digest,
        chunk_bytes=17,
    )

    assert profile["rows"] == 2
    assert profile["column_count"] == len(EXPECTED_COLUMNS) == 93
    assert profile["aggregation_key_duplicates"] == 0
    assert profile["sha256"] == digest


def test_profile_preserves_source_marked_optional_numeric_suppression(tmp_path: Path) -> None:
    raw_path = tmp_path / "suppressed.csv"
    row = _row()
    row["DME_Tot_Suplr_Clms"] = "*"
    row["Bene_Age_65_74_Cnt"] = "#"
    row["Tot_Suplr_Benes"] = ""
    _write_csv(raw_path, [row])

    profile = profile_dmepos_supplier_csv(raw_path)

    assert profile["suppressed_numeric_values"] == 2
    assert profile["unknown_total_beneficiary_values"] == 1


def test_profile_rejects_duplicate_key_bad_numeric_and_header(tmp_path: Path) -> None:
    duplicate_path = tmp_path / "duplicate.csv"
    _write_csv(duplicate_path, [_row(), _row()])
    with pytest.raises(ValueError, match="duplicate_keys=1"):
        profile_dmepos_supplier_csv(duplicate_path)

    numeric_path = tmp_path / "numeric.csv"
    invalid = _row()
    invalid["Suplr_Mdcr_Pymt_Amt"] = "not-a-number"
    _write_csv(numeric_path, [invalid])
    with pytest.raises(ValueError, match="invalid_numeric_values=1"):
        profile_dmepos_supplier_csv(numeric_path)

    header_path = tmp_path / "header.csv"
    with header_path.open("w", encoding="utf-8", newline="") as stream:
        columns = EXPECTED_COLUMNS[:-1]
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerow({column: _row()[column] for column in columns})
    with pytest.raises(ValueError, match="header does not match"):
        profile_dmepos_supplier_csv(header_path)


@pytest.mark.parametrize(
    ("column", "value", "reason"),
    [
        ("Suplr_Mdcr_Pymt_Amt", "-0.01", "negative"),
        ("Tot_Suplr_Clms", "12.5", "fractional count"),
        ("Bene_Avg_Age", "121", "average age outside 0..120"),
    ],
)
def test_profile_rejects_out_of_domain_numeric_values(
    tmp_path: Path,
    column: str,
    value: str,
    reason: str,
) -> None:
    raw_path = tmp_path / f"invalid-{column}.csv"
    row = _row()
    row[column] = value
    _write_csv(raw_path, [row])

    with pytest.raises(ValueError, match=reason):
        profile_dmepos_supplier_csv(raw_path)


def test_profile_retains_but_flags_source_percentage_above_one(tmp_path: Path) -> None:
    raw_path = tmp_path / "source-percentage.csv"
    row = _row()
    row["Bene_CC_PH_Asthma_V2_Pct"] = "1.01"
    _write_csv(raw_path, [row])

    profile = profile_dmepos_supplier_csv(raw_path)

    assert profile["out_of_range_percentage_values"] == 1
    assert profile["out_of_range_percentage_examples"] == ["Bene_CC_PH_Asthma_V2_Pct='1.01'"]


def test_profile_rejects_acquisition_hash_or_size_mismatch(tmp_path: Path) -> None:
    raw_path = tmp_path / "source.csv"
    _write_csv(raw_path, [_row()])

    with pytest.raises(ValueError, match="size mismatch"):
        profile_dmepos_supplier_csv(raw_path, expected_bytes=raw_path.stat().st_size + 1)
    with pytest.raises(ValueError, match="SHA-256"):
        profile_dmepos_supplier_csv(raw_path, expected_sha256="0" * 64)


def test_complete_manifest_preserves_payment_roles_release_and_caps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _temporary_repository(tmp_path, monkeypatch)
    raw_path = Path(
        "data/raw/cms/medicare-dmepos-by-supplier/2024/release/mup_dme_ry26_p05_v10_dy24_supr.csv"
    )
    _write_csv(raw_path, [_row()])
    release = _release(raw_path, rows=1)
    download = _download(raw_path, release)
    manifest_path = Path("research/data-manifests/dmepos-supplier-2024.json")

    manifest = build_dmepos_supplier_manifest(
        download,
        manifest_path,
        release=release,
        observed_at=datetime(2026, 9, 2, 12, tzinfo=UTC),
    )

    assert manifest["status"] == "complete"
    assert manifest["dataset"]["slug"] == DATASET_SLUG  # type: ignore[index]
    assert manifest["dataset"]["version_id"] == release.version_id  # type: ignore[index]
    assert manifest["retrieval"]["request_url"] == release.download_url  # type: ignore[index]
    assert manifest["retrieval"]["parameters"]["max_download_bytes"] == 1024 * 1024  # type: ignore[index]
    assert manifest["retrieval"]["parameters"]["disk_reserve_bytes"] == 64 * 1024 * 1024  # type: ignore[index]
    assert manifest["retrieval"]["parameters"]["version_resources_api"].endswith(  # type: ignore[index]
        release.version_id
    )
    monetary = manifest["semantics"]["monetary_fields"]  # type: ignore[index]
    assert "after deductible and coinsurance" in monetary["Suplr_Mdcr_Pymt_Amt"]
    assert "not an estimate of improper payment" in monetary["Suplr_Mdcr_Pymt_Amt"]
    assert "beneficiary deductible" in monetary["Suplr_Mdcr_Alowd_Amt"]
    assert manifest["validation"]["rows"] == 1  # type: ignore[index]
    assert manifest_path.exists()

    with pytest.raises(FileExistsError, match="Refusing to replace"):
        build_dmepos_supplier_manifest(
            download,
            manifest_path,
            release=release,
            observed_at=datetime(2026, 9, 2, 12, tzinfo=UTC),
        )


def test_manifest_rejects_wrong_release_url_year_and_tampered_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _temporary_repository(tmp_path, monkeypatch)
    raw_path = Path(
        "data/raw/cms/medicare-dmepos-by-supplier/2024/release/mup_dme_ry26_p05_v10_dy24_supr.csv"
    )
    _write_csv(raw_path, [_row()])
    release = _release(raw_path, rows=1)
    download = _download(raw_path, release)
    manifest_path = Path("research/data-manifests/dmepos-supplier-2024.json")

    with pytest.raises(ValueError, match="download URL differs"):
        build_dmepos_supplier_manifest(
            replace(download, request_url="https://data.cms.gov/different.csv"),
            manifest_path,
            release=release,
            observed_at=datetime(2026, 9, 2, 12, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="encode the declared data year"):
        build_dmepos_supplier_manifest(
            download,
            manifest_path,
            release=replace(release, data_year=2023),
            observed_at=datetime(2026, 9, 2, 12, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="SHA-256"):
        build_dmepos_supplier_manifest(
            replace(download, sha256="0" * 64),
            manifest_path,
            release=release,
            observed_at=datetime(2026, 9, 2, 12, tzinfo=UTC),
        )
