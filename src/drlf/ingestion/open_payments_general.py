from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

import psycopg

from drlf.sources.manifest import load_and_validate_manifest
from drlf.sources.open_payments import (
    MAX_TARGETED_SQL_RESPONSE_BYTES,
    profile_open_payments_sql_response,
)

DATASET_SLUG = "open-payments-general-payments"
AGGREGATION_KEYS = ("record_id",)
API_RESPONSE_ROLE = "api-response"
MAX_TARGETED_API_RESPONSE_BYTES = MAX_TARGETED_SQL_RESPONSE_BYTES


def _text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _required_text(row: dict[str, Any], field: str) -> str:
    value = _text(row.get(field))
    if value is None:
        raise ValueError(f"Open Payments row requires {field}")
    return value


def _one_of(row: dict[str, Any], *fields: str) -> str | None:
    for field in fields:
        value = _text(row.get(field))
        if value is not None:
            return value
    return None


def _integer(value: Any) -> int | None:
    normalized = _text(value)
    return int(normalized) if normalized is not None else None


def _decimal(value: Any) -> Decimal | None:
    normalized = _text(value)
    return Decimal(normalized) if normalized is not None else None


def _date(value: Any) -> date | None:
    normalized = _text(value)
    if normalized is None:
        return None
    return datetime.strptime(normalized, "%m/%d/%Y").date()


def _casefold_keys(row: dict[str, Any]) -> dict[str, Any]:
    return {str(key).casefold(): value for key, value in row.items()}


def _series(row: dict[str, Any], stem: str, count: int) -> list[str]:
    values: list[str] = []
    for ordinal in range(1, count + 1):
        value = _text(row.get(f"{stem}{ordinal}"))
        if value is not None:
            values.append(value)
    return values


def _series_one_of(row: dict[str, Any], stems: tuple[str, ...], count: int) -> list[str]:
    values: list[str] = []
    for ordinal in range(1, count + 1):
        value = _one_of(row, *(f"{stem}{ordinal}" for stem in stems))
        if value is not None:
            values.append(value)
    return values


def _series_with_scalar_alias(
    row: dict[str, Any],
    stems: tuple[str, ...],
    count: int,
    *scalar_aliases: str,
) -> list[str]:
    values = _series_one_of(row, stems, count)
    if values:
        return values
    scalar = _one_of(row, *scalar_aliases)
    return [scalar] if scalar is not None else []


def _product_slots(row: dict[str, Any], count: int = 5) -> list[dict[str, Any]]:
    """Preserve the source slot joining each published product attribute."""
    slots: list[dict[str, Any]] = []
    for ordinal in range(1, count + 1):
        slot = {
            "slot": ordinal,
            "coverage_indicator": _text(row.get(f"covered_or_noncovered_indicator_{ordinal}")),
            "product_type": _text(
                row.get(
                    "indicate_drug_or_biological_or_device_or_medical_supply_"
                    f"{ordinal}"
                )
            ),
            "category_or_therapeutic_area": _text(
                row.get(f"product_category_or_therapeutic_area_{ordinal}")
            ),
            "name": _text(
                row.get(f"name_of_drug_or_biological_or_device_or_medical_supply_{ordinal}")
            ),
            "ndc": _text(row.get(f"associated_drug_or_biological_ndc_{ordinal}")),
            "pdi": _text(row.get(f"associated_device_or_medical_supply_pdi_{ordinal}")),
        }
        if any(value is not None for key, value in slot.items() if key != "slot"):
            slots.append(slot)
    return slots


