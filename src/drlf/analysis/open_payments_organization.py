from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import psycopg

from drlf.analysis.part_d_first_pass import clean_repository_commit
from drlf.analysis.sql_runner import run_sql_analysis

AnalysisRunner = Callable[..., tuple[int, int, str]]
CommitResolver = Callable[[Path], str]

INCOMPLETE_FILENAME = "open-payments-organization-analysis.INCOMPLETE.json"
SUCCESS_FILENAME = "open-payments-organization-analysis.json"
QUERY_DIRECTORY = Path("sql/analysis/open_payments")


@dataclass(frozen=True)
class OrganizationAnalysisSpecification:
    name: str
    query_filename: str
    output_filename: str
    identifier_scope: str | None = None


ANALYSIS_SPECIFICATIONS = (
    OrganizationAnalysisSpecification(
        name="open-payments-organization-annual",
        query_filename="organization_annual_summary.sql",
        output_filename="open-payments-organization-annual-summary.csv",
    ),
    OrganizationAnalysisSpecification(
        name="open-payments-organization-recipients",
        query_filename="organization_recipient_panel.sql",
        output_filename="open-payments-organization-recipient-panel.csv",
    ),
    OrganizationAnalysisSpecification(
        name="open-payments-organization-roster-all",
        query_filename="organization_roster_transitions.sql",
        output_filename="open-payments-organization-roster-all.csv",
        identifier_scope="all",
    ),
    OrganizationAnalysisSpecification(
        name="open-payments-organization-roster-npi-only",
        query_filename="organization_roster_transitions.sql",
        output_filename="open-payments-organization-roster-npi-only.csv",
        identifier_scope="npi-only",
    ),
)


@dataclass(frozen=True)
class OrganizationAnalysisArtifact:
    name: str
    path: Path
    query_path: Path
    query_sha256: str
    parameters: dict[str, str]
    analysis_run_id: int
    rows: int
    sha256: str


@dataclass(frozen=True)
class OrganizationSourceRelease:
    source_release_id: int
    data_year: int
    manifest_path: str
    manifest_sha256: str


ReleaseValidator = Callable[..., tuple[OrganizationSourceRelease, ...]]


@dataclass(frozen=True)
class OrganizationAnalysisResult:
    output_dir: Path
    manifest_path: Path
    code_commit: str
    source_release_ids: tuple[int, ...]
    artifacts: tuple[OrganizationAnalysisArtifact, ...]


