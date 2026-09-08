from __future__ import annotations

import argparse
import csv
import io
import json
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from drlf.analysis import part_b_shortlist

DEMO_ID = "synthetic-part-b-shortlist-v1"
MAX_DEMO_INPUT_BYTES = 1_000_000
DEFAULT_DEMO_INPUT = Path("examples/synthetic-part-b-screen/scan.csv")
DEFAULT_DEMO_INPUT_SHA256 = "6680b16dbe10035eb153fd732eb62fcd41e187706ca7d45544555bde883b8f8e"
SYNTHETIC_MARKER_COLUMN = "synthetic_record"
SYNTHETIC_IDENTIFIER_PREFIX = "SYNTHETIC-"


@dataclass(frozen=True)
class SyntheticDemoResult:
    input_sha256: str
    shortlist_path: Path
    shortlist_sha256: str
    manifest_path: Path
    manifest_sha256: str
    selected_rows: int


@dataclass(frozen=True)
class _InputSummary:
    row_count: int
    data_years: tuple[int, ...]
    latest_year_rows: int


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_source_file_lf(path: Path) -> str:
    normalized = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return sha256(normalized.encode("utf-8")).hexdigest()


def _input_reference(input_path: Path) -> str:
    resolved = input_path.resolve()
    possible_roots = (Path(__file__).resolve().parents[2], Path.cwd().resolve())
    for root in possible_roots:
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError:
            continue
    return str(resolved)


def _read_synthetic_input(input_path: Path) -> bytes:
    if not input_path.is_file():
        raise FileNotFoundError(f"Synthetic demo input does not exist: {input_path}")
    with input_path.open("rb") as input_file:
        content = input_file.read(MAX_DEMO_INPUT_BYTES + 1)
    if len(content) > MAX_DEMO_INPUT_BYTES:
        raise ValueError("Synthetic demo input exceeds its 1,000,000-byte safety limit")
    return content


def _inspect_synthetic_input(content: bytes) -> _InputSummary:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Synthetic demo input is not valid UTF-8") from error
    years: list[int] = []
    with io.StringIO(text, newline="") as input_file:
        reader = csv.DictReader(input_file)
        if SYNTHETIC_MARKER_COLUMN not in (reader.fieldnames or []):
            raise ValueError(f"Synthetic demo input is missing {SYNTHETIC_MARKER_COLUMN!r}")
        for row_number, row in enumerate(reader, start=2):
            if row[SYNTHETIC_MARKER_COLUMN].strip().lower() != "true":
                raise ValueError(
                    f"Synthetic demo row {row_number} is not explicitly marked synthetic"
                )
            rendering_npi = row.get("rendering_npi", "").strip()
            if not rendering_npi.startswith(SYNTHETIC_IDENTIFIER_PREFIX):
                raise ValueError(f"Synthetic demo row {row_number} has a non-synthetic identifier")
            try:
                years.append(int(row.get("data_year", "")))
            except ValueError:
                raise ValueError(
                    f"Synthetic demo row {row_number} has an invalid data_year"
                ) from None

    if not years:
        raise ValueError("Synthetic demo input contains no data rows")
    latest_year = max(years)
    return _InputSummary(
        row_count=len(years),
        data_years=tuple(sorted(set(years))),
        latest_year_rows=sum(year == latest_year for year in years),
    )


