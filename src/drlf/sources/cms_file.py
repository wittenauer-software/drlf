from __future__ import annotations

import json
import os
import re
import shutil
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import truststore

DEFAULT_CHUNK_BYTES = 1024 * 1024
DEFAULT_DISK_RESERVE_BYTES = 64 * 1024 * 1024


class CmsDownloadLimitExceeded(RuntimeError):
    """Raised before a retained CMS download would exceed its explicit byte ceiling."""


class CmsInsufficientDiskSpace(RuntimeError):
    """Raised before a CMS download when free space cannot cover the bounded write."""


@dataclass(frozen=True)
class CmsFilePreflight:
    request_url: str
    final_url: str
    total_bytes: int | None
    media_type: str | None
    etag: str | None
    last_modified: str | None
    size_source: Literal["head", "content-range", "unknown"]


@dataclass(frozen=True)
class CmsFileDownload:
    path: Path
    request_url: str
    final_url: str
    bytes: int
    sha256: str
    media_type: str
    compression: str | None
    resumed_from: int | None
    etag: str | None
    last_modified: str | None
    max_bytes: int
    preflight_total_bytes: int | None
    preflight_size_source: Literal["head", "content-range", "unknown"]
    disk_reserve_bytes: int | None = None


def validate_cms_https_url(url: str) -> None:
    """Require an HTTPS URL on cms.gov or one of its subdomains."""
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or not (hostname == "cms.gov" or hostname.endswith(".cms.gov")):
        raise ValueError(f"CMS acquisition requires an HTTPS CMS URL: {url}")


def repository_relative_raw_path(path: Path) -> str:
    """Return a repository-relative raw path, rejecting paths outside immutable raw storage."""
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


def _response_media_type(response: Any, path: Path) -> str:
    headers = response.headers
    if hasattr(headers, "get_content_type"):
        media_type = str(headers.get_content_type())
    else:
        media_type = (_response_header(response, "Content-Type") or "").split(";", 1)[0]
    media_type = media_type.strip()
    return media_type or _infer_file_metadata(path)[0]


def _response_status(response: Any) -> int:
    status = getattr(response, "status", None)
    if status is None and hasattr(response, "getcode"):
        status = response.getcode()
    if not isinstance(status, int):
        raise RuntimeError("CMS response did not expose an HTTP status")
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
        raise RuntimeError(f"Invalid or missing Content-Range: {value!r}")
    total = int(match.group(3)) if match.group(3) is not None else None
    return int(match.group(1)), int(match.group(2)), total


def _partial_paths(output_path: Path) -> tuple[Path, Path]:
    return (
        output_path.with_name(f".{output_path.name}.part"),
        output_path.with_name(f".{output_path.name}.part.json"),
    )


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


