from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path

REQUIRED_COLUMNS = {
    "data_year",
    "rendering_npi",
    "hcpcs_code",
    "place_of_service",
    "provider_last_org_name",
    "provider_first_name",
    "provider_state",
    "panel_volume_band",
    "provider_total_beneficiaries",
    "tested_beneficiaries",
    "panel_penetration",
    "days_per_tested_beneficiary",
    "units_per_beneficiary_day",
    "peer_evaluation_status",
    "peer_count",
    "reach_empirical_percentile",
    "repeat_days_empirical_percentile",
    "units_per_day_empirical_percentile",
    "reach_outlier_flag",
    "repeat_day_intensity_flag",
    "unit_intensity_flag",
    "triage_route",
    "comparable_years",
    "reach_full_flag_years",
    "repeat_days_full_flag_years",
    "unit_full_flag_years",
    "reach_temporally_confirmed",
    "repeat_days_temporally_confirmed",
    "unit_temporally_confirmed",
    "temporal_triage_route",
    "broad_peer_count",
    "broad_reach_empirical_percentile",
    "broad_repeat_days_empirical_percentile",
    "broader_peer_sensitivity_status",
    "reconstructed_medicare_payment_amount",
}

OUTPUT_COLUMNS = (
    "selection_priority",
    "selection_reasons",
    "data_year",
    "rendering_npi",
    "provider_name",
    "provider_state",
    "hcpcs_code",
    "place_of_service",
    "panel_volume_band",
    "provider_total_beneficiaries",
    "tested_beneficiaries",
    "panel_penetration",
    "days_per_tested_beneficiary",
    "units_per_beneficiary_day",
    "peer_evaluation_status",
    "peer_count",
    "reach_empirical_percentile",
    "repeat_days_empirical_percentile",
    "units_per_day_empirical_percentile",
    "reach_outlier_flag",
    "repeat_day_intensity_flag",
    "unit_intensity_flag",
    "triage_route",
    "comparable_years",
    "reach_full_flag_years",
    "repeat_days_full_flag_years",
    "unit_full_flag_years",
    "reach_temporally_confirmed",
    "repeat_days_temporally_confirmed",
    "unit_temporally_confirmed",
    "temporal_triage_route",
    "broad_peer_count",
    "broad_reach_empirical_percentile",
    "broad_repeat_days_empirical_percentile",
    "broader_peer_sensitivity_status",
    "reconstructed_medicare_payment_amount",
)


def _boolean(value: str | None, field: str) -> bool:
    normalized = (value or "").strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"Part B scan output has invalid boolean {field}={value!r}")


def _required_number(value: str | None, field: str) -> Decimal:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError(f"Part B scan output has blank required number: {field}")
    try:
        number = Decimal(normalized)
    except InvalidOperation:
        raise ValueError(f"Part B scan output has invalid number {field}={value!r}") from None
    if not number.is_finite():
        raise ValueError(f"Part B scan output has non-finite number {field}={value!r}")
    return number


def _optional_number(value: str | None, field: str) -> Decimal | None:
    normalized = (value or "").strip()
    if not normalized:
        return None
    try:
        number = Decimal(normalized)
    except InvalidOperation:
        raise ValueError(f"Part B scan output has invalid number {field}={value!r}") from None
    if not number.is_finite():
        raise ValueError(f"Part B scan output has non-finite number {field}={value!r}")
    return number


