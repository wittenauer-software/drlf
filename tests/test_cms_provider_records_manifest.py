from __future__ import annotations

import json
import shutil
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from drlf import cli
from drlf.sources.cms_api import ExactInFilter, build_data_url
from drlf.sources.cms_provider_records_manifest import (
    ENROLLMENT_COLUMNS,
    RELEASES,
    REVOCATION_COLUMNS,
    build_cms_provider_records_api_manifest,
    profile_cms_provider_records_api_inventory,
)


def _enrollment_row(npi: str = "1234567890") -> dict[str, str]:
    row = dict.fromkeys(ENROLLMENT_COLUMNS, "")
    row.update(
        {
            "NPI": npi,
            "MULTIPLE_NPI_FLAG": "N",
            "PECOS_ASCT_CNTL_ID": "SYNTHETIC-PECOS-CONTROL",
            "ENRLMT_ID": "SYNTHETIC000001",
            "PROVIDER_TYPE_CD": "30-01",
            "PROVIDER_TYPE_DESC": "DME SUPPLIER - MEDICAL SUPPLY COMPANY",
            "STATE_CD": "FL",
            "ORG_NAME": "EXAMPLE MEDICAL SUPPLY",
        }
    )
    return row


def _revocation_row(npi: str = "1234567890") -> dict[str, str]:
    row = dict.fromkeys(REVOCATION_COLUMNS, "")
    row.update(
        {
            "ENRLMT_ID": "SYNTHETIC000001",
            "NPI": npi,
            "ORG_NAME": "EXAMPLE MEDICAL SUPPLY",
            "MULTIPLE_NPI_FLAG": "N",
            "STATE_CD": "FL",
            "PROVIDER_TYPE_DESC": "DME SUPPLIER - MEDICAL SUPPLY COMPANY",
            "REVOCATION_RSN": "424.535(A)(1) Noncompliance",
            "REVOCATION_EFCTV_DT": "2024-07-31",
            "REENROLLMENT_BAR_EXPRTN_DT": "2027-10-23",
        }
    )
    return row


def _write_inventory(
    root: Path,
    *,
    dataset_key: str,
    rows: list[dict[str, str]],
    npis: tuple[str, ...] = ("1234567890", "1234567891"),
) -> Path:
    release = RELEASES[dataset_key]
    snapshot_dir = root / f"data/raw/cms/{dataset_key}/current/observed"
    snapshot_dir.mkdir(parents=True)
    page_bytes = json.dumps(rows, separators=(",", ":")).encode()
    page_path = snapshot_dir / "page-00001-offset-000000000.json"
    page_path.write_bytes(page_bytes)
    exact_filter = ExactInFilter("NPI", npis)
    request_url = build_data_url(
        release.version_id,
        exact_filter,
        offset=0,
        page_size=100,
        sort_fields=("NPI",),
    )
    inventory = {
        "inventory_version": 1,
        "dataset_uuid": release.version_id,
        "observed_at": "2026-09-03T02:59:29Z",
        "filters": {
            "condition": {
                "path": "NPI",
                "operator": "IN",
                "values": list(npis),
            }
        },
        "page_size": 100,
        "max_pages": 1,
        "max_response_bytes": 1_048_576,
        "max_total_bytes": 1_048_576,
        "expected_rows": len(rows),
        "total_bytes": len(page_bytes),
        "total_rows": len(rows),
        "pages": [
            {
                "ordinal": 1,
                "offset": 0,
                "rows": len(rows),
                "url": request_url,
                "relative_filename": page_path.name,
                "bytes": len(page_bytes),
                "sha256": sha256(page_bytes).hexdigest(),
                "media_type": "application/json",
            }
        ],
        "sort_fields": ["NPI"],
    }
    inventory_path = snapshot_dir / "inventory.json"
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    return inventory_path


def _prepare_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_schema = Path("research/data-manifests/source-manifest.schema.json").resolve()
    schema = tmp_path / "research/data-manifests/source-manifest.schema.json"
    schema.parent.mkdir(parents=True)
    shutil.copyfile(source_schema, schema)
    monkeypatch.chdir(tmp_path)


def test_revocation_manifest_preserves_exact_scope_and_point_in_time_caveat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_repository(tmp_path, monkeypatch)
    inventory = _write_inventory(
        tmp_path,
        dataset_key="revoked",
        rows=[_revocation_row()],
    )
    output = tmp_path / "research/data-manifests/revoked.json"

    manifest = build_cms_provider_records_api_manifest(
        inventory,
        output,
        dataset_key="revoked",
    )

    assert manifest["dataset"]["version_id"] == RELEASES["revoked"].version_id
    assert manifest["dataset"]["aggregation_keys"] == ["ENRLMT_ID"]
    assert manifest["retrieval"]["parameters"]["filter"] == {"NPI": ["1234567890", "1234567891"]}
    assert manifest["retrieval"]["parameters"]["matched_npis"] == ["1234567890"]
    assert manifest["retrieval"]["parameters"]["missing_npis"] == ["1234567891"]
    assert manifest["validation"]["rows"] == 1
    assert manifest["validation"]["columns"] == len(REVOCATION_COLUMNS)
    assert "not a historical-status finding" in manifest["semantics"]["suppression"]
    assert output.exists()


