from __future__ import annotations

import csv
import json
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import psycopg

from drlf.analysis.sql_runner import run_sql_analysis
from drlf.reporting.first_pass import (
    MISSING_EVIDENCE_CHECKLIST,
    ROLE_AND_MONEY_CAVEATS,
    render_first_pass_markdown,
)
from drlf.sources.part_d_geography_manifest import (
    DATASET_SLUG as GEOGRAPHY_DATASET_SLUG,
)
from drlf.sources.part_d_manifest import (
    DATASET_SLUG as PROVIDER_DATASET_SLUG,
)

FIRST_PASS_SCHEMA_VERSION = 1
QUERY_FILENAMES = (
    "trajectory.sql",
    "portfolio.sql",
    "leaders.sql",
    "churn.sql",
    "national_denominators.sql",
)

AnalysisRunner = Callable[..., tuple[int, int, str]]


@dataclass(frozen=True)
class ReleaseCandidate:
    source_release_id: int
    dataset_slug: str
    data_year: int
    version_id: str
    observed_at: str
    manifest_path: str
    manifest_sha256: str
    filters: dict[str, str]


@dataclass(frozen=True)
class ReleaseSelection:
    provider_by_year: dict[int, ReleaseCandidate]
    geography_by_year: dict[int, ReleaseCandidate]
    portfolio_by_year: dict[int, ReleaseCandidate]
    full_portfolio_years: tuple[int, ...]

    @property
    def provider_ids(self) -> list[int]:
        return _unique_ids(self.provider_by_year.values())

    @property
    def geography_ids(self) -> list[int]:
        return _unique_ids(self.geography_by_year.values())

    @property
    def portfolio_ids(self) -> list[int]:
        return _unique_ids(self.portfolio_by_year.values())

    @property
    def full_portfolio_ids(self) -> list[int]:
        releases = (
            self.portfolio_by_year[year]
            for year in self.full_portfolio_years
            if year in self.portfolio_by_year
        )
        return _unique_ids(releases)


@dataclass(frozen=True)
class FirstPassResult:
    output_dir: Path
    report_path: Path
    manifest_path: Path
    warnings: tuple[str, ...]
    analysis_run_ids: tuple[int, ...]


def _unique_ids(releases: Iterable[ReleaseCandidate]) -> list[int]:
    return sorted({release.source_release_id for release in releases})


def _normalized_filters(filters: Mapping[str, Any]) -> dict[str, str]:
    return {str(key).casefold(): str(value).casefold() for key, value in filters.items()}


def _latest(releases: Sequence[ReleaseCandidate]) -> ReleaseCandidate:
    return max(releases, key=lambda release: (release.observed_at, release.source_release_id))


