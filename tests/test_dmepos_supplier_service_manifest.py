import json
import shutil
from hashlib import sha256
from pathlib import Path

import pytest

from drlf.sources.cms_api import ExactInFilter, build_data_url
from drlf.sources.dmepos_supplier_service_manifest import (
    MAX_BUNDLE_ROWS,
    MAX_RETAINED_BYTES,
    RELEASES,
    REQUIRED_COLUMNS,
    SCHEMA_FINGERPRINT,
    build_dmepos_supplier_service_api_manifest,
    profile_dmepos_supplier_service_api_pages,
)


def _row(
    *,
    npi: str = "1234567890",
    hcpcs_code: str = "A6023",
    beneficiaries: str = "20",
) -> dict[str, str]:
    row = dict.fromkeys(REQUIRED_COLUMNS, "")
    row.update(
        {
            "Suplr_NPI": npi,
            "Suplr_Prvdr_Last_Name_Org": "Example Supply",
            "Suplr_Prvdr_Ent_Cd": "O",
            "Suplr_Prvdr_City": "Example",
            "Suplr_Prvdr_State_Abrvtn": "FL",
            "Suplr_Prvdr_Spclty_Desc": "Other Medical Supply Company",
            "Suplr_Prvdr_Spclty_Srce": "DME",
            "HCPCS_Cd": hcpcs_code,
            "HCPCS_Desc": "Collagen dressing, each",
            "Suplr_Rentl_Ind": "N",
            "Tot_Suplr_Benes": beneficiaries,
            "Tot_Suplr_Clms": "20",
            "Tot_Suplr_Srvcs": "100",
            "Avg_Suplr_Sbmtd_Chrg": "260",
            "Avg_Suplr_Mdcr_Alowd_Amt": "250",
            "Avg_Suplr_Mdcr_Pymt_Amt": "200",
            "Avg_Suplr_Mdcr_Stdzd_Amt": "201",
        }
    )
    return row


def _write_inventory(
    root: Path,
    rows: list[dict[str, str]],
    *,
    suffix: str = "a6023",
    filters: dict[str, object] | None = None,
    observed_at: str = "2026-09-03T02:40:00Z",
) -> Path:
    release = RELEASES[2024]
    snapshot_dir = root / f"data/raw/cms/dmepos-service/2024/{suffix}/observed"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    body = json.dumps(rows, separators=(",", ":")).encode()
    page_path = snapshot_dir / "page-00001-offset-000000000.json"
    page_path.write_bytes(body)
    recorded_filters = filters or {"HCPCS_Cd": "A6023"}
    sort_fields: tuple[str, ...] = ()
    if set(recorded_filters) == {"condition"}:
        condition = recorded_filters["condition"]
        assert isinstance(condition, dict)
        api_filters = ExactInFilter(
            str(condition["path"]),
            tuple(condition["values"]),  # type: ignore[arg-type]
        )
        sort_fields = ("Suplr_NPI", "HCPCS_Cd", "Suplr_Rentl_Ind")
    else:
        api_filters = recorded_filters  # type: ignore[assignment]
    inventory = {
        "inventory_version": 1,
        "dataset_uuid": release.version_id,
        "observed_at": observed_at,
        "filters": recorded_filters,
        "page_size": 1_000,
        "max_pages": 2,
        "max_response_bytes": 1_048_576,
        "max_total_bytes": 2_097_152,
        "expected_rows": len(rows),
        "total_bytes": len(body),
        "total_rows": len(rows),
        "pages": [
            {
                "ordinal": 1,
                "offset": 0,
                "rows": len(rows),
                "url": build_data_url(
                    release.version_id,
                    api_filters,
                    offset=0,
                    page_size=1_000,
                    sort_fields=sort_fields,
                ),
                "relative_filename": page_path.name,
                "bytes": len(body),
                "sha256": sha256(body).hexdigest(),
                "media_type": "application/json",
            }
        ],
    }
    if sort_fields:
        inventory["sort_fields"] = list(sort_fields)
    inventory_path = snapshot_dir / "inventory.json"
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    return inventory_path


def test_profile_preserves_suppressed_beneficiaries_and_exact_grain(tmp_path: Path) -> None:
    inventory = _write_inventory(
        tmp_path,
        [_row(), _row(npi="1234567891", beneficiaries="")],
    )

    profile = profile_dmepos_supplier_service_api_pages([inventory], data_year=2024)

    assert profile["rows"] == 2
    assert profile["columns"] == 32
    assert profile["schema_fingerprint"] == SCHEMA_FINGERPRINT
    assert profile["beneficiary_suppressed_rows"] == 1


def test_profile_rejects_duplicate_union_grain(tmp_path: Path) -> None:
    first = _write_inventory(tmp_path, [_row()], suffix="one")
    second = _write_inventory(tmp_path, [_row()], suffix="two")

    with pytest.raises(ValueError, match="repeats a published grain"):
        profile_dmepos_supplier_service_api_pages([first, second], data_year=2024)


