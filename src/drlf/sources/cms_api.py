from __future__ import annotations

import json
import re
import ssl
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import truststore

CMS_DATA_API = "https://data.cms.gov/data-api/v1/dataset"
DEFAULT_PAGE_SIZE = 5_000
DEFAULT_MAX_PAGES = 1_000
DEFAULT_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_BATCHES = 50
MAX_URL_BYTES = 8_192

_CMS_API_FIELD_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")


@dataclass(frozen=True)
class HttpResponse:
    body: bytes
    content_type: str


@dataclass(frozen=True)
class PageRecord:
    ordinal: int
    offset: int
    rows: int
    url: str
    relative_filename: str
    bytes: int
    sha256: str
    media_type: str


@dataclass(frozen=True)
class ExactInFilter:
    """A closed CMS API ``IN`` condition with deterministic exact values."""

    field: str
    values: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_field_name(self.field, label="IN-filter field")
        if isinstance(self.values, str):
            raise ValueError("IN-filter values must be a tuple of strings")
        values = tuple(self.values)
        if not values:
            raise ValueError("IN-filter values cannot be empty")
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("IN-filter values must be non-empty strings")
        if len(set(values)) != len(values):
            raise ValueError("IN-filter values must be unique")
        if values != tuple(sorted(values)):
            raise ValueError("IN-filter values must be sorted")
        object.__setattr__(self, "values", values)


type CmsApiFilters = Mapping[str, str] | ExactInFilter


def _validate_field_name(field: str, *, label: str) -> None:
    if not isinstance(field, str) or _CMS_API_FIELD_PATTERN.fullmatch(field) is None:
        raise ValueError(f"{label} must be a CMS API field name")


def _normalise_sort_fields(sort_fields: Sequence[str]) -> tuple[str, ...]:
    if isinstance(sort_fields, str):
        raise ValueError("sort_fields must be a sequence of CMS API field names")
    fields = tuple(sort_fields)
    for field in fields:
        _validate_field_name(field, label="Sort field")
    if len(set(fields)) != len(fields):
        raise ValueError("sort_fields must not contain duplicates")
    return fields


def _filter_parameters(filters: CmsApiFilters) -> list[tuple[str, str]]:
    if isinstance(filters, ExactInFilter):
        return [
            ("filter[condition][path]", filters.field),
            ("filter[condition][operator]", "IN"),
            *(("filter[condition][value][]", value) for value in filters.values),
        ]
    for field in filters:
        _validate_field_name(field, label="Equality-filter field")
    return [(f"filter[{key}]", value) for key, value in sorted(filters.items())]


def _checked_url(path: str, parameters: list[tuple[str, str | int]]) -> str:
    query = urlencode(parameters)
    suffix = f"?{query}" if query else ""
    url = f"{CMS_DATA_API}/{path}{suffix}"
    url_bytes = len(url.encode("utf-8"))
    if url_bytes > MAX_URL_BYTES:
        raise ValueError(
            f"CMS API URL is {url_bytes} bytes; the safety limit is {MAX_URL_BYTES} bytes"
        )
    return url


def _inventory_filters(filters: CmsApiFilters) -> dict[str, Any]:
    if isinstance(filters, ExactInFilter):
        return {
            "condition": {
                "path": filters.field,
                "operator": "IN",
                "values": list(filters.values),
            }
        }
    return dict(sorted(filters.items()))


def build_data_url(
    dataset_uuid: str,
    filters: CmsApiFilters,
    *,
    offset: int,
    page_size: int,
    sort_fields: Sequence[str] = (),
) -> str:
    parameters: list[tuple[str, str | int]] = [("size", page_size), ("offset", offset)]
    normalised_sort_fields = _normalise_sort_fields(sort_fields)
    if normalised_sort_fields:
        parameters.append(("sort", ",".join(normalised_sort_fields)))
    parameters.extend(_filter_parameters(filters))
    return _checked_url(f"{dataset_uuid}/data", parameters)


def build_stats_url(dataset_uuid: str, filters: CmsApiFilters) -> str:
    return _checked_url(f"{dataset_uuid}/data/stats", _filter_parameters(filters))


