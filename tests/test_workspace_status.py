from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from typer.testing import CliRunner

from drlf import cli
from drlf.workspace_status import (
    DatabaseReadiness,
    DoctorCheck,
    DoctorReport,
    ReleaseListReport,
    _local_migration_hashes,
    build_doctor_report,
    build_release_list,
    inspect_source_status,
    inventory_local_releases,
    list_database_releases,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_manifest(
    root: Path,
    *,
    name: str = "release.json",
    slug: str = "example-data",
    year: int = 2024,
    source_path: str = "data/raw/example.csv",
    content: bytes | None = b"abc",
    expected_content: bytes | None = None,
    status: str = "complete",
) -> Path:
    if content is not None:
        path = root / source_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    expected = content if expected_content is None else expected_content
    manifest = {
        "manifest_version": 2,
        "status": status,
        "source": {"name": "Example", "type": "other", "homepage": None},
        "dataset": {
            "name": "Example data",
            "slug": slug,
            "dataset_id": None,
            "version_id": "v1",
            "data_year": year,
            "population": "Synthetic test records",
            "aggregation_keys": ["example_id"],
        },
        "observation": {
            "published_at": None,
            "modified_at": None,
            "observed_at": "2026-09-03T12:00:00Z",
            "accessed_at": "2026-09-03",
        },
        "documentation": {
            "landing_page": None,
            "methodology": None,
            "data_dictionary": None,
            "snapshots": [],
        },
        "terms": {
            "license_name": None,
            "license_url": None,
            "access_restrictions": "Synthetic fixture",
            "public_use_verified": True,
        },
        "retrieval": {
            "method": "download",
            "request_url": "https://example.test/example.csv",
            "parameters": {},
            "query": None,
        },
        "files": [
            {
                "relative_path": source_path,
                "filename": Path(source_path).name,
                "role": "data",
                "bytes": len(expected) if expected is not None else 3,
                "sha256": sha256(expected or b"abc").hexdigest(),
                "media_type": "text/csv",
                "compression": None,
                "schema_fingerprint": None,
            }
        ],
        "semantics": {
            "monetary_fields": {},
            "utilization_fields": {},
            "suppression": "None",
            "exclusions": [],
        },
        "validation": {
            "rows": 1,
            "columns": 1,
            "aggregation_key_duplicates": 0,
            "notes": [],
        },
    }
    manifest_path = root / "research/data-manifests" / name
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path = manifest_path.parent
    while schema_path.name != "data-manifests" and schema_path != root:
        schema_path = schema_path.parent
    schema_path = schema_path / "source-manifest.schema.json"
    if not schema_path.exists():
        shutil.copy2(
            PROJECT_ROOT / "research/data-manifests/source-manifest.schema.json",
            schema_path,
        )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _write_project_shell(root: Path, password: str) -> None:
    (root / "sql/migrations").mkdir(parents=True)
    (root / "research/data-manifests").mkdir(parents=True)
    (root / "AGENTS.md").write_text("# Instructions\n", encoding="utf-8")
    (root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (root / "research/data-manifests/source-manifest.schema.json").write_text(
        "{}\n", encoding="utf-8"
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "drlf"\n', encoding="utf-8"
    )
    (root / "sql/migrations/0001.sql").write_text("select 1;\n", encoding="utf-8")
    (root / ".env").write_text(
        "COMPOSE_PROJECT_NAME=example-research-test\n"
        "POSTGRES_DB=research\n"
        "POSTGRES_USER=research\n"
        "POSTGRES_PORT=5432\n"
        "POSTGRES_PASSWORD="
        + password
        + "\nDATABASE_URL=postgresql://research:"
        + password
        + "@127.0.0.1/research\n",
        encoding="utf-8",
    )


def test_source_status_does_not_hash_without_explicit_cap(tmp_path: Path) -> None:
    _write_manifest(tmp_path)

    report = inspect_source_status(tmp_path)

    assert report.all_files_present is True
    assert report.all_sizes_match is True
    assert report.hash_bytes_read == 0
    assert report.hash_checked_count == 0
    assert report.manifest_source_set_verified is False
    assert report.manifests[0].restore_status == "available_size_checked"
    assert report.manifests[0].files[0].hash_status == "not_checked"


def test_source_status_hashes_only_within_aggregate_cap(tmp_path: Path) -> None:
    _write_manifest(tmp_path, name="one.json", content=b"abc")
    _write_manifest(
        tmp_path,
        name="two.json",
        slug="other-data",
        source_path="data/raw/other.csv",
        content=b"defg",
    )

    report = inspect_source_status(tmp_path, hash_byte_cap=3)

    assert report.hash_bytes_read == 3
    assert report.hash_checked_count == 1
    assert report.hash_skipped_for_cap_count == 1
    statuses = {file.hash_status for manifest in report.manifests for file in manifest.files}
    assert statuses == {"match", "skipped_cap"}
    assert report.manifest_source_set_verified is False


def test_draft_manifest_does_not_consume_complete_release_hash_cap(tmp_path: Path) -> None:
    _write_manifest(tmp_path, name="00-draft.json", status="draft", content=b"abc")
    _write_manifest(
        tmp_path,
        name="01-complete.json",
        slug="complete-data",
        source_path="data/raw/complete.csv",
        content=b"def",
    )

    report = inspect_source_status(tmp_path, hash_byte_cap=3)

    draft, complete = report.manifests
    assert draft.files[0].hash_status == "not_checked_noncomplete"
    assert complete.files[0].hash_status == "match"
    assert report.hash_bytes_read == 3
    assert report.manifest_source_set_verified is True


def test_source_status_marks_complete_manifest_source_set_verified(tmp_path: Path) -> None:
    _write_manifest(tmp_path, content=b"abc")

    report = inspect_source_status(tmp_path, hash_byte_cap=3)

    assert report.manifest_source_set_verified is True
    assert report.all_hashes_verified is True
    assert report.manifests[0].restore_status == "verified"


def test_source_status_detects_same_size_hash_mismatch(tmp_path: Path) -> None:
    _write_manifest(tmp_path, content=b"abd", expected_content=b"abc")

    report = inspect_source_status(tmp_path, hash_byte_cap=3)

    file = report.manifests[0].files[0]
    assert file.size_status == "match"
    assert file.hash_status == "mismatch"
    assert report.hash_mismatch_count == 1
    assert report.manifest_source_set_verified is False


def test_source_status_reports_missing_and_unsafe_paths_without_opening_them(
    tmp_path: Path,
) -> None:
    missing_manifest = _write_manifest(tmp_path, content=None)
    manifest = json.loads(missing_manifest.read_text(encoding="utf-8"))
    manifest["files"].append(
        {
            "relative_path": "data/raw/../../outside.txt",
            "filename": "outside.txt",
            "role": "unsafe",
            "bytes": 1,
            "sha256": "a" * 64,
            "media_type": "text/plain",
            "compression": None,
            "schema_fingerprint": None,
        }
    )
    missing_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    report = inspect_source_status(tmp_path, hash_byte_cap=100)

    assert [file.size_status for file in report.manifests[0].files] == [
        "missing",
        "unsafe_path",
    ]
    assert report.hash_bytes_read == 0


def test_schema_invalid_complete_manifest_can_never_be_reported_verified(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["terms"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = inspect_source_status(tmp_path, hash_byte_cap=3)
    releases, invalid = inventory_local_releases(tmp_path)

    assert report.invalid_manifest_count == 1
    assert report.complete_manifest_count == 0
    assert report.manifest_source_set_verified is False
    assert "schema validation failed" in (report.manifests[0].error or "")
    assert releases == ()
    assert "schema validation failed" in invalid[0]["error"]


def test_manifest_discovery_enforces_count_cap_before_full_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_manifest(tmp_path, name="one.json")
    _write_manifest(
        tmp_path,
        name="two.json",
        slug="two-data",
        source_path="data/raw/two.csv",
    )
    monkeypatch.setattr("drlf.workspace_status.MAX_SOURCE_MANIFESTS", 1)

    with pytest.raises(ValueError, match="1-file safety limit"):
        inspect_source_status(tmp_path)


def test_release_inventory_excludes_schema_and_filters_dataset(tmp_path: Path) -> None:
    _write_manifest(tmp_path, name="one.json", slug="one-data", year=2023)
    _write_manifest(
        tmp_path,
        name="two.json",
        slug="two-data",
        year=2024,
        source_path="data/raw/two.csv",
    )
    releases, invalid = inventory_local_releases(tmp_path, dataset_slug="two-data")

    assert invalid == ()
    assert len(releases) == 1
    assert releases[0].dataset_slug == "two-data"
    assert releases[0].manifest_path == "research/data-manifests/two.json"
    assert releases[0].expected_bytes == 3


def test_release_inventory_discovers_nested_manifest_directories(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    nested_manifest = manifest.parent / "archived" / manifest.name
    nested_manifest.parent.mkdir()
    manifest.replace(nested_manifest)

    releases, invalid = inventory_local_releases(tmp_path)

    assert invalid == ()
    assert [release.manifest_path for release in releases] == [
        "research/data-manifests/archived/release.json"
    ]


def test_release_list_treats_unavailable_database_as_optional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_manifest(tmp_path)

    def unavailable(_root: Path) -> str:
        raise RuntimeError("postgresql://user:" + "do-not-print@" + "host/database")

    monkeypatch.setattr("drlf.workspace_status.effective_database_url", unavailable)

    report = build_release_list(tmp_path, include_database=True)

    assert report.database_status == "unavailable"
    assert report.database_error_type == "RuntimeError"
    assert "do-not-print" not in json.dumps(report.as_dict())


def test_database_release_inventory_filters_and_truncates_in_postgresql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    rows = [
        (
            index,
            "CMS",
            "example-data",
            "Example data",
            2024,
            "v1",
            datetime(2026, 9, 3, tzinfo=UTC),
            "complete",
            f"manifest-{index}.json",
            "a" * 64,
        )
        for index in range(1, 4)
    ]

    class FakeCursor:
        def fetchmany(self, count: int):
            captured["fetch_count"] = count
            return rows

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, query: str, parameters: tuple[object, ...]):
            captured["query"] = query
            captured["parameters"] = parameters
            return FakeCursor()

    def fake_connect(_url: str, **kwargs: object):
        captured["connect_kwargs"] = kwargs
        return FakeConnection()

    monkeypatch.setattr("drlf.workspace_status.psycopg.connect", fake_connect)

    releases, truncated = list_database_releases(
        "postgresql://not-rendered",
        dataset_slug="example-data",
        data_year=2024,
        row_cap=2,
        statement_timeout_seconds=3,
    )

    assert len(releases) == 2
    assert truncated is True
    assert "dataset.slug = %s" in str(captured["query"])
    assert "release.data_year = %s" in str(captured["query"])
    assert captured["parameters"] == ("example-data", 2024, 3)
    assert captured["fetch_count"] == 3
    assert captured["connect_kwargs"] == {
        "connect_timeout": 3,
        "options": "-c statement_timeout=3000 -c default_transaction_read_only=on",
    }


def test_doctor_report_is_ready_without_rendering_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    password = "a-unique-local-secret"
    _write_project_shell(tmp_path, password)
    for key in (
        "COMPOSE_PROJECT_NAME",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_PORT",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("drlf.workspace_status.shutil.which", lambda _name: "tool")
    monkeypatch.setattr(
        "drlf.workspace_status._probe_command",
        lambda *args, **kwargs: (True, "tool version 1"),
    )
    monkeypatch.setattr(
        "drlf.workspace_status.inspect_database_readiness",
        lambda *_args: DatabaseReadiness("18.6", (), (), ()),
    )

    report = build_doctor_report(tmp_path)
    serialized = json.dumps(report.as_dict())

    assert report.ready is True
    assert {check.status for check in report.checks} <= {"pass", "warn", "skip"}
    assert password not in serialized
    assert "postgresql://" not in serialized


def test_doctor_fails_when_database_has_migrations_absent_from_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_project_shell(tmp_path, "local-secret")
    monkeypatch.setattr("drlf.workspace_status.shutil.which", lambda _name: "tool")
    monkeypatch.setattr(
        "drlf.workspace_status._probe_command",
        lambda *args, **kwargs: (True, "tool version 1"),
    )
    monkeypatch.setattr(
        "drlf.workspace_status.inspect_database_readiness",
        lambda *_args: DatabaseReadiness("18.6", (), (), ("9999_future.sql",)),
    )

    report = build_doctor_report(tmp_path)
    database = next(check for check in report.checks if check.name == "database")

    assert database.status == "fail"
    assert "older than or incompatible" in database.detail
    assert report.ready is False


def test_doctor_prioritizes_incompatible_database_over_pending_migrations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_project_shell(tmp_path, "local-secret")
    monkeypatch.setattr("drlf.workspace_status.shutil.which", lambda _name: "tool")
    monkeypatch.setattr(
        "drlf.workspace_status._probe_command",
        lambda *args, **kwargs: (True, "tool version 1"),
    )
    monkeypatch.setattr(
        "drlf.workspace_status.inspect_database_readiness",
        lambda *_args: DatabaseReadiness("18.6", ("0002_local.sql",), (), ("9999_future.sql",)),
    )

    report = build_doctor_report(tmp_path)
    database = next(check for check in report.checks if check.name == "database")

    assert database.status == "fail"
    assert "absent from this checkout" in database.detail
    assert "also pending" in database.detail
    assert report.ready is False


def test_doctor_rejects_a_gap_before_a_recorded_later_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_project_shell(tmp_path, "local-secret")
    monkeypatch.setattr("drlf.workspace_status.shutil.which", lambda _name: "tool")
    monkeypatch.setattr(
        "drlf.workspace_status._probe_command",
        lambda *args, **kwargs: (True, "tool version 1"),
    )
    monkeypatch.setattr(
        "drlf.workspace_status.inspect_database_readiness",
        lambda *_args: DatabaseReadiness(
            "18.6",
            ("0001_first.sql",),
            (),
            (),
            ("0001_first.sql",),
        ),
    )

    report = build_doctor_report(tmp_path)
    database = next(check for check in report.checks if check.name == "database")

    assert database.status == "fail"
    assert "not an exact prefix" in database.detail
    assert "0001_first.sql" in database.detail
    assert report.ready is False


def test_doctor_requires_reachable_docker_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_project_shell(tmp_path, "local-secret")
    monkeypatch.setattr("drlf.workspace_status.shutil.which", lambda _name: "tool")

    def probe(command: list[str], **_kwargs: object) -> tuple[bool, str]:
        if "info" in command:
            return False, "daemon unavailable"
        return True, "tool version 1"

    monkeypatch.setattr("drlf.workspace_status._probe_command", probe)
    monkeypatch.setattr(
        "drlf.workspace_status.inspect_database_readiness",
        lambda *_args: DatabaseReadiness("18.6", (), (), ()),
    )

    report = build_doctor_report(tmp_path)
    daemon = next(check for check in report.checks if check.name == "docker-daemon")

    assert daemon.status == "fail"
    assert report.ready is False


def test_doctor_skip_database_is_not_full_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_project_shell(tmp_path, "local-secret")
    monkeypatch.setattr("drlf.workspace_status.shutil.which", lambda _name: "tool")
    monkeypatch.setattr(
        "drlf.workspace_status._probe_command",
        lambda *args, **kwargs: (True, "tool version 1"),
    )

    report = build_doctor_report(tmp_path, skip_database=True)

    assert next(check for check in report.checks if check.name == "database").status == "skip"
    assert report.ready is False


def test_migration_hashes_match_loader_text_newline_normalization(tmp_path: Path) -> None:
    migration = tmp_path / "0001.sql"
    migration.write_bytes(b"select 1;\r\n")

    hashes = _local_migration_hashes(tmp_path)

    assert hashes == {"0001.sql": sha256(b"select 1;\n").hexdigest()}


def test_doctor_rejects_unsynchronized_passwords_and_skips_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_project_shell(tmp_path, "password-one")
    env_path = tmp_path / ".env"
    env_path.write_text(
        env_path.read_text(encoding="utf-8").replace("password-one@", "password-two@"),
        encoding="utf-8",
    )
    monkeypatch.setattr("drlf.workspace_status.shutil.which", lambda _name: "tool")
    monkeypatch.setattr(
        "drlf.workspace_status._probe_command",
        lambda *args, **kwargs: (True, "tool version 1"),
    )

    report = build_doctor_report(tmp_path, skip_database=True)

    environment = next(check for check in report.checks if check.name == "environment")
    database = next(check for check in report.checks if check.name == "database")
    assert environment.status == "fail"
    assert database.status == "skip"
    assert report.ready is False


@pytest.mark.parametrize(
    ("old", "new", "field"),
    [
        (
            "COMPOSE_PROJECT_NAME=example-research-test",
            "COMPOSE_PROJECT_NAME=",
            "COMPOSE_PROJECT_NAME",
        ),
        ("POSTGRES_USER=research", "POSTGRES_USER=other_user", "POSTGRES_USER"),
        ("POSTGRES_DB=research", "POSTGRES_DB=other_database", "POSTGRES_DB"),
        ("POSTGRES_PORT=5432", "POSTGRES_PORT=5433", "POSTGRES_PORT"),
    ],
)
def test_doctor_rejects_unsynchronized_compose_identity_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    old: str,
    new: str,
    field: str,
) -> None:
    _write_project_shell(tmp_path, "local-secret")
    env_path = tmp_path / ".env"
    env_path.write_text(
        env_path.read_text(encoding="utf-8").replace(old, new),
        encoding="utf-8",
    )
    monkeypatch.setattr("drlf.workspace_status.shutil.which", lambda _name: "tool")
    monkeypatch.setattr(
        "drlf.workspace_status._probe_command",
        lambda *args, **kwargs: (True, "tool version 1"),
    )

    report = build_doctor_report(tmp_path, skip_database=True)
    environment = next(check for check in report.checks if check.name == "environment")

    assert environment.status == "fail"
    assert field in environment.detail
    assert report.ready is False


def test_operational_cli_commands_emit_json_and_honor_exit_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_manifest(tmp_path)
    runner = CliRunner()

    source_result = runner.invoke(cli.app, ["source-status", "--root", str(tmp_path), "--json"])
    release_result = runner.invoke(cli.app, ["release-list", "--root", str(tmp_path), "--json"])
    monkeypatch.setattr(
        cli,
        "build_doctor_report",
        lambda *_args, **_kwargs: DoctorReport(
            ready=False,
            checks=(DoctorCheck("example", "fail", "not ready"),),
        ),
    )
    doctor_result = runner.invoke(
        cli.app, ["doctor", "--root", str(tmp_path), "--skip-database", "--json"]
    )

    assert source_result.exit_code == 0, source_result.output
    assert json.loads(source_result.output)["hash_bytes_read"] == 0
    assert release_result.exit_code == 0, release_result.output
    assert json.loads(release_result.output)["local_release_count"] == 1
    assert doctor_result.exit_code == 1
    assert json.loads(doctor_result.output)["ready"] is False


def test_release_list_text_warns_when_database_inventory_is_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli,
        "build_release_list",
        lambda *_args, **_kwargs: ReleaseListReport(
            local_releases=(),
            invalid_manifests=(),
            database_status="truncated",
            database_releases=(),
            database_truncated=True,
            database_error_type=None,
        ),
    )

    result = CliRunner().invoke(
        cli.app,
        ["release-list", "--root", str(tmp_path), "--include-database"],
    )

    assert result.exit_code == 0, result.output
    assert "truncated at the requested row cap" in result.output
