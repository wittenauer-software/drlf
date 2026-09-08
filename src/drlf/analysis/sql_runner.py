from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from hashlib import sha256
from io import StringIO
from pathlib import Path
from typing import Any

import psycopg


def parse_parameters(values: list[str]) -> dict[str, str]:
    """Parse repeatable KEY=VALUE parameters for a repository SQL query."""
    parameters: dict[str, str] = {}
    for value in values:
        key, separator, parameter_value = value.partition("=")
        if not separator or not key.strip():
            raise ValueError(f"Invalid parameter {value!r}; expected KEY=VALUE")
        key = key.strip()
        if key in parameters:
            raise ValueError(f"Duplicate parameter: {key}")
        parameters[key] = parameter_value
    return parameters


def bind_source_release_parameters(
    parameters: dict[str, str], source_release_ids: list[int]
) -> dict[str, str]:
    """Bind the registered releases to the reserved SQL array parameter."""
    if "source_release_ids" in parameters:
        raise ValueError("source_release_ids is reserved; supply releases with --source-release-id")
    release_ids = sorted(set(source_release_ids))
    bound = dict(parameters)
    bound["source_release_ids"] = "{" + ",".join(str(value) for value in release_ids) + "}"
    return bound


def _relative_workspace_path(path: Path) -> str:
    return path.resolve().relative_to(Path.cwd().resolve()).as_posix()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_csv(
    output_path: Path,
    columns: list[str],
    rows: Iterable[Sequence[Any]],
    *,
    max_rows: int | None = None,
    max_bytes: int | None = None,
) -> tuple[int, str]:
    if output_path.exists():
        raise FileExistsError(f"Refusing to replace analysis artifact: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    record_buffer = StringIO(newline="")
    record_writer = csv.writer(record_buffer, lineterminator="\n")
    bytes_written = 0

    def write_record(output_file: Any, record: Sequence[Any]) -> None:
        nonlocal bytes_written
        record_buffer.seek(0)
        record_buffer.truncate(0)
        record_writer.writerow(record)
        encoded_record = record_buffer.getvalue().encode("utf-8")
        projected_bytes = bytes_written + len(encoded_record)
        if max_bytes is not None and projected_bytes > max_bytes:
            raise RuntimeError(f"Analysis output exceeded the {max_bytes}-byte safety cap")
        output_file.write(encoded_record)
        bytes_written = projected_bytes

    created_output = False
    try:
        with output_path.open("xb") as output_file:
            created_output = True
            write_record(output_file, columns)
            for row in rows:
                if max_rows is not None and row_count >= max_rows:
                    raise RuntimeError(f"Analysis output exceeded the {max_rows}-row safety cap")
                write_record(output_file, row)
                row_count += 1
        return row_count, _sha256_file(output_path)
    except BaseException as error:
        if created_output:
            try:
                output_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                error.add_note(
                    "Could not remove incomplete analysis output: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
        raise


def _remove_partial_output(output_path: Path) -> str | None:
    """Remove an unregistered output and return a diagnostic if cleanup fails."""
    try:
        output_path.unlink(missing_ok=True)
    except OSError as error:
        return f"Output cleanup failed: {type(error).__name__}: {error}"
    return None


def _record_unsuccessful_run(
    connection: psycopg.Connection[Any],
    analysis_run_id: int,
    *,
    status: str,
    detail: str,
) -> None:
    connection.rollback()
    with connection.transaction():
        connection.execute(
            """
            update analytics.analysis_run
            set status = %s, completed_at = now(),
                notes = concat_ws(E'\n', notes, %s::text)
            where analysis_run_id = %s
            """,
            (status, detail, analysis_run_id),
        )


def run_sql_analysis(
    database_url: str,
    query_path: Path,
    output_path: Path,
    *,
    case_id: str,
    name: str,
    code_commit: str,
    parameters: dict[str, str],
    source_release_ids: list[int],
    notes: str | None = None,
    statement_timeout_seconds: int = 120,
    max_output_rows: int = 100_000,
    max_output_bytes: int = 256 * 1024 * 1024,
) -> tuple[int, int, str]:
    """Run a read-only SQL artifact with explicit source-release provenance."""
    if statement_timeout_seconds <= 0:
        raise ValueError("statement_timeout_seconds must be positive")
    if max_output_rows <= 0:
        raise ValueError("max_output_rows must be positive")
    if max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be positive")
    parameters = bind_source_release_parameters(parameters, source_release_ids)
    query = query_path.read_text(encoding="utf-8")
    query_relative_path = _relative_workspace_path(query_path)
    output_relative_path = _relative_workspace_path(output_path)
    if output_path.exists():
        raise FileExistsError(f"Refusing to replace analysis artifact: {output_path}")

    with psycopg.connect(database_url) as connection:
        analysis_run_id = connection.execute(
            """
            insert into analytics.analysis_run (
                case_id, name, code_commit, query_path, parameters, status, notes
            )
            values (%s, %s, %s, %s, %s::jsonb, 'running', %s)
            returning analysis_run_id
            """,
            (
                case_id,
                name,
                code_commit,
                query_relative_path,
                psycopg.types.json.Json(parameters),
                notes,
            ),
        ).fetchone()[0]
        for source_release_id in sorted(set(source_release_ids)):
            connection.execute(
                """
                insert into analytics.analysis_run_source_release (
                    analysis_run_id, source_release_id
                )
                values (%s, %s)
                """,
                (analysis_run_id, source_release_id),
            )
        connection.commit()

        artifact_written = False
        try:
            with connection.transaction():
                connection.execute("set transaction read only")
                connection.execute(
                    "select set_config('statement_timeout', %s, true)",
                    (f"{statement_timeout_seconds}s",),
                )
                with connection.cursor(name=f"analysis_run_{analysis_run_id}") as cursor:
                    cursor.itersize = 1_000
                    cursor.execute(query, parameters)
                    if cursor.description is None:
                        raise ValueError("Analysis SQL must return a result set")
                    columns = [column.name for column in cursor.description]
                    row_count, artifact_hash = _write_csv(
                        output_path,
                        columns,
                        cursor,
                        max_rows=max_output_rows,
                        max_bytes=max_output_bytes,
                    )
                    artifact_written = True
            output_bytes = output_path.stat().st_size
            with connection.transaction():
                connection.execute(
                    """
                    insert into analytics.analysis_artifact (
                        analysis_run_id, relative_path, media_type, sha256, description
                    )
                    values (%s, %s, 'text/csv', %s, %s)
                    """,
                    (
                        analysis_run_id,
                        output_relative_path,
                        artifact_hash,
                        f"{row_count} rows; {output_bytes} bytes",
                    ),
                )
                connection.execute(
                    """
                    update analytics.analysis_run
                    set status = 'succeeded', completed_at = now()
                    where analysis_run_id = %s
                    """,
                    (analysis_run_id,),
                )
        except KeyboardInterrupt as error:
            cleanup_error = _remove_partial_output(output_path) if artifact_written else None
            detail = "Cancelled: KeyboardInterrupt"
            if cleanup_error is not None:
                detail = f"{detail}\n{cleanup_error}"
            try:
                _record_unsuccessful_run(
                    connection,
                    analysis_run_id,
                    status="cancelled",
                    detail=detail,
                )
            except Exception as recording_error:
                error.add_note(
                    "Could not record the cancelled analysis run: "
                    f"{type(recording_error).__name__}: {recording_error}"
                )
            raise
        except Exception as error:
            cleanup_error = _remove_partial_output(output_path) if artifact_written else None
            detail = f"Failure: {type(error).__name__}: {error}"
            if cleanup_error is not None:
                detail = f"{detail}\n{cleanup_error}"
            try:
                _record_unsuccessful_run(
                    connection,
                    analysis_run_id,
                    status="failed",
                    detail=detail,
                )
            except Exception as recording_error:
                error.add_note(
                    "Could not record the failed analysis run: "
                    f"{type(recording_error).__name__}: {recording_error}"
                )
            raise

    return analysis_run_id, row_count, artifact_hash
