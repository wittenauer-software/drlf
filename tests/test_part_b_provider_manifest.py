import json
import shutil
from hashlib import sha256
from pathlib import Path

import pytest

from drlf.sources.part_b_provider_manifest import (
    DATASET_TYPE_UUID,
    REQUIRED_COLUMNS,
    build_part_b_provider_api_manifest,
    profile_provider_api_pages,
)

VERSION_UUID = "4d0b2df0-1e99-4db7-a574-a571d99217f1"


def _row(*, npi: str = "1234567890") -> dict[str, str]:
    row = {column: "" for column in REQUIRED_COLUMNS}
    row.update(
        {
            "Rndrng_NPI": npi,
            "Rndrng_Prvdr_Last_Org_Name": "Example",
            "Rndrng_Prvdr_First_Name": "Person",
            "Rndrng_Prvdr_Ent_Cd": "I",
            "Rndrng_Prvdr_State_Abrvtn": "MD",
            "Rndrng_Prvdr_Type": "Allergy/ Immunology",
            "Tot_HCPCS_Cds": "10",
            "Tot_Benes": "100",
            "Tot_Srvcs": "200",
            "Tot_Sbmtd_Chrg": "10000",
            "Tot_Mdcr_Alowd_Amt": "3000",
            "Tot_Mdcr_Pymt_Amt": "2400",
            "Tot_Mdcr_Stdzd_Amt": "2350",
            "Drug_Sprsn_Ind": "*",
            "Med_Sprsn_Ind": "#",
            "Bene_CC_BH_Tobacco_V1_Pct": "18",
            "Bene_CC_PH_Asthma_V2_Pct": "12",
            "Bene_CC_PH_COPD_V2_Pct": "9",
            "Bene_Avg_Risk_Scre": "1.1",
        }
    )
    return row


def _write_inventory(
    root: Path,
    rows: list[dict[str, str]],
    *,
    filters: dict[str, str] | None = None,
    dataset_uuid: str = VERSION_UUID,
) -> Path:
    snapshot_dir = root / "data/raw/cms/part-b-provider/2024/observed"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    body = json.dumps(rows, separators=(",", ":")).encode()
    page_path = snapshot_dir / "page-00001-offset-000000000.json"
    page_path.write_bytes(body)
    inventory = {
        "inventory_version": 1,
        "dataset_uuid": dataset_uuid,
        "observed_at": "2026-09-02T12:00:00Z",
        "filters": filters or {"Rndrng_NPI": "1234567890"},
        "page_size": 5_000,
        "max_pages": 1,
        "total_rows": len(rows),
        "pages": [
            {
                "ordinal": 1,
                "offset": 0,
                "rows": len(rows),
                "url": (
                    "https://data.cms.gov/data-api/v1/dataset/"
                    f"{dataset_uuid}/data?size=5000&offset=0"
                ),
                "relative_filename": page_path.name,
                "bytes": len(body),
                "sha256": sha256(body).hexdigest(),
                "media_type": "application/json",
            }
        ],
    }
    inventory_path = snapshot_dir / "inventory.json"
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    return inventory_path


def test_profile_validates_current_provider_grain_and_columns(tmp_path: Path) -> None:
    inventory_path = _write_inventory(tmp_path, [_row()])

    profile = profile_provider_api_pages(inventory_path)

    assert profile["rows"] == 1
    assert profile["column_count"] == len(REQUIRED_COLUMNS) == 81
    assert profile["aggregation_key_duplicates"] == 0


def test_profile_rejects_duplicate_provider_npi_grain(tmp_path: Path) -> None:
    row = _row()
    inventory_path = _write_inventory(tmp_path, [row, row])

    with pytest.raises(ValueError, match="duplicates=1"):
        profile_provider_api_pages(inventory_path)


def test_profile_rejects_missing_required_column(tmp_path: Path) -> None:
    row = _row()
    del row["Bene_CC_PH_Asthma_V2_Pct"]
    inventory_path = _write_inventory(tmp_path, [row])

    with pytest.raises(ValueError, match="rows_missing_required_columns=1"):
        profile_provider_api_pages(inventory_path)


def test_profile_rejects_invalid_npi(tmp_path: Path) -> None:
    inventory_path = _write_inventory(tmp_path, [_row(npi="invalid")])

    with pytest.raises(ValueError, match="invalid_npis=1"):
        profile_provider_api_pages(inventory_path)


def test_profile_replays_every_exact_filter(tmp_path: Path) -> None:
    inventory_path = _write_inventory(
        tmp_path,
        [_row()],
        filters={"Rndrng_NPI": "9999999999", "Rndrng_Prvdr_State_Abrvtn": "MD"},
    )

    with pytest.raises(ValueError, match="filter_mismatches=1"):
        profile_provider_api_pages(inventory_path)


def test_profile_requires_version_specific_uuid(tmp_path: Path) -> None:
    inventory_path = _write_inventory(tmp_path, [_row()], dataset_uuid=DATASET_TYPE_UUID)

    with pytest.raises(ValueError, match="version-specific UUID"):
        profile_provider_api_pages(inventory_path)


def test_build_manifest_preserves_denominator_and_role_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_schema = Path("research/data-manifests/source-manifest.schema.json").resolve()
    monkeypatch.chdir(tmp_path)
    schema = tmp_path / "research/data-manifests/source-manifest.schema.json"
    schema.parent.mkdir(parents=True)
    shutil.copyfile(source_schema, schema)
    inventory_path = _write_inventory(tmp_path, [_row()])
    manifest_path = tmp_path / "research/data-manifests/part-b-provider-2024.json"

    manifest = build_part_b_provider_api_manifest(
        inventory_path,
        manifest_path,
        data_year=2024,
        modified_at="2026-05-21",
        documentation_snapshots=[],
    )

    assert manifest["dataset"]["dataset_id"] == DATASET_TYPE_UUID
    assert manifest["dataset"]["version_id"] == VERSION_UUID
    assert manifest["dataset"]["aggregation_keys"] == ["Rndrng_NPI"]
    assert manifest["retrieval"]["parameters"]["filters"] == {"Rndrng_NPI": "1234567890"}
    assert "not only visible Provider-and-Service rows" in manifest["semantics"]["suppression"]
    assert (
        "not the indication"
        in manifest["semantics"]["utilization_fields"]["Bene_CC_PH_Asthma_V2_Pct"]
    )
    assert (
        "top-coded at 75%"
        in manifest["semantics"]["utilization_fields"]["Bene_CC_PH_Asthma_V2_Pct"]
    )
    assert "not proof" in manifest["semantics"]["monetary_fields"]["Tot_Mdcr_Pymt_Amt"]
    assert all(record["relative_path"].startswith("data/raw/") for record in manifest["files"])
