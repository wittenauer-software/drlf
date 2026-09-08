from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO

SCREEN_LABEL = "exploratory-multicode-reach"
SCREEN_CAVEAT = (
    "Exploratory multicode reach screen; not a fraud score or replacement for the maintained "
    "Part B shortlist."
)
DEFAULT_MAX_INPUT_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_INPUT_ROWS = 25_000
DEFAULT_MAX_OUTPUT_BYTES = 32 * 1024 * 1024

REQUIRED_COLUMNS = {
    "data_year",
    "rendering_npi",
    "provider_type",
    "entity_code",
    "place_of_service",
    "peer_evaluation_status",
    "provider_last_org_name",
    "provider_first_name",
    "provider_city",
    "provider_state",
    "hcpcs_code",
    "hcpcs_description",
    "provider_total_beneficiaries",
    "tested_beneficiaries",
    "peer_count",
    "panel_penetration",
    "days_per_tested_beneficiary",
    "reach_empirical_percentile",
    "reconstructed_medicare_payment_amount",
}

OUTPUT_COLUMNS = (
    "record_type",
    "screen_label",
    "screen_caveat",
    "reach_percentile_threshold",
    "minimum_tail_years",
    "minimum_qualifying_codes",
    "eligibility_requirement",
    "input_sha256",
    "input_bytes",
    "input_rows",
    "max_input_bytes",
    "max_input_rows",
    "max_output_bytes",
    "latest_data_year",
    "rendering_npi",
    "provider_type",
    "entity_code",
    "place_of_service",
    "provider_name",
    "latest_provider_city",
    "latest_provider_state",
    "qualifying_code_count",
    "hcpcs_code",
    "hcpcs_description",
    "observed_years",
    "reach_tail_years",
    "reach_tail_year_count",
    "minimum_observed_reach_empirical_percentile",
    "minimum_tail_reach_empirical_percentile",
    "latest_reach_empirical_percentile",
    "minimum_tail_peer_count",
    "latest_peer_count",
    "latest_provider_total_beneficiaries",
    "latest_tested_beneficiaries",
    "latest_panel_penetration",
    "latest_days_per_tested_beneficiary",
    "code_reconstructed_medicare_payment_amount_observed_years",
)

_NPI_PATTERN = re.compile(r"\d{10}")
_HCPCS_PATTERN = re.compile(r"[A-Z0-9]{5}")


class _BoundedUtf8Writer:
    def __init__(self, output_file: BinaryIO, *, max_bytes: int) -> None:
        self.output_file = output_file
        self.max_bytes = max_bytes
        self.bytes_written = 0

    def write(self, text: str) -> int:
        encoded = text.encode("utf-8")
        next_size = self.bytes_written + len(encoded)
        if next_size > self.max_bytes:
            raise ValueError(
                f"Exploratory multi-code output would exceed maximum of {self.max_bytes} bytes"
            )
        self.output_file.write(encoded)
        self.bytes_written = next_size
        return len(text)


@dataclass(frozen=True)
class _ScanRow:
    data_year: int
    rendering_npi: str
    provider_type: str
    entity_code: str
    place_of_service: str
    peer_evaluation_status: str
    provider_last_org_name: str
    provider_first_name: str
    provider_city: str
    provider_state: str
    hcpcs_code: str
    hcpcs_description: str
    provider_total_beneficiaries: int
    tested_beneficiaries: int
    peer_count: int | None
    panel_penetration: Decimal
    days_per_tested_beneficiary: Decimal
    reach_empirical_percentile: Decimal | None
    reconstructed_medicare_payment_amount: Decimal

    @property
    def group_key(self) -> tuple[str, str, str, str]:
        return (
            self.rendering_npi,
            self.provider_type,
            self.entity_code,
            self.place_of_service,
        )


@dataclass(frozen=True)
class _QualifyingCode:
    latest: _ScanRow
    observed_years: tuple[int, ...]
    tail_years: tuple[int, ...]
    minimum_observed_percentile: Decimal
    minimum_tail_percentile: Decimal
    observed_payment: Decimal


def _required_text(row: dict[str | None, str | list[str] | None], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Part B scan output has blank required field: {field}")
    return value.strip()


def _required_integer(
    row: dict[str | None, str | list[str] | None], field: str, *, minimum: int
) -> int:
    value = _required_text(row, field)
    try:
        number = int(value)
    except ValueError:
        raise ValueError(f"Part B scan output has invalid integer {field}={value!r}") from None
    if str(number) != value or number < minimum:
        raise ValueError(f"Part B scan output has invalid integer {field}={value!r}")
    return number


def _optional_integer(
    row: dict[str | None, str | list[str] | None], field: str, *, minimum: int
) -> int | None:
    value = row.get(field)
    if not isinstance(value, str):
        raise ValueError(f"Part B scan output has invalid field {field}={value!r}")
    normalized = value.strip()
    if not normalized:
        return None
    try:
        number = int(normalized)
    except ValueError:
        raise ValueError(f"Part B scan output has invalid integer {field}={value!r}") from None
    if str(number) != normalized or number < minimum:
        raise ValueError(f"Part B scan output has invalid integer {field}={value!r}")
    return number


def _required_decimal(row: dict[str | None, str | list[str] | None], field: str) -> Decimal:
    value = _required_text(row, field)
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"Part B scan output has invalid number {field}={value!r}") from None
    if not number.is_finite():
        raise ValueError(f"Part B scan output has non-finite number {field}={value!r}")
    return number