def _selection_reasons(row: dict[str, str]) -> tuple[str, ...]:
    reasons: list[str] = []
    broad_reach = _optional_number(
        row["broad_reach_empirical_percentile"],
        "broad_reach_empirical_percentile",
    )
    broad_repeat = _optional_number(
        row["broad_repeat_days_empirical_percentile"],
        "broad_repeat_days_empirical_percentile",
    )
    if _boolean(row["reach_temporally_confirmed"], "reach_temporally_confirmed"):
        reasons.append("temporal-reach")
    if _boolean(row["repeat_days_temporally_confirmed"], "repeat_days_temporally_confirmed"):
        reasons.append("temporal-repeat")
    if _boolean(row["unit_temporally_confirmed"], "unit_temporally_confirmed"):
        reasons.append("temporal-unit")
    if _boolean(row["reach_outlier_flag"], "reach_outlier_flag"):
        reasons.append("latest-full-reach")
    if _boolean(row["repeat_day_intensity_flag"], "repeat_day_intensity_flag"):
        reasons.append("latest-full-repeat")
    if _boolean(row["unit_intensity_flag"], "unit_intensity_flag"):
        reasons.append("latest-full-unit")
    if row["broader_peer_sensitivity_status"] == "broader-peer-tail-descriptive":
        if broad_reach is None or broad_repeat is None:
            raise ValueError("Broader-peer tail row is missing its sensitivity percentiles")
        if broad_reach >= Decimal("99"):
            reasons.append("broader-peer-reach-provisional")
        if broad_repeat >= Decimal("99"):
            reasons.append("broader-peer-repeat-provisional")
    return tuple(reasons)


def _priority(reasons: tuple[str, ...]) -> int:
    reason_set = set(reasons)
    if {"temporal-reach", "temporal-repeat"} <= reason_set or {
        "temporal-reach",
        "temporal-unit",
    } <= reason_set:
        return 1
    if "temporal-reach" in reason_set:
        return 2
    if "latest-full-reach" in reason_set and (
        "latest-full-repeat" in reason_set or "latest-full-unit" in reason_set
    ):
        return 3
    if "latest-full-reach" in reason_set:
        return 4
    if reason_set & {"temporal-repeat", "temporal-unit"}:
        return 5
    if reason_set & {"latest-full-repeat", "latest-full-unit"}:
        return 6
    return 7


def _validate_header(fieldnames: list[str] | None) -> None:
    observed = set(fieldnames or [])
    missing = sorted(REQUIRED_COLUMNS - observed)
    if missing:
        raise ValueError(f"Part B scan output is missing columns: {', '.join(missing)}")


def _latest_year(input_path: Path) -> int:
    latest: int | None = None
    with input_path.open(encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        _validate_header(reader.fieldnames)
        for row in reader:
            year = int(row["data_year"])
            latest = year if latest is None else max(latest, year)
    if latest is None:
        raise ValueError("Part B scan output contains no data rows")
    return latest


def generate_part_b_shortlist(input_path: Path, output_path: Path) -> tuple[int, str]:
    """Create a deterministic latest-year review list from a complete Part B scan."""
    if output_path.exists():
        raise FileExistsError(f"Refusing to replace shortlist: {output_path}")
    latest_year = _latest_year(input_path)
    selected: list[dict[str, str]] = []

    with input_path.open(encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        _validate_header(reader.fieldnames)
        for source_row in reader:
            if int(source_row["data_year"]) != latest_year:
                continue
            reasons = _selection_reasons(source_row)
            if not reasons:
                continue
            provider_name = " ".join(
                part.strip()
                for part in (
                    source_row["provider_first_name"],
                    source_row["provider_last_org_name"],
                )
                if part.strip()
            )
            output_row = {column: source_row.get(column, "") for column in OUTPUT_COLUMNS}
            output_row["selection_priority"] = str(_priority(reasons))
            output_row["selection_reasons"] = ";".join(reasons)
            output_row["provider_name"] = provider_name
            selected.append(output_row)

    selected.sort(
        key=lambda row: (
            int(row["selection_priority"]),
            -_required_number(row["panel_penetration"], "panel_penetration"),
            -_required_number(row["days_per_tested_beneficiary"], "days_per_tested_beneficiary"),
            -_required_number(
                row["reconstructed_medicare_payment_amount"],
                "reconstructed_medicare_payment_amount",
            ),
            row["rendering_npi"],
            row["hcpcs_code"],
            row["place_of_service"],
        )
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    digest = sha256()
    with output_path.open("x", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=OUTPUT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in selected:
            writer.writerow(row)
    with output_path.open("rb") as output_file:
        for chunk in iter(lambda: output_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return len(selected), digest.hexdigest()
