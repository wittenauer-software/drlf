from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO

DEFAULT_MAX_INPUT_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_INPUT_ROWS = 100_000
DEFAULT_MAX_ANONYMOUS_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_ANONYMOUS_ROWS = 10_000
DEFAULT_MAX_IDENTITY_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_IDENTITY_ROWS = 10_000
DEFAULT_MAX_OUTPUT_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_OUTPUT_ROWS = 10_000

SCREEN_ALGORITHM = "dmepos-supplier-summary-materiality"
SCREEN_ALGORITHM_VERSION = "2"
SCREEN_LABEL = f"{SCREEN_ALGORITHM}-v{SCREEN_ALGORITHM_VERSION}"
SUPPORTED_SCREEN_LABELS = frozenset({"dmepos-supplier-summary-materiality-v1", SCREEN_LABEL})
SCREEN_CAVEAT = (
    "Deterministic research routing only. Reconstructed payments and Q75 benchmark exposures "
    "are not supplier income, improper payments, loss, damages, or recoverable amounts."
)
SELECTION_SCOPE = "2024 high-dollar-persistent and high-dollar-emerging candidates only"

RUN_SIGNATURE_COLUMNS = (
    "requested_data_years",
    "latest_year_parameter",
    "minimum_panel_beneficiaries",
    "minimum_peer_count",
    "minimum_annual_payment",
    "minimum_benchmark_exposure",
    "tail_percentile_threshold",
    "full_percentile_threshold",
    "minimum_robust_z",
    "minimum_ratio",
)

METRIC_PREFIXES = (
    "standardized_payment",
    "raw_payment",
    "claims",
    "services",
)

METRIC_COLUMNS = tuple(
    column
    for prefix in METRIC_PREFIXES
    for column in (
        f"{prefix}_per_beneficiary",
        f"{prefix}_peer_median",
        f"{prefix}_peer_q75",
        f"{prefix}_peer_iqr",
        f"{prefix}_peer_mad",
        f"{prefix}_empirical_percentile",
        f"{prefix}_median_ratio",
        f"{prefix}_median_absolute_delta",
        f"{prefix}_robust_z",
        f"{prefix}_scale_source",
    )
)

SCREEN_ALGORITHM_COLUMNS = (
    "screen_algorithm",
    "screen_algorithm_version",
)

SCAN_RESULT_COLUMNS = (
    "data_year",
    "supplier_npi",
    "entity_code",
    "supplier_specialty_description",
    "supplier_specialty_source",
    "dominant_broad_category",
    "beneficiary_volume_band",
    "dme_payment_share",
    "pos_payment_share",
    "drug_payment_share",
    "source_release_id",
    "total_hcpcs_codes",
    "total_beneficiaries",
    "total_claims",
    "total_services",
    "total_submitted_charge",
    "total_medicare_allowed_amount",
    "total_medicare_payment_amount",
    "total_medicare_standardized_payment_amount",
    "raw_payment_benchmark_exposure_above_q75",
    "standardized_payment_benchmark_exposure_above_q75",
    "peer_count",
    "peer_evaluation_status",
    *METRIC_COLUMNS,
    "raw_payment_full_outlier_flag",
    "standardized_payment_full_outlier_flag",
    "claims_full_outlier_flag",
    "services_full_outlier_flag",
    "standardized_payment_tail_flag",
    "utilization_tail_flag",
    "magnitude_gates_pass",
    "candidate_tail_year",
    "candidate_full_year",
    "comparable_years",
    "candidate_tail_years",
    "candidate_full_years",
    "latest_year_present",
    "high_priority_temporal_flag",
    "trajectory_route",
    *RUN_SIGNATURE_COLUMNS,
    "category_caveat",
    "monetary_caveat",
    "benchmark_caveat",
    "route_caveat",
)

SCAN_COLUMNS = (*SCREEN_ALGORITHM_COLUMNS, *SCAN_RESULT_COLUMNS)

REQUIRED_COLUMNS = set(SCAN_COLUMNS)
PROHIBITED_IDENTITY_COLUMNS = {
    "supplier_last_org_name",
    "supplier_first_name",
    "supplier_middle_initial",
    "supplier_credentials",
    "supplier_address_line1",
    "supplier_address_line2",
    "supplier_city",
    "supplier_state",
    "supplier_zip5",
}

IDENTITY_COLUMNS = {
    "data_year",
    "supplier_npi",
    "source_release_id",
    "supplier_last_org_name",
    "supplier_first_name",
    "supplier_city",
    "supplier_state",
}

_ANONYMOUS_PROVENANCE_COLUMNS = (
    "screen_label",
    "screen_caveat",
    "selection_scope",
    "scan_input_sha256",
    "scan_input_bytes",
    "scan_input_rows",
    "max_input_bytes",
    "max_input_rows",
    "max_output_bytes",
    "max_output_rows",
)

ANONYMOUS_OUTPUT_COLUMNS = (
    "record_type",
    "screen_label",
    "screen_caveat",
    "selection_priority",
    "selection_reason",
    "selection_scope",
    "scan_input_sha256",
    "scan_input_bytes",
    "scan_input_rows",
    "max_input_bytes",
    "max_input_rows",
    "max_output_bytes",
    "max_output_rows",
    *SCAN_RESULT_COLUMNS,
)

