from hashlib import sha256
from pathlib import Path

import psycopg

MAX_MIGRATION_FILES = 1_000
MAX_MIGRATION_FILE_BYTES = 8 * 1024 * 1024
MAX_MIGRATION_TOTAL_BYTES = 64 * 1024 * 1024
DATABASE_CONNECT_TIMEOUT_SECONDS = 10
MIGRATION_LOCK_TIMEOUT_MS = 10_000
MIGRATION_STATEMENT_TIMEOUT_MS = 300_000


def find_migration_history_gaps(
    local_versions: tuple[str, ...], applied_versions: set[str]
) -> tuple[str, ...]:
    """Return unapplied local versions that precede a recorded later version."""
    applied_positions = [
        position for position, version in enumerate(local_versions) if version in applied_versions
    ]
    if not applied_positions:
        return ()
    last_applied_position = max(applied_positions)
    return tuple(
        version
        for version in local_versions[: last_applied_position + 1]
        if version not in applied_versions
    )


def _read_local_migrations(migrations_dir: Path) -> dict[str, tuple[str, str]]:
    migration_paths = sorted(migrations_dir.glob("*.sql"))
    if not migration_paths:
        raise ValueError(f"No SQL migrations found in {migrations_dir}")
    if len(migration_paths) > MAX_MIGRATION_FILES:
        raise ValueError(f"Migration count exceeds the {MAX_MIGRATION_FILES:,}-file safety limit")

    total_bytes = 0
    migrations: dict[str, tuple[str, str]] = {}
    for migration_path in migration_paths:
        with migration_path.open("rb") as migration_file:
            content = migration_file.read(MAX_MIGRATION_FILE_BYTES + 1)
        if len(content) > MAX_MIGRATION_FILE_BYTES:
            raise ValueError(
                f"Migration exceeds the {MAX_MIGRATION_FILE_BYTES:,}-byte safety limit: "
                f"{migration_path.name}"
            )
        total_bytes += len(content)
        if total_bytes > MAX_MIGRATION_TOTAL_BYTES:
            raise ValueError(
                f"Migrations exceed the {MAX_MIGRATION_TOTAL_BYTES:,}-byte aggregate safety limit"
            )
        sql = content.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        migrations[migration_path.name] = (sql, sha256(sql.encode("utf-8")).hexdigest())
    return migrations


def check_connection(database_url: str) -> str:
    """Return the PostgreSQL server version without logging credentials."""
    with psycopg.connect(
        database_url,
        connect_timeout=DATABASE_CONNECT_TIMEOUT_SECONDS,
        options=f"-c statement_timeout={DATABASE_CONNECT_TIMEOUT_SECONDS * 1_000}",
    ) as connection:
        return str(connection.execute("select version()").fetchone()[0])


def apply_migrations(database_url: str, migrations_dir: Path) -> list[str]:
    """Apply ordered migrations after rejecting divergent or modified histories."""
    migrations = _read_local_migrations(migrations_dir)

    applied_now: list[str] = []
    with psycopg.connect(
        database_url,
        connect_timeout=DATABASE_CONNECT_TIMEOUT_SECONDS,
        options=(
            f"-c lock_timeout={MIGRATION_LOCK_TIMEOUT_MS} "
            f"-c statement_timeout={MIGRATION_STATEMENT_TIMEOUT_MS}"
        ),
    ) as connection:
        # One transaction covers the register, every migration, and every recorded hash. A later
        # failure therefore cannot leave a partially advanced baseline database.
        with connection.transaction():
            table_name = connection.execute(
                "select to_regclass('metadata.schema_migration')"
            ).fetchone()[0]
            if table_name is None:
                connection.execute("create schema if not exists metadata")
                connection.execute(
                    """
                    create table if not exists metadata.schema_migration (
                        version text primary key,
                        sha256 char(64) not null,
                        applied_at timestamptz not null default now()
                    )
                    """
                )
                existing: dict[str, str] = {}
            else:
                rows = connection.execute(
                    "select version, sha256 from metadata.schema_migration "
                    "order by version limit %s",
                    (MAX_MIGRATION_FILES + 1,),
                ).fetchall()
                if len(rows) > MAX_MIGRATION_FILES:
                    raise RuntimeError(
                        "Database migration register exceeds the bounded local migration limit"
                    )
                existing = {str(version): str(checksum).strip() for version, checksum in rows}

            database_only = sorted(set(existing) - set(migrations))
            if database_only:
                raise RuntimeError(
                    "Database contains migrations absent from this checkout; refusing to apply: "
                    + ", ".join(database_only)
                )

            modified = sorted(
                version
                for version, (_sql, checksum) in migrations.items()
                if version in existing and existing[version] != checksum
            )
            if modified:
                raise RuntimeError("Applied migration was modified: " + ", ".join(modified))

            history_gaps = find_migration_history_gaps(tuple(migrations), set(existing))
            if history_gaps:
                raise RuntimeError(
                    "Applied migration history is not an exact prefix; refusing to backfill "
                    "earlier migration(s): " + ", ".join(history_gaps)
                )

            for version, (sql, checksum) in migrations.items():
                if version in existing:
                    continue
                connection.execute(sql)
                connection.execute(
                    "insert into metadata.schema_migration (version, sha256) values (%s, %s)",
                    (version, checksum),
                )
                applied_now.append(version)

    return applied_now