def _row_values(
    source_row: dict[str, Any],
    source_release_id: int,
    program_year: int,
) -> tuple[Any, ...]:
    """Map one General Payment API row while preserving explicit role semantics."""
    row = _casefold_keys(source_row)
    row_year = _integer(row.get("program_year"))
    if row_year != program_year:
        raise ValueError(
            f"Open Payments row program year {row_year!r} does not match manifest {program_year}"
        )

    total_amount = _decimal(row.get("total_amount_of_payment_usdollars"))
    if total_amount is None:
        raise ValueError("Open Payments row requires total_amount_of_payment_usdollars")
    payment_date = _date(row.get("date_of_payment"))
    if payment_date is None:
        raise ValueError("Open Payments row requires date_of_payment")

    return (
        source_release_id,
        program_year,
        _required_text(row, "record_id"),
        _text(row.get("change_type")),
        _text(row.get("covered_recipient_type")),
        _one_of(row, "covered_recipient_profile_id", "physician_profile_id"),
        _one_of(row, "covered_recipient_npi", "physician_npi"),
        _one_of(row, "covered_recipient_first_name", "physician_first_name"),
        _one_of(row, "covered_recipient_middle_name", "physician_middle_name"),
        _one_of(row, "covered_recipient_last_name", "physician_last_name"),
        _one_of(row, "covered_recipient_name_suffix", "physician_name_suffix"),
        _text(row.get("recipient_city")),
        _text(row.get("recipient_state")),
        _text(row.get("recipient_zip_code")),
        _text(row.get("recipient_country")),
        _series_with_scalar_alias(
            row,
            ("covered_recipient_primary_type_", "physician_primary_type_"),
            6,
            "physician_primary_type",
        ),
        _series_with_scalar_alias(
            row,
            ("covered_recipient_specialty_", "physician_specialty_"),
            6,
            "physician_specialty",
        ),
        _series_one_of(
            row,
            ("covered_recipient_license_state_code", "physician_license_state_code"),
            5,
        ),
        _text(row.get("submitting_applicable_manufacturer_or_applicable_gpo_name")),
        _required_text(
            row,
            "applicable_manufacturer_or_applicable_gpo_making_payment_id",
        ),
        _required_text(
            row,
            "applicable_manufacturer_or_applicable_gpo_making_payment_name",
        ),
        _text(row.get("applicable_manufacturer_or_applicable_gpo_making_payment_state")),
        _text(row.get("applicable_manufacturer_or_applicable_gpo_making_payment_country")),
        total_amount,
        payment_date,
        _integer(row.get("number_of_payments_included_in_total_amount")),
        _text(row.get("form_of_payment_or_transfer_of_value")),
        _text(row.get("nature_of_payment_or_transfer_of_value")),
        _text(row.get("city_of_travel")),
        _text(row.get("state_of_travel")),
        _text(row.get("country_of_travel")),
        _text(row.get("physician_ownership_indicator")),
        _one_of(
            row,
            "third_party_payment_recipient_indicator",
            "indicate_third_party_payment_recipient",
        ),
        _text(row.get("name_of_third_party_entity_receiving_payment_or_transfer_of_value")),
        _text(row.get("charity_indicator")),
        _text(row.get("third_party_equals_covered_recipient_indicator")),
        _text(row.get("contextual_information")),
        _text(row.get("delay_in_publication_indicator")),
        _text(row.get("dispute_status_for_publication")),
        _text(row.get("related_product_indicator")),
        _series(row, "covered_or_noncovered_indicator_", 5),
        _series(row, "indicate_drug_or_biological_or_device_or_medical_supply_", 5),
        _series(row, "product_category_or_therapeutic_area_", 5),
        _series(row, "name_of_drug_or_biological_or_device_or_medical_supply_", 5),
        _series(row, "associated_drug_or_biological_ndc_", 5),
        _series(row, "associated_device_or_medical_supply_pdi_", 5),
        json.dumps(_product_slots(row), separators=(",", ":"), sort_keys=True),
        _date(row.get("payment_publication_date")),
        _text(row.get("teaching_hospital_ccn")),
        _text(row.get("teaching_hospital_id")),
        _text(row.get("teaching_hospital_name")),
        _text(row.get("recipient_primary_business_street_address_line1")),
        _text(row.get("recipient_primary_business_street_address_line2")),
        _text(row.get("recipient_province")),
        _text(row.get("recipient_postal_code")),
    )


def _relative_manifest_path(manifest_path: Path) -> str:
    return manifest_path.resolve().relative_to(Path.cwd().resolve()).as_posix()