IDENTIFIED_OUTPUT_COLUMNS = (
    "record_type",
    "screen_label",
    "screen_caveat",
    "selection_priority",
    "selection_reason",
    "selection_scope",
    "anonymous_shortlist_sha256",
    "anonymous_shortlist_bytes",
    "anonymous_shortlist_rows",
    "identity_input_sha256",
    "identity_input_bytes",
    "identity_input_rows",
    "max_anonymous_bytes",
    "max_anonymous_rows",
    "max_identity_bytes",
    "max_identity_rows",
    "max_output_bytes",
    "max_output_rows",
    "identity_scope_requirement",
    "supplier_name",
    "supplier_city",
    "supplier_state",
    "scan_input_sha256",
    "scan_input_bytes",
    "scan_input_rows",
    "anonymous_generation_max_input_bytes",
    "anonymous_generation_max_input_rows",
    "anonymous_generation_max_output_bytes",
    "anonymous_generation_max_output_rows",
    *SCAN_RESULT_COLUMNS,
)

# Compatibility for callers that used OUTPUT_COLUMNS before the two-stage freeze was introduced.
OUTPUT_COLUMNS = IDENTIFIED_OUTPUT_COLUMNS

_NPI_PATTERN = re.compile(r"[0-9]{10}")
_ALLOWED_CATEGORIES = {"DME", "POS", "Drug", "mixed", "unclassified", "none"}
_ALLOWED_VOLUME_BANDS = {"100-249", "250-499", "500-999", "1000-4999", "5000+"}
_ALLOWED_PEER_STATUSES = {"scoreable", "insufficient-peer-descriptive"}
_ALLOWED_ROUTES = {
    "high-dollar-persistent",
    "high-dollar-emerging",
    "high-spend-context",
    "not-qualified",
}
_CANDIDATE_ROUTES = {"high-dollar-persistent", "high-dollar-emerging"}
_ALLOWED_SCALES = {"mad", "iqr-fallback", "degenerate"}


class _BoundedUtf8Writer:
    def __init__(self, output_file: BinaryIO, *, max_bytes: int) -> None:
        self.output_file = output_file
        self.max_bytes = max_bytes
        self.bytes_written = 0

    def write(self, value: str) -> int:
        encoded = value.encode("utf-8")
        next_size = self.bytes_written + len(encoded)
        if next_size > self.max_bytes:
            raise ValueError(
                f"DMEPOS shortlist output would exceed maximum of {self.max_bytes} bytes"
            )
        self.output_file.write(encoded)
        self.bytes_written = next_size
        return len(value)


@dataclass(frozen=True)
class _ScanRow:
    raw: dict[str, str]
    data_year: int
    supplier_npi: str
    entity_code: str
    supplier_specialty_description: str
    dominant_broad_category: str
    peer_scoreable: bool
    total_medicare_payment_amount: Decimal
    raw_benchmark_exposure: Decimal
    standardized_benchmark_exposure: Decimal
    candidate_tail_year: bool
    candidate_full_year: bool
    comparable_years: int
    candidate_tail_years: int
    candidate_full_years: int
    latest_year_present: bool
    high_priority_temporal: bool
    trajectory_route: str

    @property
    def trajectory_key(self) -> tuple[str, str, str, str]:
        return (
            self.supplier_npi,
            self.entity_code,
            self.supplier_specialty_description,
            self.dominant_broad_category,
        )

    @property
    def reported_temporal_state(self) -> tuple[int, int, int, bool, bool]:
        return (
            self.comparable_years,
            self.candidate_tail_years,
            self.candidate_full_years,
            self.latest_year_present,
            self.high_priority_temporal,
        )


@dataclass
class _TrajectoryAudit:
    scoreable_years: int = 0
    tail_years: int = 0
    full_years: int = 0
    maximum_year: int = 0
    reported_state: tuple[int, int, int, bool, bool] | None = None
    candidate_latest_row: _ScanRow | None = None


@dataclass(frozen=True)
class _Identity:
    supplier_name: str
    supplier_city: str
    supplier_state: str


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_positive_limits(*limits: tuple[int, str]) -> None:
    for value, label in limits:
        if value < 1:
            raise ValueError(f"DMEPOS {label} must be at least 1")


def _bounded_size(path: Path, *, maximum: int, label: str) -> int:
    size = path.stat().st_size
    if size > maximum:
        raise ValueError(f"DMEPOS {label} is {size} bytes; maximum is {maximum} bytes")
    return size


def _required_text(row: dict[str | None, str | list[str] | None], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"DMEPOS screen has blank required field: {field}")
    return value.strip()


def _required_integer(
    row: dict[str | None, str | list[str] | None], field: str, *, minimum: int = 0
) -> int:
    value = _required_text(row, field)
    try:
        number = int(value)
    except ValueError:
        raise ValueError(f"DMEPOS screen has invalid integer {field}={value!r}") from None
    if str(number) != value or number < minimum:
        raise ValueError(f"DMEPOS screen has invalid integer {field}={value!r}")
    return number


def _required_decimal(
    row: dict[str | None, str | list[str] | None],
    field: str,
    *,
    minimum: Decimal | None = None,
) -> Decimal:
    value = _required_text(row, field)
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"DMEPOS screen has invalid number {field}={value!r}") from None
    if not number.is_finite() or (minimum is not None and number < minimum):
        raise ValueError(f"DMEPOS screen has invalid number {field}={value!r}")
    return number


def _optional_decimal(row: dict[str | None, str | list[str] | None], field: str) -> Decimal | None:
    value = row.get(field)
    if not isinstance(value, str):
        raise ValueError(f"DMEPOS screen has invalid field {field}={value!r}")
    if not value.strip():
        return None
    return _required_decimal(row, field)


def _boolean(row: dict[str | None, str | list[str] | None], field: str) -> bool:
    value = _required_text(row, field).casefold()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"DMEPOS screen has invalid boolean {field}={value!r}")


def _validate_header(fieldnames: list[str] | None) -> None:
    fields = tuple(fieldnames or ())
    if fields != SCAN_COLUMNS:
        raise ValueError("DMEPOS screen has an unexpected column contract")


