import json
import shutil
from pathlib import Path

import pytest
from jsonschema.exceptions import ValidationError

from drlf.sources.nppes_manifest import (
    build_nppes_api_manifest,
    profile_nppes_api_snapshot,
)


def _payload(npi: str = "1234567890") -> dict[str, object]:
    return {
        "result_count": 1,
        "results": [
            {
                "number": npi,
                "enumeration_type": "NPI-1",
                "basic": {"first_name": "EXAMPLE", "last_name": "PERSON", "status": "A"},
                "addresses": [{"address_purpose": "LOCATION", "state": "MD"}],
                "taxonomies": [{"code": "207Q00000X", "primary": True}],
            }
        ],
    }


def test_profile_nppes_snapshot_validates_exact_npi(tmp_path: Path) -> None:
    snapshot = tmp_path / "npi.json"
    snapshot.write_text(json.dumps(_payload()), encoding="utf-8")

    profile = profile_nppes_api_snapshot(snapshot, expected_npi="1234567890")

    assert profile["enumeration_type"] == "NPI-1"
    assert profile["taxonomy_count"] == 1
    assert profile["address_count"] == 1
    assert len(profile["sha256"]) == 64


def test_profile_nppes_snapshot_rejects_mismatched_npi(tmp_path: Path) -> None:
    snapshot = tmp_path / "npi.json"
    snapshot.write_text(json.dumps(_payload("9999999999")), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match"):
        profile_nppes_api_snapshot(snapshot, expected_npi="1234567890")


def test_nppes_schema_fingerprint_includes_every_distinct_list_item_shape(tmp_path: Path) -> None:
    first = _payload()
    first["results"][0]["addresses"].append(  # type: ignore[index,union-attr]
        {
            "address_purpose": "MAILING",
            "state": "VA",
            "telephone_number": "555-0100",
        }
    )
    second = _payload()
    second["results"][0]["addresses"].append(  # type: ignore[index,union-attr]
        {"address_purpose": "MAILING", "state": "VA"}
    )
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first_path.write_text(json.dumps(first), encoding="utf-8")
    second_path.write_text(json.dumps(second), encoding="utf-8")

    first_profile = profile_nppes_api_snapshot(first_path, expected_npi="1234567890")
    second_profile = profile_nppes_api_snapshot(second_path, expected_npi="1234567890")

    assert first_profile["schema_fingerprint"] != second_profile["schema_fingerprint"]


def test_build_nppes_manifest_requires_retained_raw_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_schema = Path("research/data-manifests/source-manifest.schema.json").resolve()
    monkeypatch.chdir(tmp_path)
    schema = tmp_path / "research/data-manifests/source-manifest.schema.json"
    schema.parent.mkdir(parents=True)
    shutil.copyfile(source_schema, schema)
    snapshot = tmp_path / "data/raw/nppes/npi-registry/1234567890/observed/npi.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(json.dumps(_payload()), encoding="utf-8")

    manifest = build_nppes_api_manifest(
        snapshot,
        tmp_path / "research/data-manifests/nppes.json",
        npi="1234567890",
        observed_at="2026-09-02T12:00:00Z",
    )

    assert manifest["status"] == "complete"
    assert manifest["source"]["type"] == "NPPES"
    assert manifest["dataset"]["aggregation_keys"] == ["number"]
    assert manifest["retrieval"]["parameters"] == {"number": "1234567890", "version": "2.1"}
    assert manifest["files"][0]["relative_path"].startswith("data/raw/")


def test_build_nppes_manifest_refuses_existing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    snapshot = tmp_path / "data/raw/nppes/npi.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(json.dumps(_payload()), encoding="utf-8")
    output = tmp_path / "research/data-manifests/nppes.json"
    output.parent.mkdir(parents=True)
    output.write_text("{}", encoding="utf-8")

    with pytest.raises(FileExistsError):
        build_nppes_api_manifest(
            snapshot,
            output,
            npi="1234567890",
            observed_at="2026-09-02T12:00:00Z",
        )


def test_build_nppes_manifest_does_not_leave_invalid_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_schema = Path("research/data-manifests/source-manifest.schema.json").resolve()
    monkeypatch.chdir(tmp_path)
    schema = tmp_path / "research/data-manifests/source-manifest.schema.json"
    schema.parent.mkdir(parents=True)
    shutil.copyfile(source_schema, schema)
    snapshot = tmp_path / "data/raw/nppes/npi.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(json.dumps(_payload()), encoding="utf-8")
    output = tmp_path / "research/data-manifests/nppes.json"

    with pytest.raises(ValidationError):
        build_nppes_api_manifest(
            snapshot,
            output,
            npi="1234567890",
            observed_at="2026-99-99T12:00:00Z",
        )

    assert not output.exists()
    assert not output.with_name(f".{output.name}.part").exists()
