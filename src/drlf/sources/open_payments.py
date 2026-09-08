from __future__ import annotations

import json
import os
import re
import ssl
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

import truststore
from jsonschema import Draft202012Validator, FormatChecker

OPEN_PAYMENTS_HOMEPAGE = "https://openpaymentsdata.cms.gov/"
OPEN_PAYMENTS_DATA_OVERVIEW = "https://www.cms.gov/priorities/key-initiatives/open-payments/data"
OPEN_PAYMENTS_DATA_EXPLORER = (
    "https://www.cms.gov/priorities/key-initiatives/open-payments/data/explore"
)
OPEN_PAYMENTS_METHODOLOGY = (
    "https://www.cms.gov/openpayments/downloads/openpaymentsdatadictionary.pdf"
)
OPEN_PAYMENTS_SQL_API = "https://openpaymentsdata.cms.gov/api/1/datastore/sql"

DEFAULT_CHUNK_BYTES = 1024 * 1024
MAX_TARGETED_SQL_RESPONSE_BYTES = 32 * 1024 * 1024

SnapshotScope = Literal["annual-archive", "targeted-api"]
ManifestStatus = Literal["draft", "complete"]
PaymentCategory = Literal["general", "research", "ownership"]

_RESOURCE_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]*$", re.IGNORECASE)


class DownloadLimitExceeded(RuntimeError):
    """Raised before a retained download would exceed its explicit byte ceiling."""


@dataclass(frozen=True)
class OpenPaymentsPreflight:
    request_url: str
    final_url: str
    total_bytes: int | None
    media_type: str | None
    etag: str | None
    last_modified: str | None
    size_source: Literal["head", "content-range", "unknown"]


@dataclass(frozen=True)
class OpenPaymentsDownload:
    path: Path
    request_url: str
    final_url: str
    bytes: int
    sha256: str
    media_type: str
    compression: str | None
    resumed_from: int
    etag: str | None
    last_modified: str | None


@dataclass(frozen=True)
class OpenPaymentsFile:
    path: Path
    role: str
    media_type: str | None = None
    compression: str | None = None
    schema_fingerprint: str | None = None

    @classmethod
    def from_download(
        cls,
        download: OpenPaymentsDownload,
        *,
        role: str,
    ) -> OpenPaymentsFile:
        return cls(
            path=download.path,
            role=role,
            media_type=download.media_type,
            compression=download.compression,
        )


@dataclass(frozen=True)
class OpenPaymentsManifestSpec:
    dataset_name: str
    dataset_slug: str
    program_year: int
    snapshot_scope: SnapshotScope
    request_url: str
    observed_at: datetime
    files: Sequence[OpenPaymentsFile]
    dataset_id: str | None = None
    version_id: str | None = None
    population: str = ""
    aggregation_keys: Sequence[str] = ()
    published_at: date | None = None
    modified_at: date | datetime | None = None
    landing_page: str = OPEN_PAYMENTS_DATA_EXPLORER
    methodology: str = OPEN_PAYMENTS_METHODOLOGY
    data_dictionary: str = OPEN_PAYMENTS_METHODOLOGY
    documentation_snapshots: Sequence[str] = ()
    license_name: str | None = None
    license_url: str | None = None
    access_restrictions: str = "Not yet verified for this observed release"
    public_use_verified: bool = False
    retrieval_parameters: Mapping[str, Any] | None = None
    query: str | None = None
    monetary_fields: Mapping[str, str] | None = None
    utilization_fields: Mapping[str, str] | None = None
    suppression: str = "Not yet verified for this observed release"
    exclusions: Sequence[str] = ()
    validation_rows: int | None = None
    validation_columns: int | None = None
    aggregation_key_duplicates: int | None = None
    validation_notes: Sequence[str] = ()
    status: ManifestStatus = "draft"


def build_open_payments_sql_query(
    resource_id: str,
    exact_filters: Mapping[str, str],
) -> str:
    """Build a deterministic, exact-filter Open Payments SQL API query."""
    if not _RESOURCE_ID.fullmatch(resource_id):
        raise ValueError("Open Payments datastore resource ID must be a UUID")
    if not exact_filters:
        raise ValueError("At least one exact Open Payments SQL filter is required")

    clauses: list[str] = []
    for field, value in sorted(exact_filters.items()):
        if not _FIELD_NAME.fullmatch(field):
            raise ValueError(f"Unsafe Open Payments filter field: {field}")
        if not value or any(character in value for character in {'"', "\\", "\r", "\n"}):
            raise ValueError(f"Unsafe Open Payments filter value for {field}")
        keyword = "WHERE" if not clauses else "AND"
        clauses.append(f'[{keyword} {field} = "{value}"]')
    return f"[SELECT * FROM {resource_id}]" + "".join(clauses)