def _validate_screen_algorithm_contract(
    row: dict[str | None, str | list[str] | None], row_number: int
) -> None:
    if (
        row.get("screen_algorithm") != SCREEN_ALGORITHM
        or row.get("screen_algorithm_version") != SCREEN_ALGORITHM_VERSION
    ):
        raise ValueError(
            "DMEPOS screen algorithm/version contract is not the maintained v2 contract "
            f"at row {row_number}"
        )


def _validate_identity_header(fieldnames: list[str] | None) -> None:
    fields = fieldnames or []
    if len(fields) != len(set(fields)):
        raise ValueError("DMEPOS identity mapping has duplicate column names")
    missing = sorted(IDENTITY_COLUMNS - set(fields))
    unexpected = sorted(set(fields) - IDENTITY_COLUMNS)
    if missing:
        raise ValueError("DMEPOS identity mapping is missing columns: " + ", ".join(missing))
    if unexpected:
        raise ValueError("DMEPOS identity mapping has unexpected columns: " + ", ".join(unexpected))


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _validate_signature(row: dict[str | None, str | list[str] | None]) -> tuple[str, ...]:
    years = _required_text(row, "requested_data_years")
    latest_year = _required_integer(row, "latest_year_parameter", minimum=1)
    minimum_panel = _required_integer(row, "minimum_panel_beneficiaries", minimum=1)
    minimum_peers = _required_integer(row, "minimum_peer_count", minimum=1)
    numeric_values = tuple(
        _required_decimal(row, field, minimum=Decimal(0))
        for field in (
            "minimum_annual_payment",
            "minimum_benchmark_exposure",
            "tail_percentile_threshold",
            "full_percentile_threshold",
            "minimum_robust_z",
            "minimum_ratio",
        )
    )
    canonical = (
        years,
        str(latest_year),
        str(minimum_panel),
        str(minimum_peers),
        *(_decimal_text(value) for value in numeric_values),
    )
    expected = (
        "2022;2023;2024",
        "2024",
        "100",
        "100",
        "100000",
        "100000",
        "97.5",
        "99",
        "3.5",
        "2",
    )
    if canonical != expected:
        raise ValueError(
            "DMEPOS screen settings do not match the prospective 2022-2024 materiality contract"
        )
    return canonical


def _validate_metric(
    row: dict[str | None, str | list[str] | None], prefix: str
) -> tuple[Decimal, Decimal | None, Decimal | None]:
    for suffix in ("per_beneficiary", "peer_median", "peer_q75", "peer_iqr", "peer_mad"):
        _required_decimal(row, f"{prefix}_{suffix}", minimum=Decimal(0))
    percentile = _required_decimal(row, f"{prefix}_empirical_percentile", minimum=Decimal(0))
    if percentile > 100:
        raise ValueError(f"DMEPOS screen has out-of-range {prefix} percentile")
    ratio = _optional_decimal(row, f"{prefix}_median_ratio")
    if ratio is not None and ratio < 0:
        raise ValueError(f"DMEPOS screen has negative {prefix} ratio")
    _required_decimal(row, f"{prefix}_median_absolute_delta", minimum=Decimal(0))
    robust_z = _optional_decimal(row, f"{prefix}_robust_z")
    scale = _required_text(row, f"{prefix}_scale_source")
    if scale not in _ALLOWED_SCALES:
        raise ValueError(f"DMEPOS screen has invalid {prefix} scale source {scale!r}")
    if scale == "degenerate" and robust_z is not None:
        raise ValueError(f"DMEPOS screen has robust z for degenerate {prefix} scale")
    return percentile, ratio, robust_z