def select_source_releases(
    candidates: Sequence[ReleaseCandidate],
    *,
    candidate_npi: str,
    brand_name: str,
    generic_name: str,
    years: Sequence[int],
) -> ReleaseSelection:
    """Select one latest targeted or complete-annual release per year and role."""
    provider_by_year: dict[int, ReleaseCandidate] = {}
    geography_by_year: dict[int, ReleaseCandidate] = {}
    portfolio_by_year: dict[int, ReleaseCandidate] = {}
    full_portfolio_years: list[int] = []
    normalized_brand = brand_name.casefold()
    normalized_generic = generic_name.casefold()

    for year in years:
        year_candidates = [candidate for candidate in candidates if candidate.data_year == year]
        brand_generic_provider_matches = [
            candidate
            for candidate in year_candidates
            if candidate.dataset_slug == PROVIDER_DATASET_SLUG
            and _normalized_filters(candidate.filters)
            == {"brnd_name": normalized_brand, "gnrc_name": normalized_generic}
        ]
        brand_provider_matches = [
            candidate
            for candidate in year_candidates
            if candidate.dataset_slug == PROVIDER_DATASET_SLUG
            and _normalized_filters(candidate.filters) == {"brnd_name": normalized_brand}
        ]
        full_provider_matches = [
            candidate
            for candidate in year_candidates
            if candidate.dataset_slug == PROVIDER_DATASET_SLUG
            and _normalized_filters(candidate.filters) == {}
        ]
        brand_generic_geography_matches = [
            candidate
            for candidate in year_candidates
            if candidate.dataset_slug == GEOGRAPHY_DATASET_SLUG
            and _normalized_filters(candidate.filters)
            == {"brnd_name": normalized_brand, "gnrc_name": normalized_generic}
        ]
        brand_geography_matches = [
            candidate
            for candidate in year_candidates
            if candidate.dataset_slug == GEOGRAPHY_DATASET_SLUG
            and _normalized_filters(candidate.filters) == {"brnd_name": normalized_brand}
        ]
        full_geography_matches = [
            candidate
            for candidate in year_candidates
            if candidate.dataset_slug == GEOGRAPHY_DATASET_SLUG
            and _normalized_filters(candidate.filters) == {}
        ]
        npi_matches = [
            candidate
            for candidate in year_candidates
            if candidate.dataset_slug == PROVIDER_DATASET_SLUG
            and _normalized_filters(candidate.filters) == {"prscrbr_npi": candidate_npi}
        ]

        compatible_provider = [
            *brand_generic_provider_matches,
            *brand_provider_matches,
            *full_provider_matches,
        ]
        if compatible_provider:
            provider_by_year[year] = _latest(compatible_provider)
        compatible_geography = [
            *brand_generic_geography_matches,
            *brand_geography_matches,
            *full_geography_matches,
        ]
        if compatible_geography:
            geography_by_year[year] = _latest(compatible_geography)

        portfolio_candidates = [*npi_matches, *full_provider_matches]
        if portfolio_candidates:
            provider_version = provider_by_year.get(year)
            same_version = (
                [
                    candidate
                    for candidate in portfolio_candidates
                    if candidate.version_id == provider_version.version_id
                ]
                if provider_version
                else []
            )
            selected_portfolio = _latest(same_version or portfolio_candidates)
            portfolio_by_year[year] = selected_portfolio
            full_portfolio_years.append(year)
        elif year in provider_by_year:
            portfolio_by_year[year] = provider_by_year[year]

    if not provider_by_year:
        raise ValueError(
            f"No loaded targeted or complete Provider-and-Drug releases found for {brand_name!r}"
        )

    return ReleaseSelection(
        provider_by_year=provider_by_year,
        geography_by_year=geography_by_year,
        portfolio_by_year=portfolio_by_year,
        full_portfolio_years=tuple(sorted(full_portfolio_years)),
    )


def resolve_source_releases(
    database_url: str,
    *,
    candidate_npi: str,
    brand_name: str,
    generic_name: str,
    years: Sequence[int],
    repository_root: Path,
) -> ReleaseSelection:
    """Resolve loaded releases and verify their committed manifests before analysis."""
    with psycopg.connect(database_url) as connection:
        rows = connection.execute(
            """
            select
                release.source_release_id,
                dataset.slug,
                release.data_year,
                release.version_id,
                release.observed_at,
                release.manifest_path,
                release.manifest_sha256
            from metadata.source_release as release
            join metadata.source_dataset as dataset using (dataset_id)
            where dataset.slug = any(%s)
              and release.status = 'loaded'
              and release.data_year = any(%s)
              and release.manifest_path is not null
              and release.manifest_sha256 is not null
            order by release.data_year, release.observed_at, release.source_release_id
            """,
            ([PROVIDER_DATASET_SLUG, GEOGRAPHY_DATASET_SLUG], list(years)),
        ).fetchall()

    candidates: list[ReleaseCandidate] = []
    for (
        release_id,
        dataset_slug,
        data_year,
        version_id,
        observed_at,
        manifest_path,
        stored_hash,
    ) in rows:
        path = repository_root / manifest_path
        if not path.is_file():
            raise FileNotFoundError(f"Loaded source manifest is missing: {path}")
        manifest_bytes = path.read_bytes()
        actual_hash = sha256(manifest_bytes).hexdigest()
        expected_hash = str(stored_hash).strip()
        if actual_hash != expected_hash:
            raise ValueError(f"Loaded source manifest hash differs from the database: {path}")
        manifest = json.loads(manifest_bytes)
        filters = manifest.get("retrieval", {}).get("parameters", {}).get("filters", {})
        if not isinstance(filters, dict):
            raise ValueError(f"Source manifest filters are not an object: {path}")
        candidates.append(
            ReleaseCandidate(
                source_release_id=int(release_id),
                dataset_slug=str(dataset_slug),
                data_year=int(data_year),
                version_id=str(version_id or ""),
                observed_at=observed_at.isoformat(),
                manifest_path=str(manifest_path),
                manifest_sha256=expected_hash,
                filters={str(key): str(value) for key, value in filters.items()},
            )
        )

    return select_source_releases(
        candidates,
        candidate_npi=candidate_npi,
        brand_name=brand_name,
        generic_name=generic_name,
        years=years,
    )


def _pg_array(values: Sequence[int]) -> str:
    return "{" + ",".join(str(value) for value in values) + "}"