def test_empty_enrollment_snapshot_records_dictionary_schema_and_all_missing_npis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_repository(tmp_path, monkeypatch)
    inventory = _write_inventory(tmp_path, dataset_key="enrollment", rows=[])
    output = tmp_path / "research/data-manifests/enrollment.json"

    manifest = build_cms_provider_records_api_manifest(
        inventory,
        output,
        dataset_key="enrollment",
    )

    assert manifest["validation"]["rows"] == 0
    assert manifest["validation"]["columns"] == len(ENROLLMENT_COLUMNS)
    assert manifest["retrieval"]["parameters"]["matched_npis"] == []
    assert manifest["retrieval"]["parameters"]["missing_npis"] == [
        "1234567890",
        "1234567891",
    ]
    assert "empty row array" in manifest["validation"]["notes"][3]


def test_profile_rejects_wrong_release_and_row_outside_exact_npi_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_repository(tmp_path, monkeypatch)
    inventory = _write_inventory(
        tmp_path,
        dataset_key="revoked",
        rows=[_revocation_row(npi="9999999999")],
    )

    with pytest.raises(ValueError, match="violates its exact NPI filter"):
        profile_cms_provider_records_api_inventory(inventory, dataset_key="revoked")

    payload = json.loads(inventory.read_text(encoding="utf-8"))
    payload["dataset_uuid"] = RELEASES["enrollment"].version_id
    inventory.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="pinned Q2 2026 UUID"):
        profile_cms_provider_records_api_inventory(inventory, dataset_key="revoked")


def test_profile_rejects_relaxed_resource_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_repository(tmp_path, monkeypatch)
    inventory = _write_inventory(tmp_path, dataset_key="enrollment", rows=[_enrollment_row()])
    payload = json.loads(inventory.read_text(encoding="utf-8"))
    payload["max_pages"] = 2
    inventory.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="one-page safety limit"):
        profile_cms_provider_records_api_inventory(inventory, dataset_key="enrollment")


def test_profile_rejects_inventory_byte_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_repository(tmp_path, monkeypatch)
    inventory = _write_inventory(tmp_path, dataset_key="enrollment", rows=[_enrollment_row()])
    payload = json.loads(inventory.read_text(encoding="utf-8"))
    payload["total_bytes"] += 1
    inventory.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="total bytes do not match"):
        profile_cms_provider_records_api_inventory(inventory, dataset_key="enrollment")


def test_profile_rejects_schema_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_repository(tmp_path, monkeypatch)
    row = _revocation_row()
    row["NEW_FIELD"] = "unexpected"
    inventory = _write_inventory(tmp_path, dataset_key="revoked", rows=[row])

    with pytest.raises(ValueError, match="pinned revoked schema"):
        profile_cms_provider_records_api_inventory(inventory, dataset_key="revoked")


def test_cli_routes_provider_record_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = tmp_path / "inventory.json"
    inventory.write_text("{}", encoding="utf-8")
    output = tmp_path / "manifest.json"
    observed: dict[str, Any] = {}

    def fake_builder(
        inventory_path: Path,
        manifest_path: Path,
        **kwargs: Any,
    ) -> dict[str, Any]:
        observed["call"] = (inventory_path, manifest_path, kwargs)
        return {
            "validation": {"rows": 5},
            "retrieval": {
                "parameters": {
                    "matched_npis": ["1234567890"],
                    "missing_npis": ["1234567891"],
                }
            },
        }

    monkeypatch.setattr(
        cli,
        "require_research_root",
        lambda *_args, **_kwargs: "private-system-of-record",
    )
    monkeypatch.setattr(cli, "build_cms_provider_records_api_manifest", fake_builder)
    result = CliRunner().invoke(
        cli.app,
        [
            "cms-provider-records-api-manifest",
            str(output),
            "--dataset",
            "revoked",
            "--inventory",
            str(inventory),
            "--documentation-snapshot",
            "data/raw/cms/docs/methodology.pdf",
        ],
    )

    assert result.exit_code == 0, result.output
    assert observed["call"] == (
        inventory,
        output,
        {
            "dataset_key": "revoked",
            "documentation_snapshots": ["data/raw/cms/docs/methodology.pdf"],
        },
    )
    assert "Validated 5 revoked rows; matched 1 NPIs, missing 1" in result.output
