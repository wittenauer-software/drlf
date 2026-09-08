from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO

DISCOVERY_ALGORITHM = "dmepos-candidate-service-code-discovery"
DISCOVERY_ALGORITHM_VERSION = "1"
FREEZE_ALGORITHM = "dmepos-service-code-freeze"
FREEZE_ALGORITHM_VERSION = "1"
MATERIALITY_FREEZE_ALGORITHM = "dmepos-service-code-materiality-freeze"
MATERIALITY_FREEZE_ALGORITHM_VERSION = "1"
MATERIALITY_SELECTION_MODE = "candidate-observed-payment-materiality-complete"

DEFAULT_TOP_CODES_PER_CANDIDATE = 3
DEFAULT_MIN_COHORT_CODE_PAYMENT = Decimal("100000")
DEFAULT_TARGET_CANDIDATE_VISIBLE_PAYMENT_SHARE = Decimal("0.90")
DEFAULT_MIN_VISIBLE_SUMMARY_PAYMENT_COVERAGE = Decimal("0.80")
DEFAULT_MAX_SELECTED_CODES = 25
DEFAULT_OBSERVED_PAYMENT_THRESHOLD = Decimal("100000")
DEFAULT_MAX_MATERIALITY_CODES = 100
DEFAULT_MAX_INPUT_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_INPUT_ROWS = 10_000
DEFAULT_MAX_OUTPUT_BYTES = 1024 * 1024

MONETARY_CAVEAT = (
    "Visible public DMEPOS aggregates only. Reconstructed amounts and portfolio differences "
    "are descriptive; they are not supplier income, improper payment, loss, damages, or recovery."
)

DISCOVERY_COLUMNS = (
    "code_discovery_algorithm",
    "code_discovery_algorithm_version",
    "focus_year",
    "candidate_npis_parameter",
    "source_release_ids_parameter",
    "top_codes_per_candidate",
    "min_cohort_code_payment",
    "target_candidate_visible_payment_share",
    "min_visible_summary_payment_coverage",
    "maximum_selected_codes",
    "supplier_summary_source_release_id",
    "supplier_service_source_release_id",
    "supplier_npi",
    "entity_code",
    "hcpcs_code",
    "hcpcs_description",
    "rental_cell_count",
    "rental_indicators",
    "nonunique_cell_claim_count_sum",
    "total_services",
    "reconstructed_submitted_charge",
    "reconstructed_medicare_allowed_amount",
    "reconstructed_medicare_payment_amount",
    "reconstructed_medicare_standardized_payment_amount",
    "summary_total_submitted_charge",
    "summary_total_medicare_allowed_amount",
    "summary_total_medicare_payment_amount",
    "summary_total_medicare_standardized_payment_amount",
    "visible_detail_reconstructed_submitted_charge",
    "visible_detail_reconstructed_medicare_allowed_amount",
    "visible_detail_reconstructed_medicare_payment_amount",
    "visible_detail_reconstructed_standardized_payment_amount",
    "visible_summary_submitted_charge_coverage",
    "visible_summary_allowed_amount_coverage",
    "visible_summary_payment_coverage",
    "visible_summary_standardized_payment_coverage",
    "candidate_payment_rank",
    "candidate_prior_cumulative_visible_payment",
    "candidate_after_cumulative_visible_payment",
    "candidate_prior_cumulative_visible_payment_share",
    "candidate_after_cumulative_visible_payment_share",
    "cohort_reconstructed_medicare_payment_amount",
    "cohort_reconstructed_medicare_standardized_payment_amount",
    "selected_by_candidate_top_n",
    "selected_by_cohort_payment",
    "selected_by_candidate_coverage",
    "selected_for_code_freeze",
    "selection_reason",
    "monetary_caveat",
)

FROZEN_COLUMNS = (
    "record_type",
    "freeze_algorithm",
    "freeze_algorithm_version",
    "code_discovery_algorithm",
    "code_discovery_algorithm_version",
    "focus_year",
    "candidate_npis_parameter",
    "source_release_ids_parameter",
    "top_codes_per_candidate",
    "min_cohort_code_payment",
    "target_candidate_visible_payment_share",
    "min_visible_summary_payment_coverage",
    "maximum_selected_codes",
    "discovery_input_sha256",
    "discovery_input_bytes",
    "discovery_input_rows",
    "max_input_bytes",
    "max_input_rows",
    "max_output_bytes",
    "selected_code_count",
    "hcpcs_code",
    "hcpcs_descriptions",
    "selected_by_candidate_top_n",
    "top_n_candidate_npis",
    "selected_by_cohort_payment",
    "cohort_reconstructed_medicare_payment_amount",
    "cohort_reconstructed_medicare_standardized_payment_amount",
    "selected_by_candidate_coverage",
    "coverage_target_candidate_npis",
    "source_candidate_rows",
    "selection_reasons",
    "monetary_caveat",
)