def run_synthetic_part_b_demo(
    input_path: Path,
    output_dir: Path,
    *,
    allow_unpinned_input: bool = False,
) -> SyntheticDemoResult:
    """Run the maintained Part B shortlist logic against an explicitly synthetic fixture."""
    input_content = _read_synthetic_input(input_path)
    summary = _inspect_synthetic_input(input_content)
    input_sha256 = sha256(input_content).hexdigest()
    pinned_fixture_verified = input_sha256 == DEFAULT_DEMO_INPUT_SHA256
    if not pinned_fixture_verified and not allow_unpinned_input:
        raise ValueError(
            "Synthetic demo input does not match the pinned committed fixture; "
            "explicitly allow an unpinned input only after verifying it contains no real data"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    shortlist_path = output_dir / "synthetic-part-b-shortlist.csv"
    manifest_path = output_dir / "synthetic-part-b-demo-run.json"
    existing = [path for path in (shortlist_path, manifest_path) if path.exists()]
    if existing:
        raise FileExistsError(
            "Refusing to replace synthetic demo output: "
            + ", ".join(str(path) for path in existing)
        )

    input_size = len(input_content)
    with tempfile.TemporaryDirectory(prefix=".synthetic-demo-input-", dir=output_dir) as temp_dir:
        snapshot_path = Path(temp_dir) / "scan.csv"
        snapshot_path.write_bytes(input_content)
        selected_rows, shortlist_sha256 = part_b_shortlist.generate_part_b_shortlist(
            snapshot_path,
            shortlist_path,
        )
    shortlist_size = shortlist_path.stat().st_size
    latest_year = max(summary.data_years)
    if pinned_fixture_verified:
        notice = (
            "Verified pinned, fully synthetic teaching data; no row represents a real provider, "
            "beneficiary, claim, payment, or allegation."
        )
        input_assurance = "pinned-committed-fixture"
    else:
        notice = (
            "Unpinned input explicitly allowed by the operator. Marker checks passed, but this "
            "tool does not independently establish that the input contains only synthetic data."
        )
        input_assurance = "operator-asserted-unpinned-input"
    manifest = {
        "demo": {
            "id": DEMO_ID,
            "input_assurance": input_assurance,
            "notice": notice,
            "pinned_fixture_verified": pinned_fixture_verified,
        },
        "input": {
            "bytes": input_size,
            "capture": "bounded-single-read-snapshot",
            "data_years": list(summary.data_years),
            "file": input_path.name,
            "latest_year": latest_year,
            "latest_year_rows": summary.latest_year_rows,
            "path": _input_reference(input_path),
            "rows": summary.row_count,
            "sha256": input_sha256,
        },
        "method": {
            "callable": ("drlf.analysis.part_b_shortlist.generate_part_b_shortlist"),
            "interpretation": (
                "Screening routes are review-priority signals, not findings of improper "
                "billing, medical necessity, intent, or fraud."
            ),
            "selection_scope": "latest input year",
            "source_file": Path(part_b_shortlist.__file__).name,
            "source_sha256_lf": _hash_source_file_lf(Path(part_b_shortlist.__file__)),
        },
        "outputs": {
            "shortlist": {
                "bytes": shortlist_size,
                "file": shortlist_path.name,
                "rows": selected_rows,
                "sha256": shortlist_sha256,
            }
        },
        "schema_version": 1,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    manifest_sha256 = _hash_file(manifest_path)
    return SyntheticDemoResult(
        input_sha256=input_sha256,
        shortlist_path=shortlist_path,
        shortlist_sha256=shortlist_sha256,
        manifest_path=manifest_path,
        manifest_sha256=manifest_sha256,
        selected_rows=selected_rows,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the offline synthetic Part B screening demo.",
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        help="New directory (or an existing directory without demo outputs)",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_DEMO_INPUT,
        help=f"Explicitly synthetic scan CSV (default: {DEFAULT_DEMO_INPUT})",
    )
    parser.add_argument(
        "--allow-unpinned-input",
        action="store_true",
        help=(
            "Run a non-default fixture after independently confirming it contains no real data; "
            "the audit manifest labels its synthetic status as operator-asserted"
        ),
    )
    arguments = parser.parse_args(argv)
    result = run_synthetic_part_b_demo(
        arguments.input,
        arguments.output_dir,
        allow_unpinned_input=arguments.allow_unpinned_input,
    )
    print(
        f"Wrote {result.selected_rows} shortlist rows "
        f"({result.shortlist_sha256}) to {result.shortlist_path}"
    )
    print(f"Wrote audit manifest ({result.manifest_sha256}) to {result.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