def _relative_path(path: Path, repository_root: Path) -> str:
    try:
        return path.resolve().relative_to(repository_root.resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"First-pass output must be inside the repository: {path}") from error


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as input_file:
        return list(csv.DictReader(input_file))


def _available_denominator_years(rows: Sequence[Mapping[str, str]]) -> set[int]:
    return {
        int(row["data_year"])
        for row in rows
        if row.get("data_year") not in (None, "")
        and row.get("national_denominator_status") == "available"
    }


def clean_repository_commit(root: Path) -> str:
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status.strip():
        raise ValueError(
            "Refusing to register provenance from a dirty Git worktree; "
            "commit the coherent research/code change first"
        )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _source_release_rows(selection: ReleaseSelection) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(role: str, year: int, release: ReleaseCandidate) -> None:
        row = asdict(release)
        row["role"] = role
        row["data_year"] = year
        row["filters"] = json.dumps(release.filters, sort_keys=True, separators=(",", ":"))
        rows.append(row)

    for year, release in selection.provider_by_year.items():
        add("provider-brand-cohort", year, release)
    for year, release in selection.geography_by_year.items():
        add("geography-national-denominator", year, release)
    for year, release in selection.portfolio_by_year.items():
        role = (
            "candidate-all-drug"
            if year in selection.full_portfolio_years
            else "candidate-brand-fallback"
        )
        add(role, year, release)
    return sorted(rows, key=lambda row: (row["data_year"], row["role"]))


def _write_source_releases(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    columns = (
        "role",
        "data_year",
        "source_release_id",
        "dataset_slug",
        "version_id",
        "observed_at",
        "manifest_path",
        "manifest_sha256",
        "filters",
    )
    with path.open("x", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})


def _coverage_warnings(
    selection: ReleaseSelection,
    *,
    requested_years: Sequence[int],
    trajectory_rows: Sequence[Mapping[str, str]],
    denominator_rows: Sequence[Mapping[str, str]],
) -> list[str]:
    warnings: list[str] = []
    requested = set(requested_years)
    provider_years = set(selection.provider_by_year)
    fallback_years = set(selection.portfolio_by_year) - set(selection.full_portfolio_years)
    candidate_years = {
        int(row["data_year"]) for row in trajectory_rows if row.get("data_year") not in (None, "")
    }

    missing_provider = sorted(requested - provider_years)
    if missing_provider:
        warnings.append(
            "No exact-brand Provider-and-Drug cohort release was loaded for years: "
            + ", ".join(map(str, missing_provider))
            + "."
        )
    available_denominator_years = _available_denominator_years(denominator_rows)
    missing_denominators = sorted(requested - available_denominator_years)
    if missing_denominators:
        warnings.append(
            "The authoritative Geography-and-Drug national denominator is missing for years: "
            + ", ".join(map(str, missing_denominators))
            + "; visible provider sums were not substituted."
        )
    if fallback_years:
        warnings.append(
            "No candidate-NPI all-drug release was loaded for years: "
            + ", ".join(map(str, sorted(fallback_years)))
            + "; portfolio output is limited to the target brand in those years."
        )
    missing_candidate = sorted(provider_years - candidate_years)
    if missing_candidate:
        warnings.append(
            "No visible candidate row appeared in the loaded brand cohort for years: "
            + ", ".join(map(str, missing_candidate))
            + "; absence can reflect no row or public-file suppression."
        )
    if not any(year + 1 in provider_years for year in provider_years):
        warnings.append("No adjacent pair of loaded brand-cohort years was available for churn.")
    mixed_versions = sorted(
        year
        for year, provider in selection.provider_by_year.items()
        if year in selection.full_portfolio_years
        and (portfolio := selection.portfolio_by_year.get(year)) is not None
        and provider.version_id != portfolio.version_id
    )
    if mixed_versions:
        warnings.append(
            "Candidate portfolio and product cohort use different CMS version IDs for years: "
            + ", ".join(map(str, mixed_versions))
            + "; compare revision differences before interpreting the portfolio."
        )
    return warnings


def _validate_inputs(
    *,
    candidate_npi: str,
    case_id: str,
    brand_name: str,
    generic_name: str,
    from_year: int,
    to_year: int,
) -> None:
    if not re.fullmatch(r"[0-9]{10}", candidate_npi):
        raise ValueError("candidate_npi must contain exactly ten digits")
    if not re.fullmatch(r"CASE-[0-9]{4}", case_id):
        raise ValueError("case_id must use CASE-NNNN syntax")
    if not brand_name.strip():
        raise ValueError("brand_name must not be blank")
    if not generic_name.strip():
        raise ValueError("generic_name must not be blank")
    if from_year < 2013 or to_year > 2200 or from_year > to_year:
        raise ValueError("year range must be ordered and begin no earlier than 2013")
    if to_year - from_year > 20:
        raise ValueError("first-pass analysis is limited to 21 years per run")


