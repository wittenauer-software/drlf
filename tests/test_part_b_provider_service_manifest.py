import json
import shutil
from hashlib import sha256
from pathlib import Path

import pytest

from drlf.sources.part_b_provider_service_manifest import (
    DATASET_TYPE_UUID,
    REQUIRED_COLUMNS,
    build_part_b_provider_service_api_manifest,
    profile_provider_service_api_pages,
)

VERSION_UUID = "335e5f35-eca6-482d-87b3-f99883e213e3"


def _row(
    *,
    npi: str = "1234567890",
    hcpcs_code: str = "TSTAA",
    place_of_service: str = "O",
) -> dict[str, str]:
    row = {column: "" for column in REQUIRED_COLUMNS}
    row.update(
        {
            "Rndrng_NPI": npi,
            "Rndrng_Prvdr_Last_Org_Name": "Example",
            "Rndrng_Prvdr_First_Name": "Person",
            "Rndrng_Prvdr_Ent_Cd": "I",
            "Rndrng_Prvdr_State_Abrvtn": "MD",
            "Rndrng_Prvdr_Type": "Allergy/ Immunology",
            "HCPCS_Cd": hcpcs_code,
            "HCPCS_Desc": "Synthetic test service A",
            "HCPCS_Drug_Ind": "N",
            "Place_Of_Srvc": place_of_service,
            "Tot_Benes": "20",
            "Tot_Srvcs": "21",
            "Tot_Bene_Day_Srvcs": "21",
            "Avg_Sbmtd_Chrg": "81",
            "Avg_Mdcr_Alowd_Amt": "28.82",
            "Avg_Mdcr_Pymt_Amt": "23.17",
            "Avg_Mdcr_Stdzd_Amt": "21.33",
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
    snapshot_dir = root / "data/raw/cms/part-b-provider-service/2024/observed"
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


def test_profile_validates_current_grain_and_columns(tmp_path: Path) -> None:
    inventory_path = _write_inventory(tmp_path, [_row()])

    profile = profile_provider_service_api_pages(inventory_path)

    assert profile["rows"] == 1
    assert profile["column_count"] == len(REQUIRED_COLUMNS) == 28
    assert profile["aggregation_key_duplicates"] == 0


def test_profile_rejects_duplicate_provider_service_place_grain(tmp_path: Path) -> None:
    row = _row()
    inventory_path = _write_inventory(tmp_path, [row, row])

    with pytest.raises(ValueError, match="duplicates=1"):
        profile_provider_service_api_pages(inventory_path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda row: row.pop("Tot_Bene_Day_Srvcs"), "rows_missing_required_columns=1"),
        (lambda row: row.update(Rndrng_NPI="invalid"), "invalid_npis=1"),
        (lambda row: row.update(HCPCS_Cd="TSTA"), "invalid_hcpcs_codes=1"),
        (lambda row: row.update(Place_Of_Srvc="X"), "invalid_places_of_service=1"),
    ],
)
def test_profile_rejects_invalid_rows(tmp_path: Path, mutation, message: str) -> None:
    row = _row()
    mutation(row)
    inventory_path = _write_inventory(tmp_path, [row])

    with pytest.raises(ValueError, match=message):
        profile_provider_service_api_pages(inventory_path)


def test_profile_replays_every_exact_filter(tmp_path: Path) -> None:
    inventory_path = _write_inventory(
        tmp_path,
        [_row()],
        filters={"Rndrng_NPI": "9999999999", "HCPCS_Cd": "TSTAA"},
    )

    with pytest.raises(ValueError, match="filter_mismatches=1"):
        profile_provider_service_api_pages(inventory_path)


def test_profile_requires_version_specific_uuid(tmp_path: Path) -> None:
    inventory_path = _write_inventory(tmp_path, [_row()], dataset_uuid=DATASET_TYPE_UUID)

    with pytest.raises(ValueError, match="version-specific UUID"):
        profile_provider_service_api_pages(inventory_path)


def test_build_manifest_preserves_version_filters_semantics_and_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_schema = Path("research/data-manifests/source-manifest.schema.json").resolve()
    monkeypatch.chdir(tmp_path)
    schema = tmp_path / "research/data-manifests/source-manifest.schema.json"
    schema.parent.mkdir(parents=True)
    shutil.copyfile(source_schema, schema)
    inventory_path = _write_inventory(tmp_path, [_row()])
    manifest_path = tmp_path / "research/data-manifests/part-b-provider-service-2024.json"

    manifest = build_part_b_provider_service_api_manifest(
        inventory_path,
        manifest_path,
        data_year=2024,
        modified_at="2026-05-21",
        documentation_snapshots=[],
    )

    assert manifest["dataset"]["dataset_id"] == DATASET_TYPE_UUID
    assert manifest["dataset"]["version_id"] == VERSION_UUID
    assert manifest["terms"]["license_name"] == (
        "CMS AMA CPT end-user agreement (CPT material only)"
    )
    assert manifest["terms"]["license_url"] == "https://www.cms.gov/license/ama"
    assert "does not grant" in manifest["terms"]["access_restrictions"]
    assert manifest["terms"]["public_use_verified"] is True
    assert manifest["dataset"]["aggregation_keys"] == [
        "Rndrng_NPI",
        "HCPCS_Cd",
        "Place_Of_Srvc",
    ]
    assert manifest["retrieval"]["parameters"]["filters"] == {"Rndrng_NPI": "1234567890"}
    assert "10 or fewer" in manifest["semantics"]["suppression"]
    assert "not proof" in manifest["semantics"]["monetary_fields"]["Avg_Mdcr_Pymt_Amt"]
    assert [record["role"] for record in manifest["files"]] == [
        "api-page",
        "acquisition-inventory",
    ]
    assert all(record["relative_path"].startswith("data/raw/") for record in manifest["files"])