def _validate_files(files: list[dict[str, Any]]) -> None:
    """Validate immutable inputs without materializing file bodies in memory."""
    for file_record in files:
        path = Path(file_record["relative_path"])
        if not path.is_file():
            raise FileNotFoundError(f"Manifest file is missing: {path}")
        if path.stat().st_size != file_record["bytes"]:
            raise ValueError(f"Manifest byte count does not match: {path}")
        digest = sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != file_record["sha256"]:
            raise ValueError(f"Manifest SHA-256 does not match: {path}")


def _validate_targeted_api_response_sizes(files: list[dict[str, Any]]) -> None:
    for file_record in files:
        if file_record["bytes"] > MAX_TARGETED_API_RESPONSE_BYTES:
            raise ValueError(
                "Open Payments targeted API response is larger than the 32 MiB in-memory load "
                "ceiling; use a future streaming ingestion path for large or full annual sources"
            )


def _validate_manifest_identity(manifest: dict[str, Any]) -> int:
    dataset = manifest["dataset"]
    if dataset["slug"] != DATASET_SLUG:
        raise ValueError("Manifest is not for CMS Open Payments General Payment data")
    if dataset["aggregation_keys"] != list(AGGREGATION_KEYS):
        raise ValueError("Manifest aggregation keys do not match Open Payments Record_ID grain")
    if manifest["status"] != "complete":
        raise ValueError("Open Payments loading requires a complete manifest")
    if manifest["retrieval"]["parameters"].get("snapshot_scope") != "targeted-api":
        raise ValueError("This loader supports bounded targeted Open Payments API responses")
    program_year = dataset["data_year"]
    if not isinstance(program_year, int):
        raise ValueError("Open Payments manifests require an integer data_year")
    return program_year


def _manifest_exact_filters(manifest: dict[str, Any]) -> dict[str, str]:
    filters = manifest["retrieval"]["parameters"].get("exact_filters")
    if not isinstance(filters, dict) or not filters:
        raise ValueError("Open Payments targeted manifests require exact API filters")
    if any(
        not isinstance(field, str)
        or not field.strip()
        or not isinstance(value, str)
        or not value.strip()
        for field, value in filters.items()
    ):
        raise ValueError("Open Payments exact API filters must be nonblank strings")
    return filters


def _revalidate_api_response(
    file_record: dict[str, Any],
    manifest: dict[str, Any],
    *,
    program_year: int,
) -> dict[str, Any]:
    """Replay targeted-cohort validation from immutable bytes before a database write."""
    schema_fingerprint = file_record.get("schema_fingerprint")
    if not isinstance(schema_fingerprint, str) or not schema_fingerprint:
        raise ValueError("Open Payments API response requires a manifest schema fingerprint")
    profile = profile_open_payments_sql_response(
        Path(file_record["relative_path"]),
        program_year=program_year,
        exact_filters=_manifest_exact_filters(manifest),
        required_fields=(
            "total_amount_of_payment_usdollars",
            "date_of_payment",
            "applicable_manufacturer_or_applicable_gpo_making_payment_id",
            "applicable_manufacturer_or_applicable_gpo_making_payment_name",
        ),
    )
    validation = manifest["validation"]
    comparisons = {
        "rows": profile["rows"],
        "columns": profile["column_count"],
        "aggregation_key_duplicates": profile["aggregation_key_duplicates"],
    }
    for field, observed in comparisons.items():
        if validation.get(field) != observed:
            raise ValueError(
                f"Open Payments replayed {field} {observed} does not match manifest "
                f"{validation.get(field)!r}"
            )
    if profile["schema_fingerprint"] != schema_fingerprint:
        raise ValueError("Open Payments replayed schema fingerprint does not match manifest")
    return profile


def _load_api_rows(path: Path) -> list[dict[str, Any]]:
    response_bytes = path.stat().st_size
    if response_bytes > MAX_TARGETED_API_RESPONSE_BYTES:
        raise ValueError(
            "Open Payments targeted API response is larger than the 32 MiB in-memory load "
            "ceiling; use a future streaming ingestion path for large or full annual sources"
        )
    with path.open(encoding="utf-8") as stream:
        rows = json.load(stream)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Open Payments API response is not a JSON row array: {path}")
    return rows