def _optional_decimal(row: dict[str | None, str | list[str] | None], field: str) -> Decimal | None:
    value = row.get(field)
    if not isinstance(value, str):
        raise ValueError(f"Part B scan output has invalid field {field}={value!r}")
    normalized = value.strip()
    if not normalized:
        return None
    try:
        number = Decimal(normalized)
    except InvalidOperation:
        raise ValueError(f"Part B scan output has invalid number {field}={value!r}") from None
    if not number.is_finite():
        raise ValueError(f"Part B scan output has non-finite number {field}={value!r}")
    return number


def _validate_header(fieldnames: list[str] | None) -> None:
    fields = fieldnames or []
    if len(fields) != len(set(fields)):
        raise ValueError("Part B scan output has duplicate column names")
    missing = sorted(REQUIRED_COLUMNS - set(fields))
    if missing:
        raise ValueError(f"Part B scan output is missing columns: {', '.join(missing)}")


def _parse_row(raw: dict[str | None, str | list[str] | None], row_number: int) -> _ScanRow:
    if None in raw:
        raise ValueError(f"Part B scan output row {row_number} has more values than columns")

    rendering_npi = _required_text(raw, "rendering_npi")
    if _NPI_PATTERN.fullmatch(rendering_npi) is None:
        raise ValueError(
            f"Part B scan output has invalid rendering_npi={rendering_npi!r} at row {row_number}"
        )

    provider_total = _required_integer(raw, "provider_total_beneficiaries", minimum=0)
    tested = _required_integer(raw, "tested_beneficiaries", minimum=0)
    if tested > provider_total:
        raise ValueError(
            "Part B scan output has tested_beneficiaries greater than "
            f"provider_total_beneficiaries at row {row_number}"
        )

    penetration = _required_decimal(raw, "panel_penetration")
    if penetration < 0 or penetration > 1:
        raise ValueError(
            f"Part B scan output has out-of-range panel_penetration at row {row_number}"
        )
    days = _required_decimal(raw, "days_per_tested_beneficiary")
    if days < 0:
        raise ValueError(
            f"Part B scan output has negative days_per_tested_beneficiary at row {row_number}"
        )
    reach_percentile = _optional_decimal(raw, "reach_empirical_percentile")
    if reach_percentile is not None and not 0 <= reach_percentile <= 100:
        raise ValueError(
            f"Part B scan output has out-of-range reach_empirical_percentile at row {row_number}"
        )
    peer_status = _required_text(raw, "peer_evaluation_status")
    peer_count = _optional_integer(raw, "peer_count", minimum=0)
    if peer_status == "scoreable" and peer_count is None:
        raise ValueError(f"Part B scan output has blank peer_count for scoreable row {row_number}")

    hcpcs_code = _required_text(raw, "hcpcs_code").upper()
    if _HCPCS_PATTERN.fullmatch(hcpcs_code) is None:
        raise ValueError(
            f"Part B scan output has invalid hcpcs_code={hcpcs_code!r} at row {row_number}"
        )

    return _ScanRow(
        data_year=_required_integer(raw, "data_year", minimum=1),
        rendering_npi=rendering_npi,
        provider_type=_required_text(raw, "provider_type"),
        entity_code=_required_text(raw, "entity_code"),
        place_of_service=_required_text(raw, "place_of_service"),
        peer_evaluation_status=peer_status,
        provider_last_org_name=_required_text(raw, "provider_last_org_name"),
        provider_first_name=(raw.get("provider_first_name") or "").strip(),
        provider_city=_required_text(raw, "provider_city"),
        provider_state=_required_text(raw, "provider_state"),
        hcpcs_code=hcpcs_code,
        hcpcs_description=_required_text(raw, "hcpcs_description"),
        provider_total_beneficiaries=provider_total,
        tested_beneficiaries=tested,
        peer_count=peer_count,
        panel_penetration=penetration,
        days_per_tested_beneficiary=days,
        reach_empirical_percentile=reach_percentile,
        reconstructed_medicare_payment_amount=_required_decimal(
            raw, "reconstructed_medicare_payment_amount"
        ),
    )