MATERIALITY_FROZEN_COLUMNS = (
    "record_type",
    "freeze_algorithm",
    "freeze_algorithm_version",
    "selection_mode",
    "code_discovery_algorithm",
    "code_discovery_algorithm_version",
    "focus_year",
    "candidate_npis_parameter",
    "source_release_ids_parameter",
    "supplier_summary_source_release_id",
    "supplier_service_source_release_id",
    "top_codes_per_candidate",
    "min_cohort_code_payment",
    "target_candidate_visible_payment_share",
    "min_visible_summary_payment_coverage",
    "discovery_maximum_selected_codes",
    "observed_payment_threshold",
    "discovery_input_sha256",
    "discovery_input_bytes",
    "discovery_input_rows",
    "max_input_bytes",
    "max_input_rows",
    "max_output_bytes",
    "max_materiality_codes",
    "selected_code_count",
    "hcpcs_code",
    "hcpcs_descriptions",
    "qualifying_candidate_npis",
    "qualifying_candidate_count",
    "maximum_candidate_reconstructed_medicare_payment_amount",
    "source_candidate_rows",
    "selection_reason",
    "monetary_caveat",
)

_NPI_PATTERN = re.compile(r"[0-9]{10}")
_HCPCS_PATTERN = re.compile(r"[A-Z0-9]{5}")


@dataclass
class _FrozenCode:
    top_n_candidate_npis: set[str] = field(default_factory=set)
    coverage_candidate_npis: set[str] = field(default_factory=set)
    selected_by_cohort_payment: bool = False
    cohort_payment: Decimal | None = None
    cohort_standardized_payment: Decimal | None = None


@dataclass
class _MaterialityFrozenCode:
    qualifying_candidate_npis: set[str] = field(default_factory=set)
    maximum_candidate_payment: Decimal = Decimal(0)


class _BoundedUtf8Writer:
    def __init__(self, output_file: BinaryIO, *, max_bytes: int) -> None:
        self.output_file = output_file
        self.max_bytes = max_bytes
        self.bytes_written = 0

    def write(self, value: str) -> int:
        encoded = value.encode("utf-8")
        projected = self.bytes_written + len(encoded)
        if projected > self.max_bytes:
            raise ValueError(
                f"DMEPOS service-code freeze output would exceed {self.max_bytes} bytes"
            )
        self.output_file.write(encoded)
        self.bytes_written = projected
        return len(value)


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_decimal(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _decimal(value: object, field_name: str, *, positive: bool = False) -> Decimal:
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"DMEPOS code discovery has invalid {field_name}") from None
    if not number.is_finite() or number < 0 or (positive and number <= 0):
        raise ValueError(f"DMEPOS code discovery has invalid {field_name}")
    return number


def _optional_decimal(value: object, field_name: str) -> Decimal | None:
    if str(value).strip() == "":
        return None
    return _decimal(value, field_name)


def _setting_decimal(value: object, field_name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"DMEPOS {field_name} must be a decimal") from None


def _integer(value: object, field_name: str, *, minimum: int = 0) -> int:
    rendered = str(value).strip()
    try:
        number = int(rendered)
    except ValueError:
        raise ValueError(f"DMEPOS code discovery has invalid {field_name}") from None
    if str(number) != rendered or number < minimum:
        raise ValueError(f"DMEPOS code discovery has invalid {field_name}")
    return number


def _boolean(value: object, field_name: str) -> bool:
    rendered = str(value).strip().lower()
    if rendered == "true":
        return True
    if rendered == "false":
        return False
    raise ValueError(f"DMEPOS code discovery has invalid {field_name}")


def _numeric_array(
    value: object,
    field_name: str,
    *,
    item_pattern: re.Pattern[str],
) -> tuple[str, ...]:
    rendered = str(value).strip()
    if len(rendered) < 2 or rendered[0] != "{" or rendered[-1] != "}":
        raise ValueError(f"DMEPOS code discovery has invalid {field_name}")
    items = tuple(rendered[1:-1].split(",")) if rendered != "{}" else ()
    if not items or any(item_pattern.fullmatch(item) is None for item in items):
        raise ValueError(f"DMEPOS code discovery has invalid {field_name}")
    if items != tuple(sorted(set(items), key=int)):
        raise ValueError(f"DMEPOS code discovery has noncanonical {field_name}")
    return items


def _validate_settings(
    *,
    focus_year: int,
    top_codes_per_candidate: int,
    min_cohort_code_payment: Decimal,
    target_candidate_visible_payment_share: Decimal,
    min_visible_summary_payment_coverage: Decimal,
    max_selected_codes: int,
    max_input_bytes: int,
    max_input_rows: int,
    max_output_bytes: int,
) -> None:
    if focus_year not in {2022, 2023, 2024}:
        raise ValueError("DMEPOS focus year must be a reviewed year from 2022 through 2024")
    if not 1 <= top_codes_per_candidate <= 25:
        raise ValueError("DMEPOS top codes per candidate must be between 1 and 25")
    if not min_cohort_code_payment.is_finite() or min_cohort_code_payment < 0:
        raise ValueError("DMEPOS minimum cohort code payment must be finite and nonnegative")
    if not (
        target_candidate_visible_payment_share.is_finite()
        and 0 < target_candidate_visible_payment_share <= 1
    ):
        raise ValueError("DMEPOS candidate visible-payment target must be in (0, 1]")
    if not (
        min_visible_summary_payment_coverage.is_finite()
        and 0 < min_visible_summary_payment_coverage <= 1
    ):
        raise ValueError("DMEPOS visible-summary payment coverage floor must be in (0, 1]")
    if not top_codes_per_candidate <= max_selected_codes <= 100:
        raise ValueError("DMEPOS selected-code cap must be between top N and 100")
    for value, label in (
        (max_input_bytes, "input byte cap"),
        (max_input_rows, "input row cap"),
        (max_output_bytes, "output byte cap"),
    ):
        if value < 1:
            raise ValueError(f"DMEPOS {label} must be positive")