def load_open_payments_general(
    database_url: str,
    manifest_path: Path,
    *,
    code_commit: str,
    pipeline_version: str,
    case_id: str | None = None,
) -> tuple[int, int]:
    """Load a complete bounded Open Payments General Payment API manifest."""
    manifest, manifest_hash = load_and_validate_manifest(manifest_path)
    program_year = _validate_manifest_identity(manifest)
    manifest_relative_path = _relative_manifest_path(manifest_path)
    observed_at = datetime.fromisoformat(
        manifest["observation"]["observed_at"].replace("Z", "+00:00")
    )
    files = manifest["files"]
    api_files = [file_record for file_record in files if file_record["role"] == API_RESPONSE_ROLE]
    if len(api_files) != 1:
        raise ValueError("Open Payments targeted manifests require exactly one api-response file")
    _validate_targeted_api_response_sizes(api_files)
    _validate_files(files)
    _revalidate_api_response(api_files[0], manifest, program_year=program_year)

    with psycopg.connect(database_url) as connection, connection.transaction():
        dataset_id = connection.execute(
            """
            insert into metadata.source_dataset (source, slug, name, landing_page)
            values (%s, %s, %s, %s)
            on conflict (source, slug) do update
            set name = excluded.name, landing_page = excluded.landing_page
            returning dataset_id
            """,
            (
                manifest["source"]["name"],
                manifest["dataset"]["slug"],
                manifest["dataset"]["name"],
                manifest["documentation"]["landing_page"],
            ),
        ).fetchone()[0]

        existing_release = connection.execute(
            """
            select source_release_id, manifest_sha256
            from metadata.source_release
            where manifest_path = %s
            """,
            (manifest_relative_path,),
        ).fetchone()
        if existing_release:
            source_release_id = existing_release[0]
            if existing_release[1].strip() != manifest_hash:
                raise ValueError(
                    "Committed manifest content differs from the previously loaded manifest hash"
                )
        else:
            source_release_id = connection.execute(
                """
                insert into metadata.source_release (
                    dataset_id, data_year, version_id, published_at, modified_at,
                    accessed_at, observed_at, population, aggregation_keys, suppression,
                    exclusions, status, manifest_path, manifest_sha256
                )
                values (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s,
                    %s::jsonb, 'validated', %s, %s
                )
                returning source_release_id
                """,
                (
                    dataset_id,
                    program_year,
                    manifest["dataset"]["version_id"],
                    manifest["observation"]["published_at"],
                    manifest["observation"]["modified_at"],
                    manifest["observation"]["accessed_at"],
                    observed_at,
                    manifest["dataset"]["population"],
                    json.dumps(manifest["dataset"]["aggregation_keys"]),
                    manifest["semantics"]["suppression"],
                    json.dumps(manifest["semantics"]["exclusions"]),
                    manifest_relative_path,
                    manifest_hash,
                ),
            ).fetchone()[0]

        for ordinal, file_record in enumerate(files, start=1):
            content_artifact_id = connection.execute(
                """
                insert into metadata.content_artifact (bytes, sha256, media_type)
                values (%s, %s, %s)
                on conflict (sha256) do update set sha256 = excluded.sha256
                returning content_artifact_id
                """,
                (file_record["bytes"], file_record["sha256"], file_record["media_type"]),
            ).fetchone()[0]
            connection.execute(
                """
                insert into metadata.source_release_file (
                    source_release_id, content_artifact_id, relative_path, filename,
                    role, ordinal, retrieved_at, validation
                )
                values (%s, %s, %s, %s, %s, %s, %s, '{}'::jsonb)
                on conflict (source_release_id, relative_path) do nothing
                """,
                (
                    source_release_id,
                    content_artifact_id,
                    file_record["relative_path"],
                    file_record["filename"],
                    file_record["role"],
                    ordinal,
                    observed_at,
                ),
            )

        ingestion_run_id = connection.execute(
            """
            insert into metadata.ingestion_run (
                source_release_id, case_id, pipeline_version, code_commit, status
            )
            values (%s, %s, %s, %s, 'running')
            returning ingestion_run_id
            """,
            (source_release_id, case_id, pipeline_version, code_commit),
        ).fetchone()[0]

        connection.execute(
            """
            insert into metadata.ingestion_run_file (
                ingestion_run_id, source_release_id, source_release_file_id, role, ordinal
            )
            select %s, source_release_id, source_release_file_id, role, ordinal
            from metadata.source_release_file
            where source_release_id = %s
            order by ordinal
            """,
            (ingestion_run_id, source_release_id),
        )

        connection.execute(
            "delete from relationships.open_payments_general where source_release_id = %s",
            (source_release_id,),
        )

        loaded_rows = 0
        columns = (
            "source_release_id, program_year, record_id, change_type, "
            "covered_recipient_type, covered_recipient_profile_id, covered_recipient_npi, "
            "covered_recipient_first_name, covered_recipient_middle_name, "
            "covered_recipient_last_name, covered_recipient_name_suffix, recipient_city, "
            "recipient_state, recipient_zip_code, recipient_country, recipient_primary_types, "
            "recipient_specialties, recipient_license_states, submitting_entity_name, "
            "paying_entity_id, paying_entity_name, paying_entity_state, paying_entity_country, "
            "total_amount_usd, payment_date, number_of_payments, form_of_payment, "
            "nature_of_payment, travel_city, travel_state, travel_country, "
            "physician_ownership_indicator, third_party_payment_recipient_indicator, "
            "third_party_entity_name, charity_indicator, "
            "third_party_equals_covered_recipient_indicator, contextual_information, "
            "delay_in_publication_indicator, dispute_status, related_product_indicator, "
            "product_coverage_indicators, product_types, product_categories, product_names, "
            "product_ndcs, product_pdis, product_slots, payment_publication_date, "
            "teaching_hospital_ccn, "
            "teaching_hospital_id, teaching_hospital_name, recipient_address_line1, "
            "recipient_address_line2, recipient_province, recipient_postal_code"
        )
        with connection.cursor().copy(
            f"copy relationships.open_payments_general ({columns}) from stdin"  # noqa: S608
        ) as copy:
            for file_record in api_files:
                for row in _load_api_rows(Path(file_record["relative_path"])):
                    copy.write_row(_row_values(row, source_release_id, program_year))
                    loaded_rows += 1

        expected_rows = manifest["validation"]["rows"]
        if expected_rows is not None and loaded_rows != expected_rows:
            raise ValueError(
                f"Loaded row count {loaded_rows} does not match manifest row count {expected_rows}"
            )

        connection.execute(
            """
            insert into metadata.data_quality_result (
                ingestion_run_id, check_name, status, observed, details
            )
            values
                (%s, 'manifest-file-integrity', 'pass', %s::jsonb,
                 'All retained files matched manifest byte counts and SHA-256 hashes.'),
                (%s, 'loaded-row-count', 'pass', %s::jsonb,
                 'Loaded rows matched the complete manifest validation count.'),
                (%s, 'open-payments-role-semantics', 'pass', %s::jsonb,
                 'Loaded into the relationships schema, separate from claims facts.')
            """,
            (
                ingestion_run_id,
                json.dumps({"validated_files": len(files)}),
                ingestion_run_id,
                json.dumps({"expected": expected_rows, "loaded": loaded_rows}),
                ingestion_run_id,
                json.dumps(
                    {
                        "dataset": DATASET_SLUG,
                        "amount_semantics": "reported payment or transfer of value",
                    }
                ),
            ),
        )
        connection.execute(
            """
            update metadata.ingestion_run
            set status = 'succeeded', completed_at = now(), source_rows = %s,
                loaded_rows = %s, rejected_rows = 0
            where ingestion_run_id = %s
            """,
            (loaded_rows, loaded_rows, ingestion_run_id),
        )
        connection.execute(
            "update metadata.source_release set status = 'loaded' where source_release_id = %s",
            (source_release_id,),
        )

    return source_release_id, loaded_rows