def test_profile_rejects_declared_oversize_bundle_before_reading_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventories = [
        _write_inventory(tmp_path, [_row()], suffix="oversize-one"),
        _write_inventory(tmp_path, [_row()], suffix="oversize-two"),
    ]
    declared_page_bytes = 3 * 1024 * 1024
    for inventory_path in inventories:
        payload = json.loads(inventory_path.read_text(encoding="utf-8"))
        payload["max_pages"] = 3
        payload["max_response_bytes"] = 4 * 1024 * 1024
        payload["max_total_bytes"] = 10 * 1024 * 1024
        payload["total_bytes"] = 3 * declared_page_bytes
        payload["pages"] = [
            {
                "ordinal": ordinal,
                "offset": (ordinal - 1) * payload["page_size"],
                "rows": 1 if ordinal == 1 else 0,
                "url": build_data_url(
                    RELEASES[2024].version_id,
                    payload["filters"],
                    offset=(ordinal - 1) * payload["page_size"],
                    page_size=payload["page_size"],
                ),
                "relative_filename": f"page-{ordinal:05d}.json",
                "bytes": declared_page_bytes,
                "sha256": "0" * 64,
                "media_type": "application/json",
            }
            for ordinal in range(1, 4)
        ]
        inventory_path.write_text(json.dumps(payload), encoding="utf-8")

    assert (
        sum(json.loads(path.read_text(encoding="utf-8"))["total_bytes"] for path in inventories)
        > MAX_RETAINED_BYTES
    )

    def fail_page_read(_path: Path) -> bytes:
        raise AssertionError("oversize bundle must fail before any page read")

    monkeypatch.setattr(Path, "read_bytes", fail_page_read)
    with pytest.raises(ValueError, match="bundle exceeds its retained-byte ceiling"):
        profile_dmepos_supplier_service_api_pages(inventories, data_year=2024)


def test_profile_accepts_closed_in_inventory(tmp_path: Path) -> None:
    structured_filter = {
        "condition": {
            "path": "Suplr_NPI",
            "operator": "IN",
            "values": ["1234567890", "1234567891"],
        }
    }
    inventory = _write_inventory(
        tmp_path,
        [_row(), _row(npi="1234567891")],
        filters=structured_filter,
    )
    profile = profile_dmepos_supplier_service_api_pages([inventory], data_year=2024)

    assert profile["filters"] == {"Suplr_NPI": ["1234567890", "1234567891"]}


def test_profile_rejects_inventory_url_that_does_not_match_filter(tmp_path: Path) -> None:
    inventory = _write_inventory(tmp_path, [_row()])
    payload = json.loads(inventory.read_text(encoding="utf-8"))
    payload["pages"][0]["url"] = payload["pages"][0]["url"].replace("A6023", "A6021")
    inventory.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="does not reproduce its recorded filter"):
        profile_dmepos_supplier_service_api_pages([inventory], data_year=2024)


def test_profile_rejects_wrong_release_and_filter_mismatch(tmp_path: Path) -> None:
    inventory = _write_inventory(tmp_path, [_row(hcpcs_code="A6021")])

    with pytest.raises(ValueError, match="violates its API filter"):
        profile_dmepos_supplier_service_api_pages([inventory], data_year=2024)

    payload = json.loads(inventory.read_text(encoding="utf-8"))
    payload["dataset_uuid"] = RELEASES[2023].version_id
    inventory.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="pinned annual version UUID"):
        profile_dmepos_supplier_service_api_pages([inventory], data_year=2024)


def test_build_manifest_records_targeted_union_and_caveats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_schema = Path("research/data-manifests/source-manifest.schema.json").resolve()
    monkeypatch.chdir(tmp_path)
    schema = tmp_path / "research/data-manifests/source-manifest.schema.json"
    schema.parent.mkdir(parents=True)
    shutil.copyfile(source_schema, schema)
    first = _write_inventory(
        tmp_path, [_row(hcpcs_code="A6021")], suffix="a6021", filters={"HCPCS_Cd": "A6021"}
    )
    second = _write_inventory(tmp_path, [_row()], suffix="a6023")
    manifest_path = tmp_path / "research/data-manifests/dmepos-service-2024.json"

    manifest = build_dmepos_supplier_service_api_manifest(
        [second, first],
        manifest_path,
        data_year=2024,
        documentation_snapshots=[],
    )

    assert manifest["dataset"]["version_id"] == RELEASES[2024].version_id
    assert manifest["dataset"]["aggregation_keys"] == list(
        ("Suplr_NPI", "HCPCS_Cd", "Suplr_Rentl_Ind")
    )
    assert manifest["retrieval"]["parameters"]["filters"] == {"HCPCS_Cd": ["A6021", "A6023"]}
    assert manifest["retrieval"]["parameters"]["page_size"] == 1_000
    assert manifest["retrieval"]["parameters"]["bundle_max_rows"] == MAX_BUNDLE_ROWS
    assert manifest["validation"]["rows"] == 2
    assert "need not reconcile" in manifest["semantics"]["suppression"]
    assert [record["role"] for record in manifest["files"]] == [
        "api-page",
        "acquisition-inventory",
        "api-page",
        "acquisition-inventory",
    ]
    manifest_bytes = manifest_path.read_bytes()
    assert manifest_bytes.endswith(b"\n")
    assert b"\r\n" not in manifest_bytes