def _relative_workspace_path(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_state(path: Path, state: dict[str, Any], *, create: bool = False) -> None:
    if create and path.exists():
        raise FileExistsError(f"Refusing to replace bundle state: {path}")
    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        with temporary_path.open("x", encoding="utf-8", newline="\n") as state_file:
            json.dump(state, state_file, indent=2, sort_keys=True)
            state_file.write("\n")
            state_file.flush()
            os.fsync(state_file.fileno())
        temporary_path.replace(path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _validate_release_manifest(
    root: Path,
    *,
    source_release_id: int,
    data_year: int,
    manifest_path: str,
    manifest_sha256: str,
    organization_id: str,
) -> OrganizationSourceRelease:
    path = (root / manifest_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError(
            f"Source release {source_release_id} manifest is outside the repository"
        ) from error
    if not path.is_file():
        raise FileNotFoundError(f"Source release {source_release_id} manifest is missing: {path}")
    actual_manifest_sha256 = _sha256_file(path)
    if actual_manifest_sha256 != manifest_sha256:
        raise ValueError(
            f"Source release {source_release_id} manifest hash does not match PostgreSQL lineage"
        )
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(
            f"Source release {source_release_id} manifest is not valid UTF-8 JSON"
        ) from error
    if not isinstance(manifest, dict) or manifest.get("status") != "complete":
        raise ValueError(f"Source release {source_release_id} manifest must be complete")
    dataset = manifest.get("dataset")
    if not isinstance(dataset, dict):
        raise ValueError(f"Source release {source_release_id} manifest dataset is missing")
    if dataset.get("slug") != "open-payments-general-payments":
        raise ValueError(
            f"Source release {source_release_id} manifest is not General Payment data"
        )
    if dataset.get("data_year") != data_year:
        raise ValueError(
            f"Source release {source_release_id} PostgreSQL and manifest years do not match"
        )
    retrieval = manifest.get("retrieval")
    parameters = retrieval.get("parameters") if isinstance(retrieval, dict) else None
    exact_filters = parameters.get("exact_filters") if isinstance(parameters, dict) else None
    if not isinstance(exact_filters, dict):
        raise ValueError(f"Source release {source_release_id} exact filters are missing")
    normalized_filters = {
        str(key).strip().casefold(): str(value).strip() for key, value in exact_filters.items()
    }
    expected_filters = {
        "applicable_manufacturer_or_applicable_gpo_making_payment_id": organization_id
    }
    if normalized_filters != expected_filters:
        raise ValueError(
            f"Source release {source_release_id} must use only the exact paying-entity filter "
            f"for {organization_id}; observed {normalized_filters}"
        )
    return OrganizationSourceRelease(
        source_release_id=source_release_id,
        data_year=data_year,
        manifest_path=manifest_path,
        manifest_sha256=manifest_sha256,
    )


def validate_open_payments_organization_releases(
    database_url: str,
    *,
    source_release_ids: Sequence[int],
    organization_id: str,
    years: Sequence[int],
    repository_root: Path,
) -> tuple[OrganizationSourceRelease, ...]:
    """Validate exact organization-only General Payment releases before analysis."""
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute("set transaction read only")
        rows = connection.execute(
            """
            select
                release.source_release_id,
                release.data_year,
                dataset.slug,
                release.status,
                release.manifest_path,
                release.manifest_sha256
            from metadata.source_release as release
            join metadata.source_dataset as dataset using (dataset_id)
            where release.source_release_id = any(%s)
            order by release.data_year, release.source_release_id
            """,
            (list(source_release_ids),),
        ).fetchall()

    observed_ids = {int(row[0]) for row in rows}
    missing_ids = sorted(set(source_release_ids) - observed_ids)
    if missing_ids:
        raise ValueError("Unknown source release IDs: " + ", ".join(map(str, missing_ids)))

    expected_years = set(years)
    observed_years: set[int] = set()
    validated: list[OrganizationSourceRelease] = []
    for release_id, data_year, dataset_slug, status, manifest_path, manifest_hash in rows:
        if dataset_slug != "open-payments-general-payments" or status != "loaded":
            raise ValueError(
                f"Source release {release_id} must be a loaded Open Payments General "
                "Payment release"
            )
        year = int(data_year)
        if year not in expected_years or year in observed_years:
            raise ValueError(
                "Source releases must provide exactly one loaded General Payment release per "
                "requested year"
            )
        observed_years.add(year)
        validated.append(
            _validate_release_manifest(
                repository_root,
                source_release_id=int(release_id),
                data_year=year,
                manifest_path=str(manifest_path),
                manifest_sha256=str(manifest_hash),
                organization_id=organization_id,
            )
        )
    if observed_years != expected_years:
        missing_years = sorted(expected_years - observed_years)
        raise ValueError(
            "Source releases are missing requested years: " + ", ".join(map(str, missing_years))
        )
    return tuple(validated)


def _validate_inputs(
    *,
    case_id: str,
    organization_id: str,
    payment_nature: str,
    from_year: int,
    to_year: int,
    source_release_ids: Sequence[int],
) -> tuple[str, str, tuple[int, ...]]:
    if not re.fullmatch(r"CASE-[0-9]{4}", case_id):
        raise ValueError("case_id must use CASE-NNNN syntax")
    normalized_organization_id = organization_id.strip()
    if not normalized_organization_id:
        raise ValueError("organization_id must not be blank")
    normalized_payment_nature = payment_nature.strip()
    if not normalized_payment_nature:
        raise ValueError("payment_nature must not be blank")
    if from_year < 2013 or to_year > 2200 or from_year > to_year:
        raise ValueError("year range must be ordered and begin no earlier than 2013")
    if to_year - from_year > 20:
        raise ValueError("organization analysis is limited to 21 years per run")
    if not source_release_ids:
        raise ValueError("at least one source_release_id is required")
    if any(release_id < 1 for release_id in source_release_ids):
        raise ValueError("source_release_ids must be positive integers")
    normalized_release_ids = tuple(sorted(set(source_release_ids)))
    if len(normalized_release_ids) != len(source_release_ids):
        raise ValueError("source_release_ids must not contain duplicates")
    expected_release_count = to_year - from_year + 1
    if len(normalized_release_ids) != expected_release_count:
        raise ValueError(
            "organization analysis requires exactly one source release ID per requested year"
        )
    return normalized_organization_id, normalized_payment_nature, normalized_release_ids


def run_open_payments_organization_analysis(
    database_url: str,
    output_dir: Path,
    *,
    case_id: str,
    organization_id: str,
    from_year: int,
    to_year: int,
    source_release_ids: Sequence[int],
    payment_nature: str,
    repository_root: Path | None = None,
    commit_resolver: CommitResolver = clean_repository_commit,
    release_validator: ReleaseValidator = validate_open_payments_organization_releases,
    analysis_runner: AnalysisRunner = run_sql_analysis,
) -> OrganizationAnalysisResult:
    """Run the four standard Open Payments organization analyses as one bounded bundle."""
    organization_id, payment_nature, release_ids = _validate_inputs(
        case_id=case_id,
        organization_id=organization_id,
        payment_nature=payment_nature,
        from_year=from_year,
        to_year=to_year,
        source_release_ids=source_release_ids,
    )
    root = (repository_root or Path.cwd()).resolve()
    output = output_dir.resolve()
    _relative_workspace_path(output, root)

    case_matches = sorted((root / "research" / "cases").glob(f"{case_id}-*"))
    if len(case_matches) != 1 or not case_matches[0].is_dir():
        raise ValueError(f"Expected exactly one existing case workspace for {case_id}")
    case_directory = case_matches[0].resolve()
    try:
        output.relative_to(case_directory)
    except ValueError as error:
        raise ValueError(
            f"Open Payments organization output must be inside the {case_id} case workspace"
        ) from error
    if output.exists():
        incomplete = output / INCOMPLETE_FILENAME
        detail = f"; inspect {incomplete}" if incomplete.is_file() else ""
        raise FileExistsError(
            f"Refusing to replace existing Open Payments organization output: {output}{detail}"
        )

    query_root = root / QUERY_DIRECTORY
    query_paths = {
        specification.query_filename: query_root / specification.query_filename
        for specification in ANALYSIS_SPECIFICATIONS
    }
    missing_queries = [str(path) for path in query_paths.values() if not path.is_file()]
    if missing_queries:
        raise FileNotFoundError(
            "Missing Open Payments organization SQL: " + ", ".join(missing_queries)
        )

    validated_releases = release_validator(
        database_url,
        source_release_ids=release_ids,
        organization_id=organization_id,
        years=tuple(range(from_year, to_year + 1)),
        repository_root=root,
    )
    code_commit = commit_resolver(root)
    output.mkdir(parents=True, exist_ok=True)
    incomplete_path = output / INCOMPLETE_FILENAME
    state: dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "case_id": case_id,
        "organization_id": organization_id,
        "payment_nature": payment_nature,
        "from_year": from_year,
        "to_year": to_year,
        "code_commit": code_commit,
        "source_release_ids": list(release_ids),
        "source_releases": [asdict(release) for release in validated_releases],
        "completed_analyses": [],
    }
    _write_state(incomplete_path, state, create=True)

    common_parameters = {
        "organization_id": organization_id,
        "from_year": str(from_year),
        "to_year": str(to_year),
    }
    artifacts: list[OrganizationAnalysisArtifact] = []
    try:
        for specification in ANALYSIS_SPECIFICATIONS:
            parameters = dict(common_parameters)
            if specification.identifier_scope is not None:
                parameters.update(
                    {
                        "payment_nature": payment_nature,
                        "identifier_scope": specification.identifier_scope,
                    }
                )
            artifact_path = output / specification.output_filename
            run_id, rows, artifact_hash = analysis_runner(
                database_url,
                query_paths[specification.query_filename],
                artifact_path,
                case_id=case_id,
                name=specification.name,
                code_commit=code_commit,
                parameters=parameters,
                source_release_ids=list(release_ids),
                notes=(
                    f"Bounded Open Payments organization analysis for paying-entity ID "
                    f"{organization_id}, program years {from_year}-{to_year}. Reported transfer "
                    "records are relationship context, not healthcare claims or program loss."
                ),
            )
            artifact = OrganizationAnalysisArtifact(
                name=specification.name,
                path=artifact_path,
                query_path=query_paths[specification.query_filename],
                query_sha256=_sha256_file(query_paths[specification.query_filename]),
                parameters=parameters,
                analysis_run_id=run_id,
                rows=rows,
                sha256=artifact_hash,
            )
            artifacts.append(artifact)
            state["completed_analyses"] = [
                {
                    **asdict(item),
                    "path": _relative_workspace_path(item.path, root),
                    "query_path": _relative_workspace_path(item.query_path, root),
                }
                for item in artifacts
            ]
            _write_state(incomplete_path, state)
    except Exception as error:
        state["status"] = "failed"
        state["error"] = f"{type(error).__name__}: {error}"
        _write_state(incomplete_path, state)
        raise

    state["status"] = "succeeded"
    state["artifacts"] = state.pop("completed_analyses")
    _write_state(incomplete_path, state)
    success_path = output / SUCCESS_FILENAME
    incomplete_path.replace(success_path)
    return OrganizationAnalysisResult(
        output_dir=output,
        manifest_path=success_path,
        code_commit=code_commit,
        source_release_ids=release_ids,
        artifacts=tuple(artifacts),
    )