def run_part_d_first_pass(
    database_url: str,
    output_dir: Path,
    *,
    case_id: str,
    candidate_npi: str,
    brand_name: str,
    generic_name: str,
    from_year: int,
    to_year: int,
    leader_limit: int = 25,
    repository_root: Path | None = None,
    code_commit: str | None = None,
    release_resolver: Callable[..., ReleaseSelection] = resolve_source_releases,
    analysis_runner: AnalysisRunner = run_sql_analysis,
    generated_at: datetime | None = None,
) -> FirstPassResult:
    """Create a bounded, provenance-linked first-pass bundle from loaded Part D data."""
    _validate_inputs(
        candidate_npi=candidate_npi,
        case_id=case_id,
        brand_name=brand_name,
        generic_name=generic_name,
        from_year=from_year,
        to_year=to_year,
    )
    if leader_limit < 1 or leader_limit > 100:
        raise ValueError("leader_limit must be between 1 and 100")
    root = (repository_root or Path.cwd()).resolve()
    output = output_dir.resolve()
    _relative_path(output, root)
    case_matches = sorted((root / "research" / "cases").glob(f"{case_id}-*"))
    if len(case_matches) != 1 or not case_matches[0].is_dir():
        raise ValueError(f"Expected exactly one existing case workspace for {case_id}")
    case_directory = case_matches[0].resolve()
    try:
        output.relative_to(case_directory)
    except ValueError as error:
        message = f"First-pass output must be inside the {case_id} case workspace"
        raise ValueError(message) from error
    if output.exists():
        incomplete = output / "INCOMPLETE.json"
        detail = f"; inspect {incomplete}" if incomplete.is_file() else ""
        raise FileExistsError(f"Refusing to replace existing first-pass output: {output}{detail}")

    years = list(range(from_year, to_year + 1))
    selection = release_resolver(
        database_url,
        candidate_npi=candidate_npi,
        brand_name=brand_name,
        generic_name=generic_name,
        years=years,
        repository_root=root,
    )
    commit = code_commit or clean_repository_commit(root)
    query_root = root / "sql" / "analysis" / "part_d"
    query_paths = {filename: query_root / filename for filename in QUERY_FILENAMES}
    missing_queries = [str(path) for path in query_paths.values() if not path.is_file()]
    if missing_queries:
        raise FileNotFoundError("Missing first-pass SQL: " + ", ".join(missing_queries))

    output.mkdir(parents=True)
    incomplete_path = output / "INCOMPLETE.json"
    incomplete_state: dict[str, Any] = {
        "status": "running",
        "case_id": case_id,
        "candidate_npi": candidate_npi,
        "brand_name": brand_name,
        "generic_name": generic_name,
        "from_year": from_year,
        "to_year": to_year,
        "code_commit": commit,
        "completed_analyses": [],
    }
    incomplete_path.write_text(
        json.dumps(incomplete_state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    common_parameters = {
        "candidate_npi": candidate_npi,
        "brand_name": brand_name,
        "generic_name": generic_name,
        "from_year": str(from_year),
        "to_year": str(to_year),
        "provider_release_ids": _pg_array(selection.provider_ids),
        "geography_release_ids": _pg_array(selection.geography_ids),
        "portfolio_release_ids": _pg_array(selection.portfolio_ids),
        "full_portfolio_release_ids": _pg_array(selection.full_portfolio_ids),
        "cohort_years": _pg_array(sorted(selection.provider_by_year)),
        "leader_limit": str(leader_limit),
    }
    specifications = (
        (
            "trajectory",
            "trajectory.sql",
            "trajectory.csv",
            selection.provider_ids + selection.geography_ids,
        ),
        ("portfolio", "portfolio.sql", "portfolio.csv", selection.portfolio_ids),
        ("leaders", "leaders.sql", "leaders.csv", selection.provider_ids + selection.geography_ids),
        ("churn", "churn.sql", "churn.csv", selection.provider_ids),
        (
            "national-denominators",
            "national_denominators.sql",
            "national-denominators.csv",
            selection.geography_ids,
        ),
    )

    analysis_artifacts: list[dict[str, Any]] = []
    try:
        for analysis_name, query_filename, output_filename, release_ids in specifications:
            artifact_path = output / output_filename
            run_id, rows, artifact_hash = analysis_runner(
                database_url,
                query_paths[query_filename],
                artifact_path,
                case_id=case_id,
                name=f"part-d-first-pass-{analysis_name}",
                code_commit=commit,
                parameters=common_parameters,
                source_release_ids=sorted(set(release_ids)),
                notes=(
                    f"Bounded first pass for NPI {candidate_npi}, brand {brand_name}, "
                    f"generic {generic_name}, years {from_year}-{to_year}."
                ),
            )
            analysis_artifacts.append(
                {
                    "name": analysis_name,
                    "path": _relative_path(artifact_path, root),
                    "rows": rows,
                    "sha256": artifact_hash,
                    "analysis_run_id": run_id,
                    "query_path": _relative_path(query_paths[query_filename], root),
                    "query_sha256": _sha256(query_paths[query_filename]),
                }
            )
            incomplete_state["completed_analyses"] = analysis_artifacts
            incomplete_path.write_text(
                json.dumps(incomplete_state, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
    except Exception as error:
        incomplete_state["status"] = "failed"
        incomplete_state["error"] = f"{type(error).__name__}: {error}"
        incomplete_path.write_text(
            json.dumps(incomplete_state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        raise

    incomplete_state["status"] = "analyses-complete"
    incomplete_state["phase"] = "assembling-source-register-report-and-manifest"
    incomplete_path.write_text(
        json.dumps(incomplete_state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    trajectory_rows = _read_csv(output / "trajectory.csv")
    denominator_rows = _read_csv(output / "national-denominators.csv")
    warnings = _coverage_warnings(
        selection,
        requested_years=years,
        trajectory_rows=trajectory_rows,
        denominator_rows=denominator_rows,
    )
    source_rows = _source_release_rows(selection)
    source_path = output / "source-releases.csv"
    _write_source_releases(source_path, source_rows)
    source_artifact = {
        "name": "source-releases",
        "path": _relative_path(source_path, root),
        "rows": len(source_rows),
        "sha256": _sha256(source_path),
        "analysis_run_id": None,
        "query_path": None,
        "query_sha256": None,
    }

    report_path = output / "first-pass.md"
    report_text = render_first_pass_markdown(
        case_id=case_id,
        candidate_npi=candidate_npi,
        brand_name=brand_name,
        generic_name=generic_name,
        from_year=from_year,
        to_year=to_year,
        code_commit=commit,
        warnings=warnings,
        trajectory_rows=trajectory_rows,
        source_releases=source_rows,
        artifacts=[*analysis_artifacts, source_artifact],
    )
    report_path.write_text(report_text, encoding="utf-8", newline="\n")
    report_artifact = {
        "name": "first-pass-report",
        "path": _relative_path(report_path, root),
        "rows": None,
        "sha256": _sha256(report_path),
        "analysis_run_id": None,
        "query_path": None,
        "query_sha256": None,
    }

    created = generated_at or datetime.now(UTC)
    manifest = {
        "schema_version": FIRST_PASS_SCHEMA_VERSION,
        "generated_at": created.isoformat().replace("+00:00", "Z"),
        "case_id": case_id,
        "candidate_npi": candidate_npi,
        "brand_name": brand_name,
        "generic_name": generic_name,
        "from_year": from_year,
        "to_year": to_year,
        "leader_limit": leader_limit,
        "code_commit": commit,
        "scope": "already-loaded targeted or complete-annual public Part D releases",
        "coverage": {
            "provider_brand_years": sorted(selection.provider_by_year),
            "selected_geography_release_years": sorted(selection.geography_by_year),
            "usable_national_denominator_years": sorted(
                _available_denominator_years(denominator_rows)
            ),
            "full_candidate_portfolio_years": list(selection.full_portfolio_years),
            "target_brand_portfolio_fallback_years": sorted(
                set(selection.portfolio_by_year) - set(selection.full_portfolio_years)
            ),
        },
        "warnings": warnings,
        "role_and_money_caveats": list(ROLE_AND_MONEY_CAVEATS),
        "missing_evidence_checklist": list(MISSING_EVIDENCE_CHECKLIST),
        "source_releases": source_rows,
        "artifacts": [*analysis_artifacts, source_artifact, report_artifact],
    }
    manifest_path = output / "first-pass.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    incomplete_path.unlink()

    return FirstPassResult(
        output_dir=output,
        report_path=report_path,
        manifest_path=manifest_path,
        warnings=tuple(warnings),
        analysis_run_ids=tuple(
            int(artifact["analysis_run_id"])
            for artifact in analysis_artifacts
            if artifact["analysis_run_id"] is not None
        ),
    )