def preflight_cms_file(
    request_url: str,
    *,
    timeout: int = 90,
    opener: Callable[[Request, int], Any] = _open_request,
) -> CmsFilePreflight:
    """Discover a CMS artifact's size without retaining its body.

    When HEAD omits Content-Length, a one-byte Range probe obtains the total from
    Content-Range. A server that supports neither still remains subject to the streaming cap.
    """
    validate_cms_https_url(request_url)
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
                raise RuntimeError(f"Unexpected HTTP {status} from CMS HEAD: {request_url}")
            observed_final_url = response.geturl() if hasattr(response, "geturl") else request_url
            validate_cms_https_url(observed_final_url)
            observed_media_type = _response_media_type(response, Path(urlsplit(request_url).path))
            observed_etag = _response_header(response, "ETag")
            observed_modified = _response_header(response, "Last-Modified")
            content_length = _parse_content_length(response)
            if content_length is not None:
                return CmsFilePreflight(
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
                f"CMS HEAD preflight failed with HTTP {error.code}: {request_url}"
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
        validate_cms_https_url(final_url)
        media_type = _response_media_type(response, Path(urlsplit(request_url).path))
        etag = _response_header(response, "ETag") or observed_etag
        last_modified = _response_header(response, "Last-Modified") or observed_modified
        if status == 206:
            start, end, total_bytes = _parse_content_range(response)
            if (start, end) != (0, 0):
                raise RuntimeError(
                    f"CMS byte-range preflight returned an unexpected range: {start}-{end}"
                )
            return CmsFilePreflight(
                request_url=request_url,
                final_url=final_url,
                total_bytes=total_bytes,
                media_type=media_type,
                etag=etag,
                last_modified=last_modified,
                size_source="content-range" if total_bytes is not None else "unknown",
            )
        if status == 200:
            content_length = _parse_content_length(response)
            return CmsFilePreflight(
                request_url=request_url,
                final_url=final_url,
                total_bytes=content_length,
                media_type=media_type,
                etag=etag,
                last_modified=last_modified,
                size_source="head" if content_length is not None else "unknown",
            )
        raise RuntimeError(f"Unexpected HTTP {status} from CMS range preflight: {request_url}")


def download_cms_file(
    request_url: str,
    output_path: Path,
    *,
    max_bytes: int,
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
    timeout: int = 90,
    resume: bool = True,
    disk_reserve_bytes: int = DEFAULT_DISK_RESERVE_BYTES,
    opener: Callable[[Request, int], Any] = _open_request,
    preflight_result: CmsFilePreflight | None = None,
) -> CmsFileDownload:
    """Stream one public CMS artifact to immutable raw storage under an explicit cap."""
    validate_cms_https_url(request_url)
    repository_relative_raw_path(output_path)
    if max_bytes < 1:
        raise ValueError("max_bytes must be at least 1")
    if chunk_bytes < 1:
        raise ValueError("chunk_bytes must be at least 1")
    if timeout < 1:
        raise ValueError("timeout must be at least 1 second")
    if disk_reserve_bytes < 0:
        raise ValueError("disk_reserve_bytes must be nonnegative")
    if output_path.exists():
        raise FileExistsError(f"Refusing to replace retained snapshot: {output_path}")

    preflight = preflight_result or preflight_cms_file(
        request_url,
        timeout=timeout,
        opener=opener,
    )
    if preflight.request_url != request_url:
        raise ValueError("Preflight result belongs to a different CMS URL")
    validate_cms_https_url(preflight.final_url)
    if preflight.total_bytes is not None and preflight.total_bytes > max_bytes:
        raise CmsDownloadLimitExceeded(
            f"CMS preflight found {preflight.total_bytes} bytes, above the "
            f"{max_bytes}-byte ceiling; the file body was not transferred"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path, state_path = _partial_paths(output_path)
    partial_size = partial_path.stat().st_size if partial_path.exists() else 0
    if partial_size > max_bytes:
        raise CmsDownloadLimitExceeded(
            f"Existing partial file is {partial_size} bytes, above the {max_bytes}-byte ceiling"
        )
    if partial_size and not resume:
        raise FileExistsError(f"Incomplete transfer already exists: {partial_path}")

    state = _load_partial_state(state_path) if partial_size else None
    if state and state.get("request_url") != request_url:
        raise RuntimeError("Partial transfer URL differs from the requested CMS URL")

    validator = None
    if state:
        validator = state.get("etag") or state.get("last_modified")
        current_validator = preflight.etag or preflight.last_modified
        if current_validator and current_validator != validator:
            validator = None
    requested_resume = partial_size if partial_size and validator else 0
    bounded_final_bytes = preflight.total_bytes or max_bytes
    required_free_bytes = max(0, bounded_final_bytes - requested_resume)
    available_bytes = shutil.disk_usage(output_path.parent).free
    if available_bytes < required_free_bytes + disk_reserve_bytes:
        raise CmsInsufficientDiskSpace(
            "CMS download requires at least "
            f"{required_free_bytes + disk_reserve_bytes} free bytes "
            f"({required_free_bytes} bounded write bytes plus "
            f"{disk_reserve_bytes} reserve), but only {available_bytes} are available"
        )
    headers = {"User-Agent": "drlf/0.1"}
    if requested_resume:
        headers["Range"] = f"bytes={requested_resume}-"
        headers["If-Range"] = str(validator)

    request = Request(request_url, headers=headers)
    with opener(request, timeout) as response:
        status = _response_status(response)
        final_url = response.geturl() if hasattr(response, "geturl") else request_url
        validate_cms_https_url(final_url)
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
                raise RuntimeError("Resumed response total differs from the CMS preflight total")
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
            write_mode = "wb"
            retained_prefix = 0
        else:
            raise RuntimeError(
                f"Unexpected HTTP {status} while downloading CMS source: {request_url}"
            )

        content_length = _parse_content_length(response)
        if (
            expected_response_bytes is not None
            and content_length is not None
            and expected_response_bytes != content_length
        ):
            raise RuntimeError("CMS Content-Length differs from promised Content-Range length")
        if expected_response_bytes is None:
            expected_response_bytes = content_length
        if content_length is not None and retained_prefix + content_length > max_bytes:
            raise CmsDownloadLimitExceeded(
                f"CMS response would reach {retained_prefix + content_length} bytes, above the "
                f"{max_bytes}-byte ceiling"
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
                    raise CmsDownloadLimitExceeded(
                        f"CMS transfer crossed the explicit {max_bytes}-byte ceiling; "
                        "partial data remains resumable"
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
                "CMS transfer ended before the promised response length; "
                "the partial file remains resumable"
            )
        if expected_final_bytes is not None and bytes_written != expected_final_bytes:
            raise RuntimeError(
                "CMS transfer size does not match the preflight or Content-Range total; "
                "the partial file remains resumable"
            )

    partial_path.replace(output_path)
    state_path.unlink(missing_ok=True)
    return CmsFileDownload(
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
        max_bytes=max_bytes,
        preflight_total_bytes=preflight.total_bytes,
        preflight_size_source=preflight.size_source,
        disk_reserve_bytes=disk_reserve_bytes,
    )


def inspect_retained_cms_file(
    request_url: str,
    path: Path,
    *,
    max_bytes: int,
    preflight_result: CmsFilePreflight | None = None,
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
) -> CmsFileDownload:
    """Revalidate an already-complete immutable CMS file without downloading it again.

    The acquisition's resume offset is intentionally unknown in this recovery path. The
    retained bytes, hash, current upstream metadata, and explicit ceiling remain auditable.
    """
    validate_cms_https_url(request_url)
    repository_relative_raw_path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Retained CMS file does not exist: {path}")
    if max_bytes < 1:
        raise ValueError("max_bytes must be at least 1")
    if chunk_bytes < 1:
        raise ValueError("chunk_bytes must be at least 1")
    file_bytes = path.stat().st_size
    if file_bytes > max_bytes:
        raise CmsDownloadLimitExceeded(
            f"Retained CMS file is {file_bytes} bytes, above the {max_bytes}-byte ceiling"
        )
    preflight = preflight_result or preflight_cms_file(request_url)
    if preflight.request_url != request_url:
        raise ValueError("Preflight result belongs to a different CMS URL")
    if preflight.total_bytes is not None and preflight.total_bytes != file_bytes:
        raise RuntimeError("Retained CMS file size differs from the current upstream size")
    digest = _hash_existing_file(path, chunk_bytes).hexdigest()
    inferred_media_type, compression = _infer_file_metadata(path)
    return CmsFileDownload(
        path=path,
        request_url=request_url,
        final_url=preflight.final_url,
        bytes=file_bytes,
        sha256=digest,
        media_type=preflight.media_type or inferred_media_type,
        compression=compression,
        resumed_from=None,
        etag=preflight.etag,
        last_modified=preflight.last_modified,
        max_bytes=max_bytes,
        preflight_total_bytes=preflight.total_bytes,
        preflight_size_source=preflight.size_source,
        disk_reserve_bytes=None,
    )