def _validate_materiality_settings(
    *,
    observed_payment_threshold: Decimal,
    max_materiality_codes: int,
) -> None:
    if not observed_payment_threshold.is_finite() or observed_payment_threshold <= 0:
        raise ValueError(
            "DMEPOS materiality observed-payment threshold must be finite and positive"
        )
    if not 1 <= max_materiality_codes <= 10_000:
        raise ValueError("DMEPOS materiality-code cap must be between 1 and 10000")


def _write_frozen_codes(
    output_path: Path,
    rows: list[dict[str, str]],
    *,
    max_output_bytes: int,
    columns: tuple[str, ...] = FROZEN_COLUMNS,
) -> str:
    created = False
    try:
        with output_path.open("xb") as raw_output:
            created = True
            writer = csv.DictWriter(
                _BoundedUtf8Writer(raw_output, max_bytes=max_output_bytes),
                fieldnames=columns,
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)
    except BaseException as error:
        if created:
            try:
                output_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                error.add_note(
                    "Could not remove incomplete DMEPOS code freeze: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
        raise
    return _sha256_file(output_path)


def _freeze_dmepos_service_codes(
    input_path: Path,
    output_path: Path,
    *,
    focus_year: int,
    top_codes_per_candidate: int = DEFAULT_TOP_CODES_PER_CANDIDATE,
    min_cohort_code_payment: Decimal | str | int = DEFAULT_MIN_COHORT_CODE_PAYMENT,
    target_candidate_visible_payment_share: Decimal | str | int = (
        DEFAULT_TARGET_CANDIDATE_VISIBLE_PAYMENT_SHARE
    ),
    min_visible_summary_payment_coverage: Decimal | str | int = (
        DEFAULT_MIN_VISIBLE_SUMMARY_PAYMENT_COVERAGE
    ),
    max_selected_codes: int = DEFAULT_MAX_SELECTED_CODES,
    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
    max_input_rows: int = DEFAULT_MAX_INPUT_ROWS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    materiality_complete: bool = False,
    observed_payment_threshold: Decimal | str | int = DEFAULT_OBSERVED_PAYMENT_THRESHOLD,
    max_materiality_codes: int = DEFAULT_MAX_MATERIALITY_CODES,
) -> tuple[int, int, str]:
    """Validate discovery and freeze one deterministic HCPCS selection contract."""
    min_cohort_code_payment = _setting_decimal(
        min_cohort_code_payment, "minimum cohort code payment"
    )
    target_candidate_visible_payment_share = _setting_decimal(
        target_candidate_visible_payment_share,
        "target candidate visible-payment share",
    )
    min_visible_summary_payment_coverage = _setting_decimal(
        min_visible_summary_payment_coverage,
        "minimum visible-summary payment coverage",
    )
    observed_payment_threshold = _setting_decimal(
        observed_payment_threshold,
        "materiality observed-payment threshold",
    )
    _validate_settings(
        focus_year=focus_year,
        top_codes_per_candidate=top_codes_per_candidate,
        min_cohort_code_payment=min_cohort_code_payment,
        target_candidate_visible_payment_share=target_candidate_visible_payment_share,
        min_visible_summary_payment_coverage=min_visible_summary_payment_coverage,
        max_selected_codes=max_selected_codes,
        max_input_bytes=max_input_bytes,
        max_input_rows=max_input_rows,
        max_output_bytes=max_output_bytes,
    )
    _validate_materiality_settings(
        observed_payment_threshold=observed_payment_threshold,
        max_materiality_codes=max_materiality_codes,
    )
    if input_path.resolve() == output_path.resolve():
        raise ValueError("DMEPOS code discovery input and freeze output must be different files")
    if output_path.exists():
        raise FileExistsError(f"Refusing to replace DMEPOS service-code freeze: {output_path}")
    input_bytes = input_path.stat().st_size
    if input_bytes > max_input_bytes:
        raise ValueError(
            f"DMEPOS code discovery input is {input_bytes} bytes; maximum is {max_input_bytes}"
        )
    input_sha256 = _sha256_file(input_path)

    expected_signature = (
        DISCOVERY_ALGORITHM,
        DISCOVERY_ALGORITHM_VERSION,
        str(focus_year),
        str(top_codes_per_candidate),
        _canonical_decimal(min_cohort_code_payment),
        _canonical_decimal(target_candidate_visible_payment_share),
        _canonical_decimal(min_visible_summary_payment_coverage),
        str(max_selected_codes),
    )
    observed_signature: tuple[str, ...] | None = None
    candidate_npis: tuple[str, ...] | None = None
    source_release_ids: tuple[str, ...] | None = None
    source_release_roles: tuple[str, str] | None = None
    observed_candidates: set[str] = set()
    candidate_coverages: dict[str, Decimal] = {}
    candidate_ranks: dict[str, set[int]] = defaultdict(set)
    candidate_sequences: dict[
        str, dict[int, tuple[str, Decimal, Decimal, Decimal, Decimal, Decimal]]
    ] = defaultdict(dict)
    candidate_portfolios: dict[str, tuple[Decimal, ...]] = {}
    cohort_amounts: dict[str, tuple[Decimal, Decimal]] = {}
    candidate_code_pairs: set[tuple[str, str]] = set()
    codes: dict[str, _FrozenCode] = defaultdict(_FrozenCode)
    materiality_codes: dict[str, _MaterialityFrozenCode] = defaultdict(_MaterialityFrozenCode)
    all_code_descriptions: dict[str, set[str]] = defaultdict(set)
    all_code_row_counts: dict[str, int] = defaultdict(int)
    reconstructed_code_totals: dict[str, tuple[Decimal, Decimal]] = {}
    input_rows = 0

    with input_path.open("r", encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        if tuple(reader.fieldnames or ()) != DISCOVERY_COLUMNS:
            raise ValueError("DMEPOS code discovery CSV has an unexpected column contract")
        for row in reader:
            input_rows += 1
            if input_rows > max_input_rows:
                raise ValueError(f"DMEPOS code discovery input exceeds {max_input_rows} data rows")
            if None in row or any(value is None for value in row.values()):
                raise ValueError("DMEPOS code discovery CSV contains a malformed row")

            signature = (
                row["code_discovery_algorithm"],
                row["code_discovery_algorithm_version"],
                row["focus_year"],
                row["top_codes_per_candidate"],
                _canonical_decimal(
                    _decimal(row["min_cohort_code_payment"], "min_cohort_code_payment")
                ),
                _canonical_decimal(
                    _decimal(
                        row["target_candidate_visible_payment_share"],
                        "target_candidate_visible_payment_share",
                        positive=True,
                    )
                ),
                _canonical_decimal(
                    _decimal(
                        row["min_visible_summary_payment_coverage"],
                        "min_visible_summary_payment_coverage",
                        positive=True,
                    )
                ),
                row["maximum_selected_codes"],
            )
            if signature != expected_signature:
                raise ValueError(
                    "DMEPOS code discovery algorithm or parameter handshake does not match "
                    "the requested freeze"
                )
            if observed_signature is None:
                observed_signature = signature
            elif signature != observed_signature:
                raise ValueError("DMEPOS code discovery mixes run signatures")

            row_candidates = _numeric_array(
                row["candidate_npis_parameter"],
                "candidate_npis_parameter",
                item_pattern=_NPI_PATTERN,
            )
            row_releases = _numeric_array(
                row["source_release_ids_parameter"],
                "source_release_ids_parameter",
                item_pattern=re.compile(r"[1-9][0-9]*"),
            )
            if len(row_candidates) > 100 or len(row_releases) != 2:
                raise ValueError("DMEPOS code discovery has an invalid candidate/release scope")
            if candidate_npis is None:
                candidate_npis = row_candidates
                source_release_ids = row_releases
            elif row_candidates != candidate_npis or row_releases != source_release_ids:
                raise ValueError("DMEPOS code discovery mixes candidate or source-release scopes")

            summary_release_id = row["supplier_summary_source_release_id"].strip()
            service_release_id = row["supplier_service_source_release_id"].strip()
            if (
                summary_release_id == service_release_id
                or source_release_ids is None
                or not summary_release_id.isdigit()
                or not service_release_id.isdigit()
                or tuple(sorted((summary_release_id, service_release_id), key=int))
                != source_release_ids
            ):
                raise ValueError("DMEPOS code discovery row does not match its two pinned releases")
            row_release_roles = (summary_release_id, service_release_id)
            if source_release_roles is None:
                source_release_roles = row_release_roles
            elif row_release_roles != source_release_roles:
                raise ValueError("DMEPOS code discovery mixes source-release roles")

            npi = row["supplier_npi"].strip()
            hcpcs_code = row["hcpcs_code"].strip()
            description = row["hcpcs_description"].strip()
            if candidate_npis is None or npi not in candidate_npis:
                raise ValueError("DMEPOS code discovery row is outside the candidate scope")
            if _HCPCS_PATTERN.fullmatch(hcpcs_code) is None or not description:
                raise ValueError(
                    "DMEPOS code discovery row has an invalid HCPCS code or description"
                )
            if row["entity_code"] not in {"I", "O"}:
                raise ValueError("DMEPOS code discovery row has an invalid entity code")
            pair = (npi, hcpcs_code)
            if pair in candidate_code_pairs:
                raise ValueError("DMEPOS code discovery repeats a candidate-NPI/HCPCS row")
            candidate_code_pairs.add(pair)
            observed_candidates.add(npi)

            rental_cell_count = _integer(row["rental_cell_count"], "rental_cell_count", minimum=1)
            rental_indicators = tuple(row["rental_indicators"].split(","))
            if (
                rental_cell_count not in {1, 2}
                or rental_indicators != tuple(sorted(set(rental_indicators)))
                or any(value not in {"N", "Y"} for value in rental_indicators)
                or len(rental_indicators) != rental_cell_count
            ):
                raise ValueError("DMEPOS code discovery has invalid aggregated rental provenance")

            _integer(
                row["nonunique_cell_claim_count_sum"],
                "nonunique_cell_claim_count_sum",
            )
            parsed_amounts: dict[str, Decimal] = {}
            for field_name in (
                "total_services",
                "reconstructed_submitted_charge",
                "reconstructed_medicare_allowed_amount",
                "reconstructed_medicare_payment_amount",
                "reconstructed_medicare_standardized_payment_amount",
                "summary_total_submitted_charge",
                "summary_total_medicare_allowed_amount",
                "summary_total_medicare_payment_amount",
                "summary_total_medicare_standardized_payment_amount",
                "visible_detail_reconstructed_submitted_charge",
                "visible_detail_reconstructed_medicare_allowed_amount",
                "visible_detail_reconstructed_medicare_payment_amount",
                "visible_detail_reconstructed_standardized_payment_amount",
            ):
                parsed_amounts[field_name] = _decimal(row[field_name], field_name)
            for field_name in (
                "visible_summary_submitted_charge_coverage",
                "visible_summary_allowed_amount_coverage",
                "visible_summary_standardized_payment_coverage",
            ):
                _optional_decimal(row[field_name], field_name)

            payment_coverage = _decimal(
                row["visible_summary_payment_coverage"],
                "visible_summary_payment_coverage",
                positive=True,
            )
            if payment_coverage < min_visible_summary_payment_coverage:
                raise ValueError(
                    "DMEPOS code discovery contains a candidate below the coverage floor"
                )
            if (
                parsed_amounts["summary_total_medicare_payment_amount"] <= 0
                or parsed_amounts["visible_detail_reconstructed_medicare_payment_amount"] <= 0
            ):
                raise ValueError(
                    "DMEPOS code discovery requires positive summary and detail payment"
                )
            previous_coverage = candidate_coverages.setdefault(npi, payment_coverage)
            if previous_coverage != payment_coverage:
                raise ValueError("DMEPOS code discovery has inconsistent candidate reconciliation")
            portfolio_signature = tuple(
                parsed_amounts[field_name]
                for field_name in (
                    "summary_total_submitted_charge",
                    "summary_total_medicare_allowed_amount",
                    "summary_total_medicare_payment_amount",
                    "summary_total_medicare_standardized_payment_amount",
                    "visible_detail_reconstructed_submitted_charge",
                    "visible_detail_reconstructed_medicare_allowed_amount",
                    "visible_detail_reconstructed_medicare_payment_amount",
                    "visible_detail_reconstructed_standardized_payment_amount",
                )
            )
            previous_portfolio = candidate_portfolios.setdefault(npi, portfolio_signature)
            if previous_portfolio != portfolio_signature:
                raise ValueError(
                    "DMEPOS code discovery has inconsistent candidate portfolio totals"
                )

            rank = _integer(row["candidate_payment_rank"], "candidate_payment_rank", minimum=1)
            if rank in candidate_ranks[npi]:
                raise ValueError("DMEPOS code discovery repeats a candidate payment rank")
            candidate_ranks[npi].add(rank)
            prior_payment = _decimal(
                row["candidate_prior_cumulative_visible_payment"],
                "candidate_prior_cumulative_visible_payment",
            )
            after_payment = _decimal(
                row["candidate_after_cumulative_visible_payment"],
                "candidate_after_cumulative_visible_payment",
            )
            prior_share = _decimal(
                row["candidate_prior_cumulative_visible_payment_share"],
                "candidate_prior_cumulative_visible_payment_share",
            )
            after_share = _decimal(
                row["candidate_after_cumulative_visible_payment_share"],
                "candidate_after_cumulative_visible_payment_share",
            )
            if after_payment < prior_payment or prior_share > after_share or after_share > 1:
                raise ValueError("DMEPOS code discovery has invalid cumulative payment ordering")
            code_payment = parsed_amounts["reconstructed_medicare_payment_amount"]
            code_standardized_payment = parsed_amounts[
                "reconstructed_medicare_standardized_payment_amount"
            ]
            if code_payment >= observed_payment_threshold:
                materiality_code = materiality_codes[hcpcs_code]
                materiality_code.qualifying_candidate_npis.add(npi)
                materiality_code.maximum_candidate_payment = max(
                    materiality_code.maximum_candidate_payment,
                    code_payment,
                )
            candidate_sequences[npi][rank] = (
                hcpcs_code,
                code_payment,
                prior_payment,
                after_payment,
                prior_share,
                after_share,
            )

            cohort_payment = _decimal(
                row["cohort_reconstructed_medicare_payment_amount"],
                "cohort_reconstructed_medicare_payment_amount",
            )
            cohort_standardized_payment = _decimal(
                row["cohort_reconstructed_medicare_standardized_payment_amount"],
                "cohort_reconstructed_medicare_standardized_payment_amount",
            )
            selected_top = _boolean(
                row["selected_by_candidate_top_n"], "selected_by_candidate_top_n"
            )
            selected_cohort = _boolean(
                row["selected_by_cohort_payment"], "selected_by_cohort_payment"
            )
            selected_coverage = _boolean(
                row["selected_by_candidate_coverage"], "selected_by_candidate_coverage"
            )
            selected_for_freeze = _boolean(
                row["selected_for_code_freeze"], "selected_for_code_freeze"
            )
            if selected_top != (rank <= top_codes_per_candidate):
                raise ValueError("DMEPOS code discovery top-N reason is inconsistent")
            if selected_cohort != (cohort_payment >= min_cohort_code_payment):
                raise ValueError("DMEPOS code discovery cohort-payment reason is inconsistent")
            if selected_coverage != (prior_share < target_candidate_visible_payment_share):
                raise ValueError("DMEPOS code discovery coverage reason is inconsistent")
            reasons = tuple(
                reason
                for selected, reason in (
                    (selected_top, "candidate-top-n"),
                    (selected_cohort, "cohort-payment-floor"),
                    (selected_coverage, "candidate-coverage-target"),
                )
                if selected
            )
            if selected_for_freeze != bool(reasons):
                raise ValueError("DMEPOS code discovery selected-row marker is inconsistent")
            if row["selection_reason"] != "|".join(reasons):
                raise ValueError("DMEPOS code discovery selection reason is inconsistent")
            if row["monetary_caveat"] != MONETARY_CAVEAT:
                raise ValueError(
                    "DMEPOS code discovery is missing the monetary interpretation caveat"
                )

            cohort_signature = (cohort_payment, cohort_standardized_payment)
            previous_cohort = cohort_amounts.setdefault(hcpcs_code, cohort_signature)
            if previous_cohort != cohort_signature:
                raise ValueError("DMEPOS code discovery has inconsistent cohort code amounts")
            prior_code_totals = reconstructed_code_totals.get(hcpcs_code, (Decimal(0), Decimal(0)))
            reconstructed_code_totals[hcpcs_code] = (
                prior_code_totals[0] + code_payment,
                prior_code_totals[1] + code_standardized_payment,
            )
            all_code_descriptions[hcpcs_code].add(description)
            all_code_row_counts[hcpcs_code] += 1
            if selected_for_freeze:
                code = codes[hcpcs_code]
                if selected_top:
                    code.top_n_candidate_npis.add(npi)
                if selected_coverage:
                    code.coverage_candidate_npis.add(npi)
                code.selected_by_cohort_payment |= selected_cohort
                if code.cohort_payment is None:
                    code.cohort_payment = cohort_payment
                    code.cohort_standardized_payment = cohort_standardized_payment
                elif (
                    code.cohort_payment != cohort_payment
                    or code.cohort_standardized_payment != cohort_standardized_payment
                ):
                    raise ValueError("DMEPOS code discovery has inconsistent selected-code amounts")

    if input_path.stat().st_size != input_bytes or _sha256_file(input_path) != input_sha256:
        raise ValueError("DMEPOS code discovery input changed while it was being validated")
    if (
        input_rows == 0
        or observed_signature is None
        or candidate_npis is None
        or source_release_roles is None
    ):
        raise ValueError("DMEPOS code discovery CSV contains no selected data rows")
    if observed_candidates != set(candidate_npis):
        raise ValueError("DMEPOS code discovery does not represent every requested candidate")
    for npi in candidate_npis:
        sequence = candidate_sequences[npi]
        if sorted(sequence) != list(range(1, len(sequence) + 1)):
            raise ValueError("DMEPOS code discovery does not contain a complete candidate ranking")
        prior_code: tuple[str, Decimal] | None = None
        prior_after_payment = Decimal(0)
        prior_after_share = Decimal(0)
        for rank in range(1, len(sequence) + 1):
            (
                hcpcs_code,
                code_payment,
                prior_payment,
                after_payment,
                prior_share,
                after_share,
            ) = sequence[rank]
            if prior_payment != prior_after_payment or prior_share != prior_after_share:
                raise ValueError("DMEPOS code discovery has a discontinuous cumulative ranking")
            if after_payment != prior_payment + code_payment:
                raise ValueError("DMEPOS code discovery cumulative payment does not reproduce")
            if prior_code is not None and (
                code_payment > prior_code[1]
                or (code_payment == prior_code[1] and hcpcs_code < prior_code[0])
            ):
                raise ValueError("DMEPOS code discovery is not deterministically payment-ranked")
            prior_code = (hcpcs_code, code_payment)
            prior_after_payment = after_payment
            prior_after_share = after_share
        visible_payment = candidate_portfolios[npi][6]
        if prior_after_payment != visible_payment or prior_after_share != 1:
            raise ValueError("DMEPOS code discovery omits part of a candidate decomposition")
        summary_payment = candidate_portfolios[npi][2]
        reproduced_coverage = visible_payment / summary_payment
        if abs(reproduced_coverage - candidate_coverages[npi]) > Decimal("1e-18"):
            raise ValueError("DMEPOS code discovery payment coverage does not reproduce")
        for _, _, _, after_payment, _, after_share in sequence.values():
            reproduced_share = after_payment / visible_payment
            if abs(reproduced_share - after_share) > Decimal("1e-18"):
                raise ValueError("DMEPOS code discovery cumulative share does not reproduce")
    if cohort_amounts != reconstructed_code_totals:
        raise ValueError("DMEPOS code discovery cohort code totals do not reproduce")
    if materiality_complete:
        if not materiality_codes:
            raise ValueError(
                "DMEPOS code discovery has no HCPCS code meeting the materiality threshold"
            )
        if len(materiality_codes) > max_materiality_codes:
            raise ValueError(
                "DMEPOS code discovery selected "
                f"{len(materiality_codes)} materiality-complete codes; maximum is "
                f"{max_materiality_codes}"
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        selected_code_count = len(materiality_codes)
        candidate_parameter = "{" + ",".join(candidate_npis) + "}"
        release_parameter = "{" + ",".join(source_release_ids or ()) + "}"
        materiality_rows: list[dict[str, str]] = []
        for hcpcs_code, code in sorted(materiality_codes.items()):
            qualifying_npis = sorted(code.qualifying_candidate_npis)
            materiality_rows.append(
                {
                    "record_type": "materiality-selected-code",
                    "freeze_algorithm": MATERIALITY_FREEZE_ALGORITHM,
                    "freeze_algorithm_version": MATERIALITY_FREEZE_ALGORITHM_VERSION,
                    "selection_mode": MATERIALITY_SELECTION_MODE,
                    "code_discovery_algorithm": DISCOVERY_ALGORITHM,
                    "code_discovery_algorithm_version": DISCOVERY_ALGORITHM_VERSION,
                    "focus_year": str(focus_year),
                    "candidate_npis_parameter": candidate_parameter,
                    "source_release_ids_parameter": release_parameter,
                    "supplier_summary_source_release_id": source_release_roles[0],
                    "supplier_service_source_release_id": source_release_roles[1],
                    "top_codes_per_candidate": str(top_codes_per_candidate),
                    "min_cohort_code_payment": _canonical_decimal(min_cohort_code_payment),
                    "target_candidate_visible_payment_share": _canonical_decimal(
                        target_candidate_visible_payment_share
                    ),
                    "min_visible_summary_payment_coverage": _canonical_decimal(
                        min_visible_summary_payment_coverage
                    ),
                    "discovery_maximum_selected_codes": str(max_selected_codes),
                    "observed_payment_threshold": _canonical_decimal(observed_payment_threshold),
                    "discovery_input_sha256": input_sha256,
                    "discovery_input_bytes": str(input_bytes),
                    "discovery_input_rows": str(input_rows),
                    "max_input_bytes": str(max_input_bytes),
                    "max_input_rows": str(max_input_rows),
                    "max_output_bytes": str(max_output_bytes),
                    "max_materiality_codes": str(max_materiality_codes),
                    "selected_code_count": str(selected_code_count),
                    "hcpcs_code": hcpcs_code,
                    "hcpcs_descriptions": " | ".join(sorted(all_code_descriptions[hcpcs_code])),
                    "qualifying_candidate_npis": "{" + ",".join(qualifying_npis) + "}",
                    "qualifying_candidate_count": str(len(qualifying_npis)),
                    "maximum_candidate_reconstructed_medicare_payment_amount": (
                        _canonical_decimal(code.maximum_candidate_payment)
                    ),
                    "source_candidate_rows": str(all_code_row_counts[hcpcs_code]),
                    "selection_reason": (
                        "candidate-aggregate-observed-payment-at-or-above-threshold"
                    ),
                    "monetary_caveat": MONETARY_CAVEAT,
                }
            )
        output_sha256 = _write_frozen_codes(
            output_path,
            materiality_rows,
            max_output_bytes=max_output_bytes,
            columns=MATERIALITY_FROZEN_COLUMNS,
        )
        return selected_code_count, input_rows, output_sha256

    if not codes:
        raise ValueError("DMEPOS code discovery selected no HCPCS codes")
    if len(codes) > max_selected_codes:
        raise ValueError(
            f"DMEPOS code discovery selected {len(codes)} codes; maximum is {max_selected_codes}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    selected_code_count = len(codes)
    candidate_parameter = "{" + ",".join(candidate_npis) + "}"
    release_parameter = "{" + ",".join(source_release_ids or ()) + "}"
    frozen_rows: list[dict[str, str]] = []
    for hcpcs_code, code in sorted(codes.items()):
        top_selected = bool(code.top_n_candidate_npis)
        coverage_selected = bool(code.coverage_candidate_npis)
        reasons = tuple(
            reason
            for selected, reason in (
                (top_selected, "candidate-top-n"),
                (code.selected_by_cohort_payment, "cohort-payment-floor"),
                (coverage_selected, "candidate-coverage-target"),
            )
            if selected
        )
        frozen_rows.append(
            {
                "record_type": "selected-code",
                "freeze_algorithm": FREEZE_ALGORITHM,
                "freeze_algorithm_version": FREEZE_ALGORITHM_VERSION,
                "code_discovery_algorithm": DISCOVERY_ALGORITHM,
                "code_discovery_algorithm_version": DISCOVERY_ALGORITHM_VERSION,
                "focus_year": str(focus_year),
                "candidate_npis_parameter": candidate_parameter,
                "source_release_ids_parameter": release_parameter,
                "top_codes_per_candidate": str(top_codes_per_candidate),
                "min_cohort_code_payment": _canonical_decimal(min_cohort_code_payment),
                "target_candidate_visible_payment_share": _canonical_decimal(
                    target_candidate_visible_payment_share
                ),
                "min_visible_summary_payment_coverage": _canonical_decimal(
                    min_visible_summary_payment_coverage
                ),
                "maximum_selected_codes": str(max_selected_codes),
                "discovery_input_sha256": input_sha256,
                "discovery_input_bytes": str(input_bytes),
                "discovery_input_rows": str(input_rows),
                "max_input_bytes": str(max_input_bytes),
                "max_input_rows": str(max_input_rows),
                "max_output_bytes": str(max_output_bytes),
                "selected_code_count": str(selected_code_count),
                "hcpcs_code": hcpcs_code,
                "hcpcs_descriptions": " | ".join(sorted(all_code_descriptions[hcpcs_code])),
                "selected_by_candidate_top_n": str(top_selected).lower(),
                "top_n_candidate_npis": "{" + ",".join(sorted(code.top_n_candidate_npis)) + "}",
                "selected_by_cohort_payment": str(code.selected_by_cohort_payment).lower(),
                "cohort_reconstructed_medicare_payment_amount": _canonical_decimal(
                    code.cohort_payment or Decimal(0)
                ),
                "cohort_reconstructed_medicare_standardized_payment_amount": (
                    _canonical_decimal(code.cohort_standardized_payment or Decimal(0))
                ),
                "selected_by_candidate_coverage": str(coverage_selected).lower(),
                "coverage_target_candidate_npis": "{"
                + ",".join(sorted(code.coverage_candidate_npis))
                + "}",
                "source_candidate_rows": str(all_code_row_counts[hcpcs_code]),
                "selection_reasons": "|".join(reasons),
                "monetary_caveat": MONETARY_CAVEAT,
            }
        )
    output_sha256 = _write_frozen_codes(
        output_path,
        frozen_rows,
        max_output_bytes=max_output_bytes,
    )
    return selected_code_count, input_rows, output_sha256


def freeze_dmepos_service_codes(
    input_path: Path,
    output_path: Path,
    *,
    focus_year: int,
    top_codes_per_candidate: int = DEFAULT_TOP_CODES_PER_CANDIDATE,
    min_cohort_code_payment: Decimal | str | int = DEFAULT_MIN_COHORT_CODE_PAYMENT,
    target_candidate_visible_payment_share: Decimal | str | int = (
        DEFAULT_TARGET_CANDIDATE_VISIBLE_PAYMENT_SHARE
    ),
    min_visible_summary_payment_coverage: Decimal | str | int = (
        DEFAULT_MIN_VISIBLE_SUMMARY_PAYMENT_COVERAGE
    ),
    max_selected_codes: int = DEFAULT_MAX_SELECTED_CODES,
    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
    max_input_rows: int = DEFAULT_MAX_INPUT_ROWS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> tuple[int, int, str]:
    """Freeze the maintained portfolio-oriented HCPCS union."""
    return _freeze_dmepos_service_codes(
        input_path,
        output_path,
        focus_year=focus_year,
        top_codes_per_candidate=top_codes_per_candidate,
        min_cohort_code_payment=min_cohort_code_payment,
        target_candidate_visible_payment_share=target_candidate_visible_payment_share,
        min_visible_summary_payment_coverage=min_visible_summary_payment_coverage,
        max_selected_codes=max_selected_codes,
        max_input_bytes=max_input_bytes,
        max_input_rows=max_input_rows,
        max_output_bytes=max_output_bytes,
    )


def freeze_dmepos_materiality_codes(
    input_path: Path,
    output_path: Path,
    *,
    focus_year: int,
    observed_payment_threshold: Decimal | str | int = DEFAULT_OBSERVED_PAYMENT_THRESHOLD,
    top_codes_per_candidate: int = DEFAULT_TOP_CODES_PER_CANDIDATE,
    min_cohort_code_payment: Decimal | str | int = DEFAULT_MIN_COHORT_CODE_PAYMENT,
    target_candidate_visible_payment_share: Decimal | str | int = (
        DEFAULT_TARGET_CANDIDATE_VISIBLE_PAYMENT_SHARE
    ),
    min_visible_summary_payment_coverage: Decimal | str | int = (
        DEFAULT_MIN_VISIBLE_SUMMARY_PAYMENT_COVERAGE
    ),
    max_selected_codes: int = DEFAULT_MAX_SELECTED_CODES,
    max_materiality_codes: int = DEFAULT_MAX_MATERIALITY_CODES,
    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
    max_input_rows: int = DEFAULT_MAX_INPUT_ROWS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> tuple[int, int, str]:
    """Freeze every code able to contain a material focus-year exact rental cell."""
    return _freeze_dmepos_service_codes(
        input_path,
        output_path,
        focus_year=focus_year,
        top_codes_per_candidate=top_codes_per_candidate,
        min_cohort_code_payment=min_cohort_code_payment,
        target_candidate_visible_payment_share=target_candidate_visible_payment_share,
        min_visible_summary_payment_coverage=min_visible_summary_payment_coverage,
        max_selected_codes=max_selected_codes,
        max_input_bytes=max_input_bytes,
        max_input_rows=max_input_rows,
        max_output_bytes=max_output_bytes,
        materiality_complete=True,
        observed_payment_threshold=observed_payment_threshold,
        max_materiality_codes=max_materiality_codes,
    )