def build_open_payments_sql_url(query: str) -> str:
    if not query.strip():
        raise ValueError("Open Payments SQL query cannot be blank")
    return f"{OPEN_PAYMENTS_SQL_API}?{urlencode({'query': query})}"


def _validate_cms_url(url: str) -> None:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or not (hostname == "cms.gov" or hostname.endswith(".cms.gov")):
        raise ValueError(f"Open Payments acquisition requires an HTTPS CMS URL: {url}")


def _repository_relative_raw_path(path: Path) -> str:
    try:
        relative_path = path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"Snapshot file is outside the repository: {path}") from error
    if not relative_path.startswith("data/raw/"):
        raise ValueError(f"Snapshot file must be retained under data/raw/: {path}")
    return relative_path


def _open_request(request: Request, timeout: int) -> Any:
    tls_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return urlopen(  # noqa: S310 - URL is restricted to an HTTPS CMS host
        request,
        timeout=timeout,
        context=tls_context,
    )


def _response_header(response: Any, name: str) -> str | None:
    value = response.headers.get(name)
    return str(value) if value is not None else None


def _response_media_type(response: Any, path: Path) -> str:
    headers = response.headers
    if hasattr(headers, "get_content_type"):
        media_type = str(headers.get_content_type())
    else:
        media_type = (_response_header(response, "Content-Type") or "").split(";", 1)[0]
    media_type = media_type.strip()
    if media_type:
        return media_type
    return _infer_file_metadata(path)[0]


def _response_status(response: Any) -> int:
    status = getattr(response, "status", None)
    if status is None and hasattr(response, "getcode"):
        status = response.getcode()
    if not isinstance(status, int):
        raise RuntimeError("Open Payments response did not expose an HTTP status")
    return status


def _parse_content_length(response: Any) -> int | None:
    value = _response_header(response, "Content-Length")
    if value is None:
        return None
    try:
        length = int(value)
    except ValueError as error:
        raise RuntimeError(f"Invalid Content-Length response header: {value}") from error
    if length < 0:
        raise RuntimeError(f"Invalid negative Content-Length response header: {value}")
    return length


def _parse_content_range(response: Any) -> tuple[int, int, int | None]:
    value = _response_header(response, "Content-Range") or ""
    match = re.fullmatch(r"bytes ([0-9]+)-([0-9]+)/(?:([0-9]+)|\*)", value)
    if not match:
        raise RuntimeError(f"Invalid or missing Content-Range for resumed response: {value!r}")
    total = int(match.group(3)) if match.group(3) is not None else None
    return int(match.group(1)), int(match.group(2)), total


def _partial_paths(output_path: Path) -> tuple[Path, Path]:
    partial_path = output_path.with_name(f".{output_path.name}.part")
    state_path = output_path.with_name(f".{output_path.name}.part.json")
    return partial_path, state_path


def _load_partial_state(state_path: Path) -> dict[str, Any] | None:
    if not state_path.exists():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot safely resume invalid partial state: {state_path}") from error
    if not isinstance(state, dict):
        raise RuntimeError(f"Cannot safely resume non-object partial state: {state_path}")
    return state