def _validated_threshold(value: Decimal | str) -> Decimal:
    try:
        threshold = value if isinstance(value, Decimal) else Decimal(value.strip())
    except (AttributeError, InvalidOperation):
        raise ValueError(f"Reach percentile threshold is not a number: {value!r}") from None
    if not threshold.is_finite() or not 0 <= threshold <= 100:
        raise ValueError("Reach percentile threshold must be between 0 and 100")
    return threshold


def _decimal_text(value: Decimal) -> str:
    if value.is_zero():
        return "0"
    exponent = value.as_tuple().exponent
    fixed_places = max(0, -exponent)
    integer_places = max(1, value.adjusted() + 1)
    if fixed_places + integer_places > 64:
        return str(value).replace("E+", "E")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _provider_name(row: _ScanRow) -> str:
    return " ".join(part for part in (row.provider_first_name, row.provider_last_org_name) if part)


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate_part_b_multicode_reach_screen(
    input_path: Path,
    output_path: Path,
    *,
    reach_percentile_threshold: Decimal | str,
    minimum_tail_years: int,
    minimum_qualifying_codes: int,
    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
    max_input_rows: int = DEFAULT_MAX_INPUT_ROWS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> tuple[int, int, int, str]:
    """Write an exploratory persistent multi-code reach screen from a complete scan CSV.

    Blank reach percentiles are valid for unscored source rows and cannot qualify. The maintained
    Part B shortlist is neither read nor modified by this separate exploratory route.
    """
    if output_path.exists():
        raise FileExistsError(f"Refusing to replace exploratory screen: {output_path}")
    threshold = _validated_threshold(reach_percentile_threshold)
    if minimum_tail_years < 1:
        raise ValueError("Minimum tail years must be at least 1")
    if minimum_qualifying_codes < 1:
        raise ValueError("Minimum qualifying codes must be at least 1")
    if max_input_bytes < 1:
        raise ValueError("Maximum input bytes must be at least 1")
    if max_input_rows < 1:
        raise ValueError("Maximum input rows must be at least 1")
    if max_output_bytes < 1:
        raise ValueError("Maximum output bytes must be at least 1")
    input_bytes = input_path.stat().st_size
    if input_bytes > max_input_bytes:
        raise ValueError(
            f"Part B scan input is {input_bytes} bytes; maximum is {max_input_bytes} bytes"
        )
    input_sha256 = _sha256_file(input_path)

    rows_by_code: dict[tuple[str, str, str, str, str], list[_ScanRow]] = defaultdict(list)
    seen_grains: set[tuple[int, str, str, str]] = set()
    latest_year: int | None = None
    input_rows = 0
    with input_path.open(encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        _validate_header(reader.fieldnames)
        for row_number, raw in enumerate(reader, start=2):
            input_rows += 1
            if input_rows > max_input_rows:
                raise ValueError(f"Part B scan input exceeds maximum of {max_input_rows} data rows")
            row = _parse_row(raw, row_number)
            grain = (
                row.data_year,
                row.rendering_npi,
                row.hcpcs_code,
                row.place_of_service,
            )
            if grain in seen_grains:
                raise ValueError(
                    f"Part B scan output has duplicate year/NPI/HCPCS/POS grain at row {row_number}"
                )
            seen_grains.add(grain)
            latest_year = row.data_year if latest_year is None else max(latest_year, row.data_year)
            rows_by_code[(*row.group_key, row.hcpcs_code)].append(row)
    if latest_year is None:
        raise ValueError("Part B scan output contains no data rows")

    qualifying_by_group: dict[tuple[str, str, str, str], list[_QualifyingCode]] = defaultdict(list)
    for rows in rows_by_code.values():
        rows.sort(key=lambda row: row.data_year)
        tail_rows = [
            row
            for row in rows
            if row.peer_evaluation_status == "scoreable"
            and row.reach_empirical_percentile is not None
            and row.reach_empirical_percentile >= threshold
        ]
        tail_years = tuple(row.data_year for row in tail_rows)
        if len(tail_years) < minimum_tail_years or latest_year not in tail_years:
            continue
        latest = next(row for row in rows if row.data_year == latest_year)
        observed_percentiles = [
            row.reach_empirical_percentile
            for row in rows
            if row.reach_empirical_percentile is not None
        ]
        tail_percentiles = [row.reach_empirical_percentile for row in tail_rows]
        qualifying_by_group[latest.group_key].append(
            _QualifyingCode(
                latest=latest,
                observed_years=tuple(row.data_year for row in rows),
                tail_years=tail_years,
                minimum_observed_percentile=min(observed_percentiles),
                minimum_tail_percentile=min(tail_percentiles),
                observed_payment=sum(
                    (row.reconstructed_medicare_payment_amount for row in rows),
                    start=Decimal(0),
                ),
            )
        )

    output_rows: list[dict[str, str]] = []
    qualified_group_count = 0
    qualified_npis: set[str] = set()
    for codes in qualifying_by_group.values():
        if len(codes) < minimum_qualifying_codes:
            continue
        qualified_group_count += 1
        qualified_npis.add(codes[0].latest.rendering_npi)
        sorted_codes = sorted(codes, key=lambda code: code.latest.hcpcs_code)
        for code in sorted_codes:
            latest = code.latest
            output_rows.append(
                {
                    "record_type": "candidate-code",
                    "screen_label": SCREEN_LABEL,
                    "screen_caveat": SCREEN_CAVEAT,
                    "reach_percentile_threshold": _decimal_text(threshold),
                    "minimum_tail_years": str(minimum_tail_years),
                    "minimum_qualifying_codes": str(minimum_qualifying_codes),
                    "eligibility_requirement": "peer_evaluation_status=scoreable",
                    "input_sha256": input_sha256,
                    "input_bytes": str(input_bytes),
                    "input_rows": str(input_rows),
                    "max_input_bytes": str(max_input_bytes),
                    "max_input_rows": str(max_input_rows),
                    "max_output_bytes": str(max_output_bytes),
                    "latest_data_year": str(latest_year),
                    "rendering_npi": latest.rendering_npi,
                    "provider_type": latest.provider_type,
                    "entity_code": latest.entity_code,
                    "place_of_service": latest.place_of_service,
                    "provider_name": _provider_name(latest),
                    "latest_provider_city": latest.provider_city,
                    "latest_provider_state": latest.provider_state,
                    "qualifying_code_count": str(len(sorted_codes)),
                    "hcpcs_code": latest.hcpcs_code,
                    "hcpcs_description": latest.hcpcs_description,
                    "observed_years": ";".join(map(str, code.observed_years)),
                    "reach_tail_years": ";".join(map(str, code.tail_years)),
                    "reach_tail_year_count": str(len(code.tail_years)),
                    "minimum_observed_reach_empirical_percentile": _decimal_text(
                        code.minimum_observed_percentile
                    ),
                    "minimum_tail_reach_empirical_percentile": _decimal_text(
                        code.minimum_tail_percentile
                    ),
                    "latest_reach_empirical_percentile": _decimal_text(
                        latest.reach_empirical_percentile
                    ),
                    "minimum_tail_peer_count": str(
                        min(
                            row.peer_count
                            for row in rows_by_code[(*latest.group_key, latest.hcpcs_code)]
                            if row.peer_evaluation_status == "scoreable"
                            and row.reach_empirical_percentile is not None
                            and row.reach_empirical_percentile >= threshold
                        )
                    ),
                    "latest_peer_count": str(latest.peer_count),
                    "latest_provider_total_beneficiaries": str(latest.provider_total_beneficiaries),
                    "latest_tested_beneficiaries": str(latest.tested_beneficiaries),
                    "latest_panel_penetration": _decimal_text(latest.panel_penetration),
                    "latest_days_per_tested_beneficiary": _decimal_text(
                        latest.days_per_tested_beneficiary
                    ),
                    "code_reconstructed_medicare_payment_amount_observed_years": _decimal_text(
                        code.observed_payment
                    ),
                }
            )

    output_rows.sort(
        key=lambda row: (
            -int(row["qualifying_code_count"]),
            row["rendering_npi"],
            row["provider_type"],
            row["entity_code"],
            row["place_of_service"],
            row["hcpcs_code"],
        )
    )

    rows_to_write = output_rows or [
        {
            "record_type": "run-metadata",
            "screen_label": SCREEN_LABEL,
            "screen_caveat": SCREEN_CAVEAT,
            "reach_percentile_threshold": _decimal_text(threshold),
            "minimum_tail_years": str(minimum_tail_years),
            "minimum_qualifying_codes": str(minimum_qualifying_codes),
            "eligibility_requirement": "peer_evaluation_status=scoreable",
            "input_sha256": input_sha256,
            "input_bytes": str(input_bytes),
            "input_rows": str(input_rows),
            "max_input_bytes": str(max_input_bytes),
            "max_input_rows": str(max_input_rows),
            "max_output_bytes": str(max_output_bytes),
            "latest_data_year": str(latest_year),
        }
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    created_output = False
    try:
        with output_path.open("xb") as output_file:
            created_output = True
            bounded_writer = _BoundedUtf8Writer(output_file, max_bytes=max_output_bytes)
            writer = csv.DictWriter(
                bounded_writer,
                fieldnames=OUTPUT_COLUMNS,
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows_to_write)
    except Exception:
        if created_output:
            output_path.unlink(missing_ok=True)
        raise

    return (
        len(output_rows),
        qualified_group_count,
        len(qualified_npis),
        _sha256_file(output_path),
    )
