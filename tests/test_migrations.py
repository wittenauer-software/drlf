from contextlib import contextmanager, nullcontext
from hashlib import sha256
from pathlib import Path

import pytest

from drlf.database import apply_migrations


def test_apply_migrations_requires_sql_files(tmp_path) -> None:
    try:
        apply_migrations("unused", tmp_path)
    except ValueError as error:
        assert "No SQL migrations" in str(error)
    else:
        raise AssertionError("Expected an empty migration directory to fail")


def test_apply_migrations_refuses_database_only_history_before_local_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "0001_local.sql").write_text("select 'must not run';\n", encoding="utf-8")
    executed: list[str] = []

    class FakeCursor:
        def __init__(self, *, one: tuple[object, ...] | None = None, rows=None):
            self.one = one
            self.rows = rows or []

        def fetchone(self):
            return self.one

        def fetchall(self):
            return self.rows

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, query: str, _parameters=None):
            executed.append(query)
            if "to_regclass" in query:
                return FakeCursor(one=("metadata.schema_migration",))
            if "select version, sha256" in query:
                return FakeCursor(rows=[("9999_future.sql", "a" * 64)])
            raise AssertionError(f"Unexpected mutation or query: {query}")

        def transaction(self):
            return nullcontext()

    monkeypatch.setattr(
        "drlf.database.psycopg.connect",
        lambda *_args, **_kwargs: FakeConnection(),
    )

    with pytest.raises(RuntimeError, match="absent from this checkout"):
        apply_migrations("postgresql://not-rendered", tmp_path)

    assert len(executed) == 2
    assert all("must not run" not in query for query in executed)


def test_apply_migrations_refuses_to_backfill_a_gap_before_a_later_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_sql = "select 'must not run first';\n"
    second_sql = "select 'already recorded second';\n"
    (tmp_path / "0001_first.sql").write_text(first_sql, encoding="utf-8")
    (tmp_path / "0002_second.sql").write_text(second_sql, encoding="utf-8")
    executed: list[str] = []

    class FakeCursor:
        def __init__(self, *, one: tuple[object, ...] | None = None, rows=None):
            self.one = one
            self.rows = rows or []

        def fetchone(self):
            return self.one

        def fetchall(self):
            return self.rows

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, query: str, _parameters=None):
            executed.append(query)
            if "to_regclass" in query:
                return FakeCursor(one=("metadata.schema_migration",))
            if "select version, sha256" in query:
                checksum = sha256(second_sql.encode("utf-8")).hexdigest()
                return FakeCursor(rows=[("0002_second.sql", checksum)])
            raise AssertionError(f"Unexpected mutation or query: {query}")

        def transaction(self):
            return nullcontext()

    monkeypatch.setattr(
        "drlf.database.psycopg.connect",
        lambda *_args, **_kwargs: FakeConnection(),
    )

    with pytest.raises(RuntimeError, match="not an exact prefix"):
        apply_migrations("postgresql://not-rendered", tmp_path)

    assert len(executed) == 2
    assert all("must not run first" not in query for query in executed)


def test_apply_migrations_rolls_back_the_complete_batch_when_a_later_file_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "0001_first.sql").write_text("select 'first';\n", encoding="utf-8")
    (tmp_path / "0002_second.sql").write_text("select 'second';\n", encoding="utf-8")
    effects: list[str] = []
    register: list[str] = []

    class FakeCursor:
        def __init__(self, one: tuple[object, ...] | None = None):
            self.one = one

        def fetchone(self):
            return self.one

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        @contextmanager
        def transaction(self):
            prior_effects = list(effects)
            prior_register = list(register)
            try:
                yield
            except Exception:
                effects[:] = prior_effects
                register[:] = prior_register
                raise

        def execute(self, query: str, parameters=None):
            if "to_regclass" in query:
                return FakeCursor((None,))
            if "select 'first'" in query:
                effects.append("first")
            elif "select 'second'" in query:
                effects.append("second")
                raise RuntimeError("synthetic migration failure")
            elif "insert into metadata.schema_migration" in query:
                register.append(str(parameters[0]))
            return FakeCursor()

    monkeypatch.setattr(
        "drlf.database.psycopg.connect",
        lambda *_args, **_kwargs: FakeConnection(),
    )

    with pytest.raises(RuntimeError, match="synthetic migration failure"):
        apply_migrations("postgresql://not-rendered", tmp_path)

    assert effects == []
    assert register == []