def _write_partial_state(state_path: Path, state: Mapping[str, Any]) -> None:
    temporary_path = state_path.with_name(f".{state_path.name}.new")
    temporary_path.write_text(json.dumps(dict(state), indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(state_path)


def _hash_existing_file(path: Path, chunk_bytes: int) -> Any:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_bytes):
            digest.update(chunk)
    return digest


def _infer_file_metadata(path: Path) -> tuple[str, str | None]:
    suffix = path.suffix.casefold()
    if suffix == ".zip":
        return "application/zip", "zip"
    if suffix in {".gz", ".gzip"}:
        return "application/gzip", "gzip"
    if suffix == ".json":
        return "application/json", None
    if suffix == ".csv":
        return "text/csv", None
    return "application/octet-stream", None


def preflight_open_payments_file(
    request_url: str,
    *,
    timeout: int = 90,
    opener: Callable[[Request, int], Any] = _open_request,
) -> OpenPaymentsPreflight:
    """Probe an official CMS file without consuming its body to discover total size.

    Some Open Payments bulk endpoints omit ``Content-Length`` on a HEAD response. In that case a
    one-byte Range request obtains the total from ``Content-Range``. If the server supports neither
    mechanism, the result remains ``None`` and the streaming byte ceiling is still enforced.
    """
    _validate_cms_url(request_url)
    if timeout < 1:
        raise ValueError("timeout must be at least 1 second")

    observed_final_url = request_url
    observed_media_type: str | None = None
    observed_etag: str | None = None
    observed_modified: str | None = None
    head_request = Request(
        request_url,
        method="HEAD",
        headers={"User-Agent": "drlf/0.1"},
    )
    try:
        with opener(head_request, timeout) as response:
            status = _response_status(response)
            if status not in {200, 204}:
                raise RuntimeError(
                    f"Unexpected HTTP {status} from Open Payments HEAD request: {request_url}"
                )
            observed_final_url = response.geturl() if hasattr(response, "geturl") else request_url
            _validate_cms_url(observed_final_url)
            observed_media_type = _response_media_type(response, Path(urlsplit(request_url).path))
            observed_etag = _response_header(response, "ETag")
            observed_modified = _response_header(response, "Last-Modified")
            content_length = _parse_content_length(response)
            if content_length is not None:
                return OpenPaymentsPreflight(
                    request_url=request_url,
                    final_url=observed_final_url,
                    total_bytes=content_length,
                    media_type=observed_media_type,
                    etag=observed_etag,
                    last_modified=observed_modified,
                    size_source="head",
                )
    except HTTPError as error:
        if error.code not in {403, 405, 501}:
            raise RuntimeError(
                f"Open Payments HEAD preflight failed with HTTP {error.code}: {request_url}"
            ) from error

    range_request = Request(
        request_url,
        headers={
            "User-Agent": "drlf/0.1",
            "Range": "bytes=0-0",
        },
    )
    with opener(range_request, timeout) as response:
        status = _response_status(response)
        final_url = response.geturl() if hasattr(response, "geturl") else observed_final_url
        _validate_cms_url(final_url)
        media_type = _response_media_type(response, Path(urlsplit(request_url).path))
        etag = _response_header(response, "ETag") or observed_etag
        last_modified = _response_header(response, "Last-Modified") or observed_modified
        if status == 206:
            start, end, total_bytes = _parse_content_range(response)
            if (start, end) != (0, 0):
                raise RuntimeError(
                    "Open Payments byte-range preflight returned an unexpected range: "
                    f"{start}-{end}"
                )
            return OpenPaymentsPreflight(
                request_url=request_url,
                final_url=final_url,
                total_bytes=total_bytes,
                media_type=media_type,
                etag=etag,
                last_modified=last_modified,
                size_source="content-range" if total_bytes is not None else "unknown",
            )
        if status == 200:
            return OpenPaymentsPreflight(
                request_url=request_url,
                final_url=final_url,
                total_bytes=_parse_content_length(response),
                media_type=media_type,
                etag=etag,
                last_modified=last_modified,
                size_source=("head" if _parse_content_length(response) is not None else "unknown"),
            )
        raise RuntimeError(
            f"Unexpected HTTP {status} from Open Payments range preflight: {request_url}"
        )


def download_open_payments_file(
    request_url: str,
    output_path: Path,
    *,
    max_bytes: int,
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
    timeout: int = 90,
    resume: bool = True,
    opener: Callable[[Request, int], Any] = _open_request,
    preflight_result: OpenPaymentsPreflight | None = None,
) -> OpenPaymentsDownload:
    """Stream one public CMS artifact to immutable raw storage under an explicit size cap.

    An interrupted transfer leaves a hidden partial file. Resumption uses ``Range`` and ``If-Range``
    only when the earlier response supplied an ETag or Last-Modified validator. Otherwise the next
    attempt restarts the incomplete transfer rather than risk joining two upstream versions.
    """
    _validate_cms_url(request_url)
    _repository_relative_raw_path(output_path)
    if max_bytes < 1:
        raise ValueError("max_bytes must be at least 1")
    if chunk_bytes < 1:
        raise ValueError("chunk_bytes must be at least 1")
    if timeout < 1:
        raise ValueError("timeout must be at least 1 second")
    if output_path.exists():
        raise FileExistsError(f"Refusing to replace retained snapshot: {output_path}")

    preflight = preflight_result or preflight_open_payments_file(
        request_url,
        timeout=timeout,
        opener=opener,
    )
    if preflight.request_url != request_url:
        raise ValueError("Preflight result belongs to a different Open Payments URL")
    if preflight.total_bytes is not None and preflight.total_bytes > max_bytes:
        raise DownloadLimitExceeded(
            "Open Payments preflight found "
            f"{preflight.total_bytes} bytes, above the {max_bytes}-byte ceiling; "
            "the file body was not transferred"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path, state_path = _partial_paths(output_path)
    partial_size = partial_path.stat().st_size if partial_path.exists() else 0
    if partial_size > max_bytes:
        raise DownloadLimitExceeded(
            f"Existing partial file is {partial_size} bytes, above the {max_bytes}-byte ceiling"
        )
    if partial_size and not resume:
        raise FileExistsError(f"Incomplete transfer already exists: {partial_path}")

    state = _load_partial_state(state_path) if partial_size else None
    if state and state.get("request_url") != request_url:
        raise RuntimeError("Partial transfer URL differs from the requested Open Payments URL")

    validator = None
    if state:
        validator = state.get("etag") or state.get("last_modified")
        current_validator = preflight.etag or preflight.last_modified
        if current_validator and current_validator != validator:
            validator = None
    requested_resume = partial_size if partial_size and validator else 0
    headers = {"User-Agent": "drlf/0.1"}
    if requested_resume:
        headers["Range"] = f"bytes={requested_resume}-"
        headers["If-Range"] = str(validator)

    request = Request(request_url, headers=headers)
    with opener(request, timeout) as response:
        status = _response_status(response)
        final_url = response.geturl() if hasattr(response, "geturl") else request_url
        _validate_cms_url(final_url)
        expected_final_bytes = preflight.total_bytes
        expected_response_bytes: int | None = None

        if requested_resume and status == 206:
            content_range_start, content_range_end, content_range_total = _parse_content_range(
                response
            )
            if content_range_start != requested_resume:
                raise RuntimeError(
                    "Resumed response began at the wrong byte: "
                    f"expected {requested_resume}, received {content_range_start}"
                )
            if content_range_end < content_range_start:
                raise RuntimeError("Resumed response ended before its Content-Range start")
            expected_response_bytes = content_range_end - content_range_start + 1
            if (
                expected_final_bytes is not None
                and content_range_total is not None
                and expected_final_bytes != content_range_total
            ):
                raise RuntimeError(
                    "Resumed response total differs from the Open Payments preflight total"
                )
            expected_final_bytes = content_range_total or expected_final_bytes
            response_etag = _response_header(response, "ETag")
            response_modified = _response_header(response, "Last-Modified")
            if state and response_etag and state.get("etag") not in {None, response_etag}:
                raise RuntimeError("Resumed response ETag differs from the partial transfer")
            if (
                state
                and response_modified
                and state.get("last_modified") not in {None, response_modified}
            ):
                raise RuntimeError(
                    "Resumed response Last-Modified differs from the partial transfer"
                )
            write_mode = "ab"
            retained_prefix = requested_resume
        elif status == 200:
            # The server either supplied a new full response or no safe validator was available.
            # Restarting the incomplete .part is safe; the immutable final path remains untouched.
            write_mode = "wb"
            retained_prefix = 0
        else:
            raise RuntimeError(
                f"Unexpected HTTP {status} while downloading Open Payments source: {request_url}"
            )

        content_length = _parse_content_length(response)
        if (
            expected_response_bytes is not None
            and content_length is not None
            and expected_response_bytes != content_length
        ):
            raise RuntimeError(
                "Open Payments Content-Length differs from the promised Content-Range length"
            )
        if expected_response_bytes is None:
            expected_response_bytes = content_length
        if content_length is not None and retained_prefix + content_length > max_bytes:
            raise DownloadLimitExceeded(
                "Open Payments response would reach "
                f"{retained_prefix + content_length} bytes, above the {max_bytes}-byte ceiling"
            )

        media_type = _response_media_type(response, output_path) or preflight.media_type
        inferred_compression = _infer_file_metadata(output_path)[1]
        etag = _response_header(response, "ETag") or preflight.etag
        last_modified = _response_header(response, "Last-Modified") or preflight.last_modified
        _write_partial_state(
            state_path,
            {
                "state_version": 1,
                "request_url": request_url,
                "final_url": final_url,
                "etag": etag,
                "last_modified": last_modified,
            },
        )

        digest = _hash_existing_file(partial_path, chunk_bytes) if retained_prefix else sha256()
        bytes_written = retained_prefix
        with partial_path.open(write_mode) as stream:
            while chunk := response.read(chunk_bytes):
                next_size = bytes_written + len(chunk)
                if next_size > max_bytes:
                    raise DownloadLimitExceeded(
                        "Open Payments transfer crossed the explicit "
                        f"{max_bytes}-byte ceiling; partial data remains resumable"
                    )
                stream.write(chunk)
                digest.update(chunk)
                bytes_written = next_size
            stream.flush()
            os.fsync(stream.fileno())

        received_response_bytes = bytes_written - retained_prefix
        if (
            expected_response_bytes is not None
            and received_response_bytes != expected_response_bytes
        ):
            raise RuntimeError(
                "Open Payments transfer ended before the promised response length; "
                "the partial file remains resumable"
            )
        if expected_final_bytes is not None and bytes_written != expected_final_bytes:
            raise RuntimeError(
                "Open Payments transfer size does not match the preflight or Content-Range total; "
                "the partial file remains resumable"
            )

    partial_path.replace(output_path)
    state_path.unlink(missing_ok=True)
    return OpenPaymentsDownload(
        path=output_path,
        request_url=request_url,
        final_url=final_url,
        bytes=bytes_written,
        sha256=digest.hexdigest(),
        media_type=media_type,
        compression=inferred_compression,
        resumed_from=retained_prefix,
        etag=etag,
        last_modified=last_modified,
    )


def _iso_observed_at(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _iso_date_or_datetime(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("modified_at datetime must be timezone-aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return value.isoformat()


def _manifest_file_record(file: OpenPaymentsFile) -> dict[str, Any]:
    if not file.role.strip():
        raise ValueError("Every Open Payments file needs a non-empty role")
    if not file.path.is_file():
        raise FileNotFoundError(f"Retained Open Payments file does not exist: {file.path}")
    body_digest = _hash_existing_file(file.path, DEFAULT_CHUNK_BYTES)
    inferred_media_type, inferred_compression = _infer_file_metadata(file.path)
    return {
        "relative_path": _repository_relative_raw_path(file.path),
        "filename": file.path.name,
        "role": file.role,
        "bytes": file.path.stat().st_size,
        "sha256": body_digest.hexdigest(),
        "media_type": file.media_type or inferred_media_type,
        "compression": (file.compression if file.compression is not None else inferred_compression),
        "schema_fingerprint": file.schema_fingerprint,
    }


def _validate_complete_manifest_spec(spec: OpenPaymentsManifestSpec) -> None:
    if not spec.public_use_verified:
        raise ValueError("A complete manifest requires public-use access verification")
    if not spec.population.strip():
        raise ValueError("A complete manifest requires a verified population description")
    if not spec.access_restrictions.strip():
        raise ValueError("A complete manifest requires verified access restrictions")
    if not spec.suppression.strip() or spec.suppression.startswith("Not yet verified"):
        raise ValueError("A complete manifest requires verified suppression or threshold semantics")
    if spec.validation_rows is None or spec.validation_columns is None:
        raise ValueError("A complete manifest requires row and column validation")
    if spec.aggregation_key_duplicates is None:
        raise ValueError("A complete manifest requires duplicate-key validation")
    if not isinstance(spec.dataset_id, str) or not spec.dataset_id.strip():
        raise ValueError("A complete manifest requires a verified dataset identifier")
    if not isinstance(spec.version_id, str) or not spec.version_id.strip():
        raise ValueError("A complete manifest requires a verified release or resource identifier")
    if not spec.aggregation_keys or any(
        not isinstance(key, str) or not key.strip() for key in spec.aggregation_keys
    ):
        raise ValueError("A complete manifest requires verified aggregation keys")
    if len(set(spec.aggregation_keys)) != len(spec.aggregation_keys):
        raise ValueError("A complete manifest requires unique aggregation keys")
    if not isinstance(spec.monetary_fields, Mapping) or not spec.monetary_fields or any(
        not isinstance(field, str)
        or not field.strip()
        or not isinstance(meaning, str)
        or not meaning.strip()
        for field, meaning in spec.monetary_fields.items()
    ):
        raise ValueError("A complete Open Payments manifest requires monetary-field semantics")
    if spec.utilization_fields and (
        not isinstance(spec.utilization_fields, Mapping)
        or any(
            not isinstance(field, str)
            or not field.strip()
            or not isinstance(meaning, str)
            or not meaning.strip()
        for field, meaning in spec.utilization_fields.items()
        )
    ):
        raise ValueError("Open Payments utilization-field semantics cannot be blank")
    if spec.snapshot_scope == "targeted-api":
        exact_filters = (spec.retrieval_parameters or {}).get("exact_filters")
        if not spec.query or not spec.query.strip():
            raise ValueError("A complete targeted manifest requires the exact SQL query")
        if not isinstance(exact_filters, Mapping) or not exact_filters:
            raise ValueError("A complete targeted manifest requires exact API filters")
        if any(
            not isinstance(field, str)
            or not field.strip()
            or not isinstance(value, str)
            or not value.strip()
            for field, value in exact_filters.items()
        ):
            raise ValueError("A complete targeted manifest requires nonblank string filters")
        if any(not file.schema_fingerprint for file in spec.files):
            raise ValueError(
                "A complete targeted manifest requires a schema fingerprint for every file"
            )


def build_open_payments_manifest(
    manifest_path: Path,
    spec: OpenPaymentsManifestSpec,
) -> dict[str, Any]:
    """Build a conservative manifest for an annual archive or targeted API snapshot.

    The builder intentionally supplies no default payment semantics, aggregation grain, or release
    identifiers. A caller must verify those facts against the observed annual documentation before
    promoting a draft manifest to complete.
    """
    if manifest_path.exists():
        raise FileExistsError(f"Refusing to replace manifest: {manifest_path}")
    _validate_cms_url(spec.request_url)
    if not spec.files:
        raise ValueError("At least one retained Open Payments file is required")
    if spec.status == "complete":
        _validate_complete_manifest_spec(spec)

    observed_at = _iso_observed_at(spec.observed_at)
    exclusions = list(spec.exclusions)
    validation_notes = list(spec.validation_notes)
    if spec.snapshot_scope == "targeted-api":
        exclusions.append("Records outside the retained API query or filters")
        validation_notes.append(
            "This is a targeted API snapshot and not the complete program-year universe."
        )
        retrieval_method = "api"
    elif spec.snapshot_scope == "annual-archive":
        validation_notes.append(
            "The annual-archive scope describes the retained CMS package; validate every member "
            "before treating the manifest as complete."
        )
        retrieval_method = "download"
    else:
        raise ValueError(f"Unsupported Open Payments snapshot scope: {spec.snapshot_scope}")

    retrieval_parameters = dict(spec.retrieval_parameters or {})
    retrieval_parameters["snapshot_scope"] = spec.snapshot_scope
    manifest = {
        "manifest_version": 2,
        "status": spec.status,
        "source": {
            "name": "Centers for Medicare & Medicaid Services Open Payments",
            "type": "CMS",
            "homepage": OPEN_PAYMENTS_HOMEPAGE,
        },
        "dataset": {
            "name": spec.dataset_name,
            "slug": spec.dataset_slug,
            "dataset_id": spec.dataset_id,
            "version_id": spec.version_id,
            "data_year": spec.program_year,
            "population": spec.population,
            "aggregation_keys": list(spec.aggregation_keys),
        },
        "observation": {
            "published_at": spec.published_at.isoformat() if spec.published_at else None,
            "modified_at": _iso_date_or_datetime(spec.modified_at),
            "observed_at": observed_at,
            "accessed_at": observed_at[:10],
        },
        "documentation": {
            "landing_page": spec.landing_page,
            "methodology": spec.methodology,
            "data_dictionary": spec.data_dictionary,
            "snapshots": list(spec.documentation_snapshots),
        },
        "terms": {
            "license_name": spec.license_name,
            "license_url": spec.license_url,
            "access_restrictions": spec.access_restrictions,
            "public_use_verified": spec.public_use_verified,
        },
        "retrieval": {
            "method": retrieval_method,
            "request_url": spec.request_url,
            "parameters": retrieval_parameters,
            "query": spec.query,
        },
        "files": [_manifest_file_record(file) for file in spec.files],
        "semantics": {
            "monetary_fields": dict(spec.monetary_fields or {}),
            "utilization_fields": dict(spec.utilization_fields or {}),
            "suppression": spec.suppression,
            "exclusions": exclusions,
        },
        "validation": {
            "rows": spec.validation_rows,
            "columns": spec.validation_columns,
            "aggregation_key_duplicates": spec.aggregation_key_duplicates,
            "notes": validation_notes,
        },
    }

    schema_path = Path("research/data-manifests/source-manifest.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = manifest_path.with_name(f".{manifest_path.name}.part")
    temporary_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(manifest_path)
    return manifest


def profile_open_payments_sql_response(
    response_path: Path,
    *,
    program_year: int,
    exact_filters: Mapping[str, str],
    required_fields: Sequence[str] = (),
    record_id_field: str = "record_id",
    program_year_field: str = "program_year",
) -> dict[str, Any]:
    """Validate a retained Open Payments SQL response represented as a JSON row array."""
    if not exact_filters:
        raise ValueError("At least one exact Open Payments API filter is required")
    if response_path.stat().st_size > MAX_TARGETED_SQL_RESPONSE_BYTES:
        raise ValueError(
            "Open Payments targeted SQL response is larger than the 32 MiB in-memory "
            "validation ceiling; narrow the exact API query or use a future streaming path"
        )
    try:
        rows = json.loads(response_path.read_bytes())
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Open Payments API response is not valid JSON: {response_path}"
        ) from error
    if not isinstance(rows, list):
        raise ValueError("Open Payments SQL response must be a JSON row array")

    columns: set[str] = set()
    seen_record_ids: set[str] = set()
    duplicate_record_ids = 0
    invalid_record_ids = 0
    program_year_mismatches = 0
    filter_mismatches = 0
    rows_missing_required_fields = 0
    required = {
        record_id_field.casefold(),
        program_year_field.casefold(),
        *(field.casefold() for field in required_fields),
        *(field.casefold() for field in exact_filters),
    }

    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Open Payments SQL response contains a non-object row")
        row_columns = [str(key) for key in row]
        columns.update(row_columns)
        normalized_row = {key.casefold(): row[key] for key in row_columns}
        if len(normalized_row) != len(row):
            raise ValueError("Open Payments SQL response has case-insensitive column collisions")
        if not required.issubset(normalized_row):
            rows_missing_required_fields += 1

        record_id = str(normalized_row.get(record_id_field.casefold(), "")).strip()
        if not record_id:
            invalid_record_ids += 1
        elif record_id in seen_record_ids:
            duplicate_record_ids += 1
        else:
            seen_record_ids.add(record_id)

        if str(normalized_row.get(program_year_field.casefold(), "")) != str(program_year):
            program_year_mismatches += 1
        for field, expected_value in exact_filters.items():
            if str(normalized_row.get(field.casefold(), "")) != str(expected_value):
                filter_mismatches += 1

    if (
        duplicate_record_ids
        or invalid_record_ids
        or program_year_mismatches
        or filter_mismatches
        or rows_missing_required_fields
    ):
        raise ValueError(
            "Open Payments SQL response validation failed: "
            f"duplicate_record_ids={duplicate_record_ids}, "
            f"invalid_record_ids={invalid_record_ids}, "
            f"program_year_mismatches={program_year_mismatches}, "
            f"filter_mismatches={filter_mismatches}, "
            f"rows_missing_required_fields={rows_missing_required_fields}"
        )

    column_list = sorted(columns)
    return {
        "rows": len(rows),
        "columns": column_list,
        "column_count": len(column_list),
        "aggregation_key_duplicates": duplicate_record_ids,
        "schema_fingerprint": sha256("\n".join(column_list).encode()).hexdigest(),
    }


def _payment_category_semantics(
    payment_category: PaymentCategory,
) -> tuple[dict[str, str], dict[str, str], tuple[str, ...]]:
    if payment_category in {"general", "research"}:
        return (
            {
                "total_amount_of_payment_usdollars": (
                    "Gross amount that the reporting entity reported for this Open Payments "
                    "payment or transfer-of-value record, in U.S. dollars. It is not a Medicare "
                    "claim payment, pharmacy reimbursement, covered-recipient net income, or "
                    "estimated program loss; indirect and third-party routing fields must be "
                    "reviewed before assigning the recipient of value."
                )
            },
            {
                "number_of_payments_included_in_total_amount": (
                    "Number of payments or transfers the reporting entity combined into the "
                    "reported record; it is not healthcare-claim utilization."
                )
            },
            ("total_amount_of_payment_usdollars",),
        )
    if payment_category == "ownership":
        return (
            {
                "total_amount_invested_usdollars": (
                    "Dollar amount reported as invested by the physician or immediate family "
                    "member during the program year; not a Medicare claim payment or program loss."
                ),
                "value_of_interest": (
                    "Reported value of the ownership or investment interest; not a Medicare "
                    "claim payment or program loss."
                ),
            },
            {},
            ("total_amount_invested_usdollars", "value_of_interest"),
        )
    raise ValueError(f"Unsupported Open Payments payment category: {payment_category}")


def build_open_payments_sql_api_manifest(
    response_path: Path,
    manifest_path: Path,
    *,
    dataset_name: str,
    dataset_slug: str,
    program_year: int,
    payment_category: PaymentCategory,
    dataset_id: str,
    resource_id: str,
    request_url: str,
    query: str,
    exact_filters: Mapping[str, str],
    observed_at: datetime,
    landing_page: str,
    published_at: date | None = None,
    modified_at: date | datetime | None = None,
    documentation_snapshots: Sequence[str] = (),
) -> dict[str, Any]:
    """Validate and manifest one bounded, targeted Open Payments SQL API response."""
    if not dataset_id.strip() or not resource_id.strip():
        raise ValueError("Dataset and datastore resource identifiers are required")
    if not query.strip():
        raise ValueError("The exact retained Open Payments SQL query is required")
    monetary_fields, utilization_fields, required_fields = _payment_category_semantics(
        payment_category
    )
    profile = profile_open_payments_sql_response(
        response_path,
        program_year=program_year,
        exact_filters=exact_filters,
        required_fields=required_fields,
    )
    filter_description = ", ".join(
        f"{field}={value}" for field, value in sorted(exact_filters.items())
    )
    category_scope = {
        "general": "general payments or other transfers of value",
        "research": "research payments or other transfers of value",
        "ownership": "physician or immediate-family ownership or investment interests",
    }[payment_category]
    population = (
        f"Public CMS Open Payments {payment_category} records for program year {program_year} "
        f"returned by the exact targeted SQL API filters ({filter_description}). Open Payments "
        f"contains reporting-entity-submitted transparency records about {category_scope}; "
        "it is not a healthcare claims dataset."
    )
    return build_open_payments_manifest(
        manifest_path,
        OpenPaymentsManifestSpec(
            dataset_name=dataset_name,
            dataset_slug=dataset_slug,
            program_year=program_year,
            snapshot_scope="targeted-api",
            request_url=request_url,
            observed_at=observed_at,
            files=[
                OpenPaymentsFile(
                    response_path,
                    role="api-response",
                    media_type="application/json",
                    schema_fingerprint=profile["schema_fingerprint"],
                )
            ],
            dataset_id=dataset_id,
            version_id=resource_id,
            population=population,
            aggregation_keys=["record_id"],
            published_at=published_at,
            modified_at=modified_at,
            landing_page=landing_page,
            documentation_snapshots=documentation_snapshots,
            license_name="U.S. Government Work",
            license_url="https://www.usa.gov/government-works",
            access_restrictions=(
                "Public CMS Open Payments API; no authentication or data-use agreement required"
            ),
            public_use_verified=True,
            retrieval_parameters={
                "resource_id": resource_id,
                "exact_filters": dict(exact_filters),
            },
            query=query,
            monetary_fields=monetary_fields,
            utilization_fields=utilization_fields,
            suppression=(
                "No additional cell suppression is documented for this targeted API response. "
                "Program-year reporting thresholds, delayed-publication rules, corrections, and "
                "other publication rules affect which records appear; blank fields are not zero."
            ),
            exclusions=[
                "Payments and entities outside the Open Payments reporting scope",
                "Records not yet published under an allowed delayed-publication request",
            ],
            validation_rows=profile["rows"],
            validation_columns=profile["column_count"],
            aggregation_key_duplicates=profile["aggregation_key_duplicates"],
            validation_notes=[
                "The retained body parsed as a JSON row array.",
                (
                    "Every row matched the requested program year and exact field-value filters; "
                    "all nonblank record_id values were unique."
                ),
                (
                    "Open Payments publication does not imply that a reported relationship is "
                    "improper and does not identify Medicare claim proceeds."
                ),
            ],
            status="complete",
        ),
    )