def _parse_scan_row(
    raw: dict[str | None, str | list[str] | None],
    row_number: int,
    *,
    require_algorithm_contract: bool = True,
) -> _ScanRow:
    if None in raw:
        raise ValueError(f"DMEPOS screen row {row_number} has more values than columns")
    if require_algorithm_contract:
        _validate_screen_algorithm_contract(raw, row_number)
    supplier_npi = _required_text(raw, "supplier_npi")
    if _NPI_PATTERN.fullmatch(supplier_npi) is None:
        raise ValueError(f"DMEPOS screen has invalid supplier_npi at row {row_number}")
    data_year = _required_integer(raw, "data_year", minimum=1)
    if data_year not in {2022, 2023, 2024}:
        raise ValueError(f"DMEPOS screen has out-of-contract year at row {row_number}")

    for field in (
        "entity_code",
        "supplier_specialty_description",
        "supplier_specialty_source",
        "beneficiary_volume_band",
        "peer_evaluation_status",
        "trajectory_route",
        "category_caveat",
        "monetary_caveat",
        "benchmark_caveat",
        "route_caveat",
    ):
        _required_text(raw, field)
    category = _required_text(raw, "dominant_broad_category")
    if category not in _ALLOWED_CATEGORIES:
        raise ValueError(f"DMEPOS screen has invalid broad category at row {row_number}")
    volume_band = _required_text(raw, "beneficiary_volume_band")
    if volume_band not in _ALLOWED_VOLUME_BANDS:
        raise ValueError(f"DMEPOS screen has invalid volume band at row {row_number}")
    for field in ("dme_payment_share", "pos_payment_share", "drug_payment_share"):
        share = _optional_decimal(raw, field)
        if share is not None and not 0 <= share <= 1:
            raise ValueError(f"DMEPOS screen has out-of-range {field} at row {row_number}")

    _required_integer(raw, "total_beneficiaries", minimum=100)
    _required_integer(raw, "source_release_id", minimum=1)
    _required_integer(raw, "total_hcpcs_codes", minimum=0)
    _required_integer(raw, "total_claims", minimum=0)
    _required_decimal(raw, "total_services", minimum=Decimal(0))
    for field in ("total_submitted_charge", "total_medicare_allowed_amount"):
        _required_decimal(raw, field, minimum=Decimal(0))
    total_payment = _required_decimal(raw, "total_medicare_payment_amount", minimum=Decimal(0))
    standardized_total = _required_decimal(
        raw,
        "total_medicare_standardized_payment_amount",
        minimum=Decimal(0),
    )
    raw_exposure = _required_decimal(
        raw, "raw_payment_benchmark_exposure_above_q75", minimum=Decimal(0)
    )
    standardized_exposure = _required_decimal(
        raw,
        "standardized_payment_benchmark_exposure_above_q75",
        minimum=Decimal(0),
    )
    if raw_exposure > total_payment:
        raise ValueError(f"DMEPOS raw benchmark exposure exceeds total payment at row {row_number}")
    if standardized_exposure > standardized_total:
        raise ValueError(
            "DMEPOS standardized benchmark exposure exceeds standardized payment "
            f"at row {row_number}"
        )

    metrics = {prefix: _validate_metric(raw, prefix) for prefix in METRIC_PREFIXES}
    peer_count = _required_integer(raw, "peer_count", minimum=1)
    peer_status = _required_text(raw, "peer_evaluation_status")
    if peer_status not in _ALLOWED_PEER_STATUSES:
        raise ValueError(f"DMEPOS screen has invalid peer status at row {row_number}")
    if (peer_count >= 100) != (peer_status == "scoreable"):
        raise ValueError(f"DMEPOS screen peer status disagrees with peer count at row {row_number}")

    raw_payment_full = _boolean(raw, "raw_payment_full_outlier_flag")
    standardized_payment_full = _boolean(raw, "standardized_payment_full_outlier_flag")
    claims_full = _boolean(raw, "claims_full_outlier_flag")
    services_full = _boolean(raw, "services_full_outlier_flag")
    standardized_payment_tail = _boolean(raw, "standardized_payment_tail_flag")
    utilization_tail = _boolean(raw, "utilization_tail_flag")

    def expected_full(prefix: str) -> bool:
        percentile, ratio, robust_z = metrics[prefix]
        return (
            peer_count >= 100
            and percentile >= Decimal("99")
            and ratio is not None
            and ratio >= Decimal(2)
            and robust_z is not None
            and robust_z >= Decimal("3.5")
        )

    for observed, prefix in (
        (raw_payment_full, "raw_payment"),
        (standardized_payment_full, "standardized_payment"),
        (claims_full, "claims"),
        (services_full, "services"),
    ):
        if observed != expected_full(prefix):
            raise ValueError(f"DMEPOS {prefix} full flag is inconsistent at row {row_number}")

    expected_standardized_tail = peer_count >= 100 and metrics["standardized_payment"][
        0
    ] >= Decimal("97.5")
    expected_utilization_tail = peer_count >= 100 and (
        metrics["claims"][0] >= Decimal("97.5") or metrics["services"][0] >= Decimal("97.5")
    )
    if standardized_payment_tail != expected_standardized_tail:
        raise ValueError(
            f"DMEPOS standardized payment tail flag is inconsistent at row {row_number}"
        )
    if utilization_tail != expected_utilization_tail:
        raise ValueError(f"DMEPOS utilization tail flag is inconsistent at row {row_number}")

    magnitude_gates = _boolean(raw, "magnitude_gates_pass")
    expected_magnitude = (
        total_payment >= Decimal("100000")
        and raw_exposure >= Decimal("100000")
        and standardized_exposure >= Decimal("100000")
    )
    if magnitude_gates != expected_magnitude:
        raise ValueError(f"DMEPOS magnitude gates are inconsistent at row {row_number}")
    candidate_tail_year = _boolean(raw, "candidate_tail_year")
    candidate_full_year = _boolean(raw, "candidate_full_year")
    if candidate_tail_year != (magnitude_gates and standardized_payment_tail and utilization_tail):
        raise ValueError(f"DMEPOS candidate tail year is inconsistent at row {row_number}")
    if candidate_full_year != (
        magnitude_gates and standardized_payment_full and (claims_full or services_full)
    ):
        raise ValueError(f"DMEPOS candidate full year is inconsistent at row {row_number}")

    comparable_years = _required_integer(raw, "comparable_years")
    candidate_tail_years = _required_integer(raw, "candidate_tail_years")
    candidate_full_years = _required_integer(raw, "candidate_full_years")
    if any(value > 3 for value in (comparable_years, candidate_tail_years, candidate_full_years)):
        raise ValueError(f"DMEPOS temporal count exceeds three years at row {row_number}")
    latest_year_present = _boolean(raw, "latest_year_present")
    high_priority = _boolean(raw, "high_priority_temporal_flag")
    expected_high_priority = (
        comparable_years == 3
        and candidate_tail_years >= 2
        and candidate_full_years >= 1
        and latest_year_present
    )
    if high_priority != expected_high_priority:
        raise ValueError(f"DMEPOS temporal flag is inconsistent at row {row_number}")
    route = _required_text(raw, "trajectory_route")
    if route not in _ALLOWED_ROUTES:
        raise ValueError(f"DMEPOS screen has invalid route at row {row_number}")
    if high_priority:
        expected_route = "high-dollar-persistent"
    elif data_year == 2024 and candidate_full_year:
        expected_route = "high-dollar-emerging"
    elif total_payment >= Decimal("100000"):
        expected_route = "high-spend-context"
    else:
        expected_route = "not-qualified"
    if route != expected_route:
        raise ValueError(f"DMEPOS trajectory route is inconsistent at row {row_number}")

    return _ScanRow(
        raw={str(key): str(value) for key, value in raw.items()},
        data_year=data_year,
        supplier_npi=supplier_npi,
        entity_code=_required_text(raw, "entity_code"),
        supplier_specialty_description=_required_text(raw, "supplier_specialty_description"),
        dominant_broad_category=category,
        peer_scoreable=peer_status == "scoreable",
        total_medicare_payment_amount=total_payment,
        raw_benchmark_exposure=raw_exposure,
        standardized_benchmark_exposure=standardized_exposure,
        candidate_tail_year=candidate_tail_year,
        candidate_full_year=candidate_full_year,
        comparable_years=comparable_years,
        candidate_tail_years=candidate_tail_years,
        candidate_full_years=candidate_full_years,
        latest_year_present=latest_year_present,
        high_priority_temporal=high_priority,
        trajectory_route=route,
    )