def fetch_with_retries(
    url: str,
    *,
    attempts: int = 3,
    timeout: int = 45,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> HttpResponse:
    if max_bytes < 1:
        raise ValueError("max_bytes must be at least 1")
    request = Request(url, headers={"User-Agent": "drlf/0.1"})
    tls_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(  # noqa: S310 - fixed HTTPS source
                request,
                timeout=timeout,
                context=tls_context,
            ) as response:
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise RuntimeError(
                        f"CMS API response exceeded the {max_bytes}-byte safety limit: {url}"
                    )
                return HttpResponse(
                    body=body,
                    content_type=response.headers.get_content_type(),
                )
        except (HTTPError, URLError, TimeoutError) as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise RuntimeError(f"CMS API request failed after {attempts} attempts: {url}") from last_error


def fetch_filtered_stats(
    dataset_uuid: str,
    filters: CmsApiFilters,
    *,
    fetcher: Callable[[str], HttpResponse] | None = None,
) -> dict[str, int]:
    """Return CMS filtered and full row counts before a targeted extraction."""
    url = build_stats_url(dataset_uuid, filters)
    response = (fetcher or fetch_with_retries)(url)
    try:
        decoded = json.loads(response.body)
    except json.JSONDecodeError as error:
        raise ValueError("CMS API stats response was not JSON") from error
    if not isinstance(decoded, dict):
        raise ValueError("CMS API stats response was not an object")

    values = decoded.get("data", decoded)
    if not isinstance(values, dict):
        raise ValueError("CMS API stats response did not contain a count object")
    try:
        found_rows = int(values["found_rows"])
        total_rows = int(values["total_rows"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("CMS API stats response did not contain valid row counts") from error
    if found_rows < 0 or total_rows < 0 or found_rows > total_rows:
        raise ValueError("CMS API stats response contained inconsistent row counts")
    return {"found_rows": found_rows, "total_rows": total_rows}


def download_filtered_pages(
    dataset_uuid: str,
    filters: CmsApiFilters,
    output_dir: Path,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    expected_rows: int | None = None,
    observed_at: datetime | None = None,
    sort_fields: Sequence[str] = (),
    fetcher: Callable[[str], HttpResponse] | None = None,
) -> dict[str, Any]:
    """Save untouched CMS API pages and return a reproducible page inventory."""
    if page_size < 1 or page_size > DEFAULT_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {DEFAULT_PAGE_SIZE}")
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    if max_response_bytes < 1 or max_total_bytes < 1:
        raise ValueError("Byte safety limits must be positive")
    if max_response_bytes > max_total_bytes:
        raise ValueError("max_response_bytes cannot exceed max_total_bytes")
    if expected_rows is not None and expected_rows < 0:
        raise ValueError("expected_rows cannot be negative")
    if not filters:
        raise ValueError("At least one API filter is required for targeted extraction")
    normalised_sort_fields = _normalise_sort_fields(sort_fields)
    if isinstance(filters, ExactInFilter) and not normalised_sort_fields:
        raise ValueError("Exact IN-filter downloads require stable sort_fields")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"Refusing to replace existing snapshot: {output_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    observation = observed_at or datetime.now(UTC)
    pages: list[PageRecord] = []
    seen_page_hashes: dict[str, int] = {}
    offset = 0

    while True:
        if len(pages) >= max_pages:
            raise RuntimeError(
                f"CMS API extraction reached the {max_pages}-page safety limit; "
                "use a bulk download or explicitly raise the limit after reviewing the filter"
            )
        url = build_data_url(
            dataset_uuid,
            filters,
            offset=offset,
            page_size=page_size,
            sort_fields=normalised_sort_fields,
        )
        response = (
            fetcher(url)
            if fetcher is not None
            else fetch_with_retries(url, max_bytes=max_response_bytes)
        )
        if len(response.body) > max_response_bytes:
            raise RuntimeError(
                f"CMS API response exceeded the {max_response_bytes}-byte per-page safety limit"
            )
        cumulative_bytes = sum(page.bytes for page in pages) + len(response.body)
        if cumulative_bytes > max_total_bytes:
            raise RuntimeError(
                f"CMS API extraction exceeded the {max_total_bytes}-byte cumulative safety limit"
            )
        try:
            decoded = json.loads(response.body)
        except json.JSONDecodeError as error:
            raise ValueError(f"CMS API did not return JSON at offset {offset}") from error
        if not isinstance(decoded, list):
            raise ValueError(f"CMS API response at offset {offset} was not a row array")

        page_hash = sha256(response.body).hexdigest()
        if len(decoded) == page_size and page_hash in seen_page_hashes:
            first_offset = seen_page_hashes[page_hash]
            raise RuntimeError(
                "CMS API returned an identical full page at different offsets "
                f"({first_offset} and {offset}); stopping to prevent a pagination loop"
            )
        seen_page_hashes[page_hash] = offset

        filename = f"page-{len(pages) + 1:05d}-offset-{offset:09d}.json"
        final_path = output_dir / filename
        temporary_path = output_dir / f".{filename}.part"
        temporary_path.write_bytes(response.body)
        temporary_path.replace(final_path)
        pages.append(
            PageRecord(
                ordinal=len(pages) + 1,
                offset=offset,
                rows=len(decoded),
                url=url,
                relative_filename=filename,
                bytes=len(response.body),
                sha256=page_hash,
                media_type=response.content_type,
            )
        )

        retained_rows = sum(page.rows for page in pages)
        if expected_rows is not None and retained_rows > expected_rows:
            raise RuntimeError(
                "CMS API returned more rows than the filtered stats preflight reported"
            )
        if expected_rows is not None and retained_rows == expected_rows:
            break
        if len(decoded) < page_size:
            if expected_rows is not None:
                raise RuntimeError(
                    "CMS API returned fewer rows than the filtered stats preflight reported"
                )
            break
        offset += page_size

    inventory = {
        "inventory_version": 1,
        "dataset_uuid": dataset_uuid,
        "observed_at": observation.isoformat().replace("+00:00", "Z"),
        "filters": _inventory_filters(filters),
        "page_size": page_size,
        "max_pages": max_pages,
        "max_response_bytes": max_response_bytes,
        "max_total_bytes": max_total_bytes,
        "expected_rows": expected_rows,
        "total_bytes": sum(page.bytes for page in pages),
        "total_rows": sum(page.rows for page in pages),
        "pages": [asdict(page) for page in pages],
    }
    if normalised_sort_fields:
        inventory["sort_fields"] = list(normalised_sort_fields)
    inventory_bytes = (json.dumps(inventory, indent=2) + "\n").encode()
    (output_dir / "inventory.json").write_bytes(inventory_bytes)
    return inventory


def download_exact_in_batches(
    dataset_uuid: str,
    field: str,
    values: Sequence[str],
    output_dir: Path,
    *,
    sort_fields: Sequence[str],
    batch_size: int = 1,
    page_size: int = 1_000,
    max_pages_per_batch: int = 5,
    max_rows_per_batch: int = 5_000,
    max_bundle_rows: int = 25_000,
    max_response_bytes: int = 4 * 1024 * 1024,
    max_bundle_bytes: int = 16 * 1024 * 1024,
    max_batches: int = DEFAULT_MAX_BATCHES,
    observed_at: datetime | None = None,
    fetcher: Callable[[str], HttpResponse] | None = None,
) -> dict[str, Any]:
    """Download a disjoint exact-IN union after every batch passes preflight.

    The full-union count and all batch counts are fetched before the first row is
    retained. Values are partitioned deterministically, and their disjoint counts
    must reproduce the full-union count. Each nested request retains its ordinary
    inventory; ``bundle-inventory.json`` records the cross-request safety envelope.
    """
    if isinstance(values, str):
        raise ValueError("IN-filter values must be a sequence of strings")
    normalized_values = tuple(values)
    if not normalized_values:
        raise ValueError("IN-filter values cannot be empty")
    if len(normalized_values) != len(set(normalized_values)):
        raise ValueError("IN-filter values must be unique")
    if any(not isinstance(value, str) or not value.strip() for value in normalized_values):
        raise ValueError("IN-filter values must be non-empty strings")
    normalized_values = tuple(sorted(normalized_values))
    normalized_sort_fields = _normalise_sort_fields(sort_fields)
    if not normalized_sort_fields:
        raise ValueError("Batched exact-IN downloads require stable sort_fields")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if batch_size > DEFAULT_MAX_BATCHES:
        raise ValueError(f"batch_size must be at most {DEFAULT_MAX_BATCHES}")
    if max_batches < 1 or max_batches > DEFAULT_MAX_BATCHES:
        raise ValueError(f"max_batches must be between 1 and {DEFAULT_MAX_BATCHES}")
    if page_size < 1 or page_size > 1_000:
        raise ValueError("page_size must be between 1 and 1000")
    if max_pages_per_batch < 1 or max_pages_per_batch > 5:
        raise ValueError("max_pages_per_batch must be between 1 and 5")
    if max_rows_per_batch < 1 or max_rows_per_batch > 5_000:
        raise ValueError("max_rows_per_batch must be between 1 and 5000")
    if max_bundle_rows < 1 or max_bundle_rows > 25_000:
        raise ValueError("max_bundle_rows must be between 1 and 25000")
    if max_response_bytes < 1 or max_response_bytes > 4 * 1024 * 1024:
        raise ValueError("max_response_bytes must be between 1 and 4194304")
    if max_bundle_bytes < 1 or max_bundle_bytes > 16 * 1024 * 1024:
        raise ValueError("max_bundle_bytes must be between 1 and 16777216")
    if max_response_bytes > max_bundle_bytes:
        raise ValueError("max_response_bytes cannot exceed max_bundle_bytes")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"Refusing to replace existing snapshot: {output_dir}")

    batches = tuple(
        normalized_values[offset : offset + batch_size]
        for offset in range(0, len(normalized_values), batch_size)
    )
    if len(batches) > max_batches:
        raise ValueError(
            f"Exact-IN partition requires {len(batches)} batches, above the "
            f"{max_batches}-batch safety limit"
        )

    union_filter = ExactInFilter(field, normalized_values)
    union_stats = fetch_filtered_stats(dataset_uuid, union_filter, fetcher=fetcher)
    preflights: list[dict[str, Any]] = []
    preflight_rows = 0
    for ordinal, batch_values in enumerate(batches, start=1):
        batch_filter = ExactInFilter(field, batch_values)
        stats = fetch_filtered_stats(dataset_uuid, batch_filter, fetcher=fetcher)
        if stats["total_rows"] != union_stats["total_rows"]:
            raise RuntimeError("CMS API batch stats disagree on the full dataset row count")
        if stats["found_rows"] > max_rows_per_batch:
            raise ValueError(
                f"Batch {ordinal} stats report {stats['found_rows']} rows, above the "
                f"{max_rows_per_batch}-row per-batch safety limit"
            )
        required_pages = max(1, (stats["found_rows"] + page_size - 1) // page_size)
        if required_pages > max_pages_per_batch:
            raise ValueError(
                f"Batch {ordinal} requires {required_pages} pages, above the "
                f"{max_pages_per_batch}-page per-batch safety limit"
            )
        preflight_rows += stats["found_rows"]
        preflights.append(
            {
                "ordinal": ordinal,
                "values": list(batch_values),
                "expected_rows": stats["found_rows"],
                "required_pages": required_pages,
            }
        )
    if preflight_rows != union_stats["found_rows"]:
        raise RuntimeError(
            "Disjoint exact-IN batch counts do not reproduce the full-union stats count"
        )
    if preflight_rows > max_bundle_rows:
        raise ValueError(
            f"Full exact-IN union reports {preflight_rows} rows, above the "
            f"{max_bundle_rows}-row bundle safety limit"
        )

    observation = observed_at or datetime.now(UTC)
    inventories: list[dict[str, Any]] = []
    retained_bytes = 0
    for preflight, batch_values in zip(preflights, batches, strict=True):
        remaining_bundle_bytes = max_bundle_bytes - retained_bytes
        if remaining_bundle_bytes < 1:
            raise RuntimeError(
                f"Exact-IN bundle has no remaining byte budget before batch {preflight['ordinal']}"
            )
        effective_response_bytes = min(max_response_bytes, remaining_bundle_bytes)
        batch_dir = output_dir / f"batch-{preflight['ordinal']:05d}"
        inventory = download_filtered_pages(
            dataset_uuid,
            ExactInFilter(field, batch_values),
            batch_dir,
            page_size=page_size,
            max_pages=max_pages_per_batch,
            max_response_bytes=effective_response_bytes,
            max_total_bytes=remaining_bundle_bytes,
            expected_rows=preflight["expected_rows"],
            observed_at=observation,
            sort_fields=normalized_sort_fields,
            fetcher=fetcher,
        )
        retained_bytes += inventory["total_bytes"]
        if retained_bytes > max_bundle_bytes:
            raise RuntimeError(
                f"Exact-IN bundle exceeded the {max_bundle_bytes}-byte cumulative safety limit"
            )
        inventories.append(
            {
                **preflight,
                "inventory_path": f"batch-{preflight['ordinal']:05d}/inventory.json",
                "retained_rows": inventory["total_rows"],
                "retained_bytes": inventory["total_bytes"],
            }
        )

    descriptor = {
        "bundle_inventory_version": 1,
        "dataset_uuid": dataset_uuid,
        "observed_at": observation.isoformat().replace("+00:00", "Z"),
        "filter": _inventory_filters(union_filter),
        "sort_fields": list(normalized_sort_fields),
        "batch_size": batch_size,
        "page_size": page_size,
        "max_batches": max_batches,
        "max_pages_per_batch": max_pages_per_batch,
        "max_rows_per_batch": max_rows_per_batch,
        "max_bundle_rows": max_bundle_rows,
        "max_response_bytes": max_response_bytes,
        "max_bundle_bytes": max_bundle_bytes,
        "full_union_stats": union_stats,
        "total_rows": preflight_rows,
        "total_bytes": retained_bytes,
        "inventories": inventories,
    }
    descriptor_bytes = (json.dumps(descriptor, indent=2) + "\n").encode()
    (output_dir / "bundle-inventory.json").write_bytes(descriptor_bytes)
    return descriptor


def parse_filter_arguments(values: list[str]) -> dict[str, str]:
    filters: dict[str, str] = {}
    for value in values:
        key, separator, filter_value = value.partition("=")
        if not separator or not key or not filter_value:
            raise ValueError(f"Filter must use non-empty FIELD=VALUE syntax: {value}")
        if key in filters:
            raise ValueError(f"Duplicate filter field: {key}")
        filters[key] = filter_value
    return filters