def _read_identity_mapping(
    path: Path,
    *,
    expected_candidates: dict[str, int],
    max_rows: int,
) -> tuple[dict[str, _Identity], int]:
    identities: dict[str, _Identity] = {}
    row_count = 0
    with path.open(encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        _validate_identity_header(reader.fieldnames)
        for row_number, raw in enumerate(reader, start=2):
            row_count += 1
            if row_count > max_rows:
                raise ValueError(f"DMEPOS identity mapping exceeds maximum of {max_rows} data rows")
            if None in raw:
                raise ValueError(
                    f"DMEPOS identity mapping row {row_number} has more values than columns"
                )
            if _required_integer(raw, "data_year", minimum=1) != 2024:
                raise ValueError("DMEPOS identity mapping must contain only latest-year 2024 rows")
            npi = _required_text(raw, "supplier_npi")
            if _NPI_PATTERN.fullmatch(npi) is None:
                raise ValueError(f"DMEPOS identity mapping has invalid NPI at row {row_number}")
            if npi in identities:
                raise ValueError(f"DMEPOS identity mapping has duplicate NPI {npi}")
            source_release_id = _required_integer(raw, "source_release_id", minimum=1)
            expected_release_id = expected_candidates.get(npi)
            if expected_release_id is not None and source_release_id != expected_release_id:
                raise ValueError(
                    "DMEPOS identity mapping source release does not match the frozen "
                    f"candidate for NPI {npi}"
                )
            last_or_org = _required_text(raw, "supplier_last_org_name")
            first_value = raw.get("supplier_first_name")
            if not isinstance(first_value, str):
                raise ValueError("DMEPOS identity mapping has invalid supplier_first_name")
            identities[npi] = _Identity(
                supplier_name=" ".join(part for part in (first_value.strip(), last_or_org) if part),
                supplier_city=_required_text(raw, "supplier_city"),
                supplier_state=_required_text(raw, "supplier_state"),
            )
    if set(identities) != set(expected_candidates):
        missing = sorted(set(expected_candidates) - set(identities))
        extra = sorted(set(identities) - set(expected_candidates))
        raise ValueError(
            "DMEPOS identity mapping must contain exactly the frozen candidate "
            "NPI/source-release pairs; "
            f"missing={missing}, extra={extra}"
        )
    return identities, row_count


def _write_csv_artifact(
    output_path: Path,
    *,
    columns: tuple[str, ...],
    rows: list[dict[str, str]],
    max_bytes: int,
) -> str:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    created_output = False
    try:
        with output_path.open("xb") as output_file:
            created_output = True
            writer = csv.DictWriter(
                _BoundedUtf8Writer(output_file, max_bytes=max_bytes),
                fieldnames=columns,
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)
    except Exception:
        if created_output:
            output_path.unlink(missing_ok=True)
        raise
    return _sha256_file(output_path)


def _anonymous_sort_key(row: _ScanRow) -> tuple[int, Decimal, Decimal, str]:
    return (
        0 if row.trajectory_route == "high-dollar-persistent" else 1,
        -row.standardized_benchmark_exposure,
        -row.raw_benchmark_exposure,
        row.supplier_npi,
    )


def generate_dmepos_anonymous_shortlist(
    input_path: Path,
    output_path: Path,
    *,
    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
    max_input_rows: int = DEFAULT_MAX_INPUT_ROWS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    max_output_rows: int = DEFAULT_MAX_OUTPUT_ROWS,
) -> tuple[int, int, int, str]:
    """Freeze deterministic 2024 candidate NPIs before any identity lookup.

    The full screen is validated row by row. Only persistent and emerging candidates retain their
    full latest-year row in memory; all other trajectories retain compact temporal audit fields.
    High-spend context rows remain in the full screen and are intentionally excluded here.
    """
    if output_path.exists():
        raise FileExistsError(f"Refusing to replace DMEPOS anonymous shortlist: {output_path}")
    _validate_positive_limits(
        (max_input_bytes, "maximum input bytes"),
        (max_input_rows, "maximum input rows"),
        (max_output_bytes, "maximum output bytes"),
        (max_output_rows, "maximum output rows"),
    )
    input_bytes = _bounded_size(input_path, maximum=max_input_bytes, label="screen input")
    input_hash = _sha256_file(input_path)

    seen_grain: set[tuple[int, str]] = set()
    observed_years: set[int] = set()
    source_release_by_year: dict[int, int] = {}
    trajectories: dict[tuple[str, str, str, str], _TrajectoryAudit] = defaultdict(_TrajectoryAudit)
    signature: tuple[str, ...] | None = None
    retained_candidate_rows = 0
    input_rows = 0
    with input_path.open(encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        _validate_header(reader.fieldnames)
        for row_number, raw in enumerate(reader, start=2):
            input_rows += 1
            if input_rows > max_input_rows:
                raise ValueError(
                    f"DMEPOS screen input exceeds maximum of {max_input_rows} data rows"
                )
            row_signature = _validate_signature(raw)
            if signature is None:
                signature = row_signature
            elif signature != row_signature:
                raise ValueError("DMEPOS screen contains mixed run settings")
            row = _parse_scan_row(raw, row_number)
            grain = (row.data_year, row.supplier_npi)
            if grain in seen_grain:
                raise ValueError(f"DMEPOS screen has duplicate year/NPI grain at row {row_number}")
            seen_grain.add(grain)
            observed_years.add(row.data_year)
            source_release_id = _required_integer(raw, "source_release_id", minimum=1)
            prior_release_id = source_release_by_year.setdefault(row.data_year, source_release_id)
            if prior_release_id != source_release_id:
                raise ValueError(
                    f"DMEPOS screen contains mixed source releases for {row.data_year}"
                )

            audit = trajectories[row.trajectory_key]
            audit.scoreable_years += row.peer_scoreable
            audit.tail_years += row.candidate_tail_year
            audit.full_years += row.candidate_full_year
            audit.maximum_year = max(audit.maximum_year, row.data_year)
            if audit.reported_state is None:
                audit.reported_state = row.reported_temporal_state
            elif audit.reported_state != row.reported_temporal_state:
                raise ValueError("DMEPOS trajectory contains mixed temporal summary fields")
            if row.data_year == 2024 and row.trajectory_route in _CANDIDATE_ROUTES:
                audit.candidate_latest_row = row
                retained_candidate_rows += 1
                if retained_candidate_rows > max_output_rows:
                    raise ValueError(
                        "DMEPOS anonymous shortlist exceeds maximum of "
                        f"{max_output_rows} candidate rows"
                    )

    if signature is None:
        raise ValueError("DMEPOS screen contains no data rows")
    if observed_years != {2022, 2023, 2024}:
        raise ValueError("DMEPOS screen must contain rows from all three contract years")
    if len(set(source_release_by_year.values())) != len(source_release_by_year):
        raise ValueError("DMEPOS screen reuses one source release across multiple data years")

    selected: list[_ScanRow] = []
    for audit in trajectories.values():
        latest_present = audit.maximum_year == 2024
        persistent = (
            audit.scoreable_years == 3
            and audit.tail_years >= 2
            and audit.full_years >= 1
            and latest_present
        )
        computed_state = (
            audit.scoreable_years,
            audit.tail_years,
            audit.full_years,
            latest_present,
            persistent,
        )
        if audit.reported_state != computed_state:
            raise ValueError("DMEPOS trajectory summary does not reproduce from annual rows")
        candidate = audit.candidate_latest_row
        if candidate is not None:
            expected_route = "high-dollar-persistent" if persistent else "high-dollar-emerging"
            if candidate.trajectory_route != expected_route:
                raise ValueError(
                    "DMEPOS latest candidate route does not reproduce from annual rows"
                )
            selected.append(candidate)

    if len(selected) > max_output_rows:
        raise ValueError(
            f"DMEPOS anonymous shortlist has {len(selected)} rows; "
            f"maximum is {max_output_rows} rows"
        )
    selected.sort(key=_anonymous_sort_key)
    common = {
        "screen_label": SCREEN_LABEL,
        "screen_caveat": SCREEN_CAVEAT,
        "selection_scope": SELECTION_SCOPE,
        "scan_input_sha256": input_hash,
        "scan_input_bytes": str(input_bytes),
        "scan_input_rows": str(input_rows),
        "max_input_bytes": str(max_input_bytes),
        "max_input_rows": str(max_input_rows),
        "max_output_bytes": str(max_output_bytes),
        "max_output_rows": str(max_output_rows),
    }
    rows_to_write = [
        {
            **common,
            "record_type": "candidate",
            "selection_priority": (
                "1" if row.trajectory_route == "high-dollar-persistent" else "2"
            ),
            "selection_reason": row.trajectory_route,
            **{column: row.raw[column] for column in SCAN_RESULT_COLUMNS},
        }
        for row in selected
    ]
    if not rows_to_write:
        rows_to_write = [
            {
                **common,
                "record_type": "run-metadata",
                "requested_data_years": "2022;2023;2024",
                "latest_year_parameter": "2024",
                "minimum_panel_beneficiaries": "100",
                "minimum_peer_count": "100",
                "minimum_annual_payment": "100000",
                "minimum_benchmark_exposure": "100000",
                "tail_percentile_threshold": "97.5",
                "full_percentile_threshold": "99",
                "minimum_robust_z": "3.5",
                "minimum_ratio": "2",
            }
        ]
    digest = _write_csv_artifact(
        output_path,
        columns=ANONYMOUS_OUTPUT_COLUMNS,
        rows=rows_to_write,
        max_bytes=max_output_bytes,
    )
    persistent_count = sum(row.trajectory_route == "high-dollar-persistent" for row in selected)
    emerging_count = len(selected) - persistent_count
    return len(selected), persistent_count, emerging_count, digest


def _read_anonymous_shortlist(path: Path, *, max_rows: int) -> tuple[list[dict[str, str]], int]:
    rows: list[dict[str, str]] = []
    metadata_rows = 0
    provenance: tuple[str, ...] | None = None
    with path.open(encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        if tuple(reader.fieldnames or ()) != ANONYMOUS_OUTPUT_COLUMNS:
            raise ValueError("DMEPOS anonymous shortlist has an unexpected column contract")
        for row_number, raw in enumerate(reader, start=2):
            if len(rows) + metadata_rows >= max_rows:
                raise ValueError(
                    f"DMEPOS anonymous shortlist exceeds maximum of {max_rows} data rows"
                )
            if None in raw:
                raise ValueError(
                    f"DMEPOS anonymous shortlist row {row_number} has more values than columns"
                )
            row = {str(key): str(value) for key, value in raw.items()}
            if (
                row["screen_label"] not in SUPPORTED_SCREEN_LABELS
                or row["screen_caveat"] != SCREEN_CAVEAT
            ):
                raise ValueError("DMEPOS anonymous shortlist has an invalid screen identity")
            if row["selection_scope"] != SELECTION_SCOPE:
                raise ValueError("DMEPOS anonymous shortlist has an invalid selection scope")
            if re.fullmatch(r"[0-9a-f]{64}", row["scan_input_sha256"]) is None:
                raise ValueError("DMEPOS anonymous shortlist has an invalid scan SHA-256")
            for field in (
                "scan_input_bytes",
                "scan_input_rows",
                "max_input_bytes",
                "max_input_rows",
                "max_output_bytes",
                "max_output_rows",
            ):
                _required_integer(row, field, minimum=1)
            row_provenance = tuple(row[field] for field in _ANONYMOUS_PROVENANCE_COLUMNS)
            if provenance is None:
                provenance = row_provenance
            elif provenance != row_provenance:
                raise ValueError("DMEPOS anonymous shortlist contains mixed provenance")

            record_type = row["record_type"]
            if record_type == "run-metadata":
                metadata_rows += 1
                if row["supplier_npi"] or row["selection_reason"] or row["selection_priority"]:
                    raise ValueError("DMEPOS anonymous metadata row contains a candidate identity")
                _validate_signature(row)
                continue
            if record_type != "candidate":
                raise ValueError("DMEPOS anonymous shortlist has an invalid record type")
            if metadata_rows:
                raise ValueError("DMEPOS anonymous shortlist mixes metadata and candidate rows")
            _validate_signature(row)
            npi = row["supplier_npi"]
            if _NPI_PATTERN.fullmatch(npi) is None:
                raise ValueError(f"DMEPOS anonymous shortlist has invalid NPI at row {row_number}")
            route = row["trajectory_route"]
            if route not in _CANDIDATE_ROUTES or row["selection_reason"] != route:
                raise ValueError("DMEPOS anonymous shortlist contains a noncandidate route")
            expected_priority = "1" if route == "high-dollar-persistent" else "2"
            if row["selection_priority"] != expected_priority or row["data_year"] != "2024":
                raise ValueError("DMEPOS anonymous shortlist has inconsistent candidate routing")
            # The SQL handshake was consumed before this artifact received its screen label.
            # Historical v1 artifacts intentionally retain the same downstream column contract.
            parsed = _parse_scan_row(row, row_number, require_algorithm_contract=False)
            if parsed.trajectory_route != route:
                raise ValueError("DMEPOS anonymous shortlist has inconsistent validated route")
            rows.append(row)

    total_rows = len(rows) + metadata_rows
    if total_rows == 0:
        raise ValueError("DMEPOS anonymous shortlist contains no records")
    if metadata_rows and (metadata_rows != 1 or rows):
        raise ValueError("DMEPOS empty anonymous shortlist must contain one metadata row")
    npis = [row["supplier_npi"] for row in rows]
    if len(npis) != len(set(npis)):
        raise ValueError("DMEPOS anonymous shortlist has duplicate candidate NPIs")

    expected_order = sorted(
        rows,
        key=lambda row: (
            0 if row["trajectory_route"] == "high-dollar-persistent" else 1,
            -_required_decimal(row, "standardized_payment_benchmark_exposure_above_q75"),
            -_required_decimal(row, "raw_payment_benchmark_exposure_above_q75"),
            row["supplier_npi"],
        ),
    )
    if rows != expected_order:
        raise ValueError("DMEPOS anonymous shortlist is not in deterministic order")
    return rows, total_rows


def read_dmepos_anonymous_candidate_scope(
    path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_ANONYMOUS_BYTES,
    max_rows: int = DEFAULT_MAX_ANONYMOUS_ROWS,
) -> tuple[list[tuple[str, int]], str, int, int]:
    """Validate a frozen artifact and return its exact candidate NPI/release scope."""
    _validate_positive_limits(
        (max_bytes, "maximum anonymous bytes"),
        (max_rows, "maximum anonymous rows"),
    )
    input_bytes = _bounded_size(path, maximum=max_bytes, label="anonymous shortlist input")
    candidates, input_rows = _read_anonymous_shortlist(path, max_rows=max_rows)
    candidate_scope = [
        (row["supplier_npi"], _required_integer(row, "source_release_id", minimum=1))
        for row in candidates
    ]
    if len({source_release_id for _, source_release_id in candidate_scope}) > 1:
        raise ValueError("DMEPOS frozen candidates must reference exactly one 2024 source release")
    return (
        candidate_scope,
        _sha256_file(path),
        input_bytes,
        input_rows,
    )


def read_dmepos_anonymous_candidate_npis(
    path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_ANONYMOUS_BYTES,
    max_rows: int = DEFAULT_MAX_ANONYMOUS_ROWS,
) -> tuple[list[str], str, int, int]:
    """Validate a frozen anonymous artifact and return its exact candidate NPI scope."""
    scope, artifact_hash, input_bytes, input_rows = read_dmepos_anonymous_candidate_scope(
        path,
        max_bytes=max_bytes,
        max_rows=max_rows,
    )
    return [npi for npi, _ in scope], artifact_hash, input_bytes, input_rows


def join_dmepos_shortlist_identities(
    anonymous_path: Path,
    identity_path: Path,
    output_path: Path,
    *,
    max_anonymous_bytes: int = DEFAULT_MAX_ANONYMOUS_BYTES,
    max_anonymous_rows: int = DEFAULT_MAX_ANONYMOUS_ROWS,
    max_identity_bytes: int = DEFAULT_MAX_IDENTITY_BYTES,
    max_identity_rows: int = DEFAULT_MAX_IDENTITY_ROWS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    max_output_rows: int = DEFAULT_MAX_OUTPUT_ROWS,
) -> tuple[int, str]:
    """Join identities only to a previously frozen anonymous candidate artifact."""
    if output_path.exists():
        raise FileExistsError(f"Refusing to replace DMEPOS identified shortlist: {output_path}")
    _validate_positive_limits(
        (max_anonymous_bytes, "maximum anonymous bytes"),
        (max_anonymous_rows, "maximum anonymous rows"),
        (max_identity_bytes, "maximum identity bytes"),
        (max_identity_rows, "maximum identity rows"),
        (max_output_bytes, "maximum output bytes"),
        (max_output_rows, "maximum output rows"),
    )
    anonymous_bytes = _bounded_size(
        anonymous_path,
        maximum=max_anonymous_bytes,
        label="anonymous shortlist input",
    )
    identity_bytes = _bounded_size(
        identity_path, maximum=max_identity_bytes, label="identity input"
    )
    anonymous_hash = _sha256_file(anonymous_path)
    identity_hash = _sha256_file(identity_path)
    candidates, anonymous_rows = _read_anonymous_shortlist(
        anonymous_path, max_rows=max_anonymous_rows
    )
    if len(candidates) > max_output_rows:
        raise ValueError(
            f"DMEPOS identified shortlist has {len(candidates)} rows; "
            f"maximum is {max_output_rows} rows"
        )
    identities, identity_rows = _read_identity_mapping(
        identity_path,
        expected_candidates={
            row["supplier_npi"]: int(row["source_release_id"]) for row in candidates
        },
        max_rows=max_identity_rows,
    )

    join_common = {
        "anonymous_shortlist_sha256": anonymous_hash,
        "anonymous_shortlist_bytes": str(anonymous_bytes),
        "anonymous_shortlist_rows": str(anonymous_rows),
        "identity_input_sha256": identity_hash,
        "identity_input_bytes": str(identity_bytes),
        "identity_input_rows": str(identity_rows),
        "max_anonymous_bytes": str(max_anonymous_bytes),
        "max_anonymous_rows": str(max_anonymous_rows),
        "max_identity_bytes": str(max_identity_bytes),
        "max_identity_rows": str(max_identity_rows),
        "max_output_bytes": str(max_output_bytes),
        "max_output_rows": str(max_output_rows),
        "identity_scope_requirement": "exactly frozen 2024 candidate NPI/source-release pairs",
    }
    output_rows: list[dict[str, str]] = []
    for row in candidates:
        identity = identities[row["supplier_npi"]]
        output_rows.append(
            {
                **join_common,
                "record_type": "candidate",
                "screen_label": row["screen_label"],
                "screen_caveat": row["screen_caveat"],
                "selection_priority": row["selection_priority"],
                "selection_reason": row["selection_reason"],
                "selection_scope": row["selection_scope"],
                "supplier_name": identity.supplier_name,
                "supplier_city": identity.supplier_city,
                "supplier_state": identity.supplier_state,
                "scan_input_sha256": row["scan_input_sha256"],
                "scan_input_bytes": row["scan_input_bytes"],
                "scan_input_rows": row["scan_input_rows"],
                "anonymous_generation_max_input_bytes": row["max_input_bytes"],
                "anonymous_generation_max_input_rows": row["max_input_rows"],
                "anonymous_generation_max_output_bytes": row["max_output_bytes"],
                "anonymous_generation_max_output_rows": row["max_output_rows"],
                **{column: row[column] for column in SCAN_RESULT_COLUMNS},
            }
        )
    if not output_rows:
        with anonymous_path.open(encoding="utf-8", newline="") as input_file:
            metadata = next(csv.DictReader(input_file))
        output_rows = [
            {
                **join_common,
                "record_type": "run-metadata",
                "screen_label": metadata["screen_label"],
                "screen_caveat": metadata["screen_caveat"],
                "selection_scope": metadata["selection_scope"],
                "scan_input_sha256": metadata["scan_input_sha256"],
                "scan_input_bytes": metadata["scan_input_bytes"],
                "scan_input_rows": metadata["scan_input_rows"],
                "anonymous_generation_max_input_bytes": metadata["max_input_bytes"],
                "anonymous_generation_max_input_rows": metadata["max_input_rows"],
                "anonymous_generation_max_output_bytes": metadata["max_output_bytes"],
                "anonymous_generation_max_output_rows": metadata["max_output_rows"],
                **{column: metadata[column] for column in SCAN_RESULT_COLUMNS},
            }
        ]
    digest = _write_csv_artifact(
        output_path,
        columns=IDENTIFIED_OUTPUT_COLUMNS,
        rows=output_rows,
        max_bytes=max_output_bytes,
    )
    return len(candidates), digest


def generate_dmepos_shortlist(
    input_path: Path,
    identity_path: Path,
    output_path: Path,
    *,
    anonymous_output_path: Path | None = None,
    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
    max_input_rows: int = DEFAULT_MAX_INPUT_ROWS,
    max_identity_bytes: int = DEFAULT_MAX_IDENTITY_BYTES,
    max_identity_rows: int = DEFAULT_MAX_IDENTITY_ROWS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    max_output_rows: int = DEFAULT_MAX_OUTPUT_ROWS,
) -> tuple[int, int, int, str]:
    """Compatibility wrapper that persists the required anonymous artifact before identity join."""
    anonymous_path = anonymous_output_path or output_path.with_name(
        f"{output_path.stem}.anonymous{output_path.suffix or '.csv'}"
    )
    result = generate_dmepos_anonymous_shortlist(
        input_path,
        anonymous_path,
        max_input_bytes=max_input_bytes,
        max_input_rows=max_input_rows,
        max_output_bytes=max_output_bytes,
        max_output_rows=max_output_rows,
    )
    _, digest = join_dmepos_shortlist_identities(
        anonymous_path,
        identity_path,
        output_path,
        max_identity_bytes=max_identity_bytes,
        max_identity_rows=max_identity_rows,
        max_output_bytes=max_output_bytes,
        max_output_rows=max_output_rows,
    )
    return result[0], result[1], result[2], digest
