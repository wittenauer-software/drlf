from __future__ import annotations

import io
import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request

import pytest

from drlf.sources.cms_file import (
    CmsDownloadLimitExceeded,
    CmsFilePreflight,
    CmsInsufficientDiskSpace,
    download_cms_file,
    inspect_retained_cms_file,
    preflight_cms_file,
)


class _Response(io.BytesIO):
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        url: str = "https://data.cms.gov/source.csv",
    ) -> None:
        super().__init__(body)
        self.status = status
        self.headers = headers or {}
        self._url = url

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _temporary_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.chdir(repository)


def _preflight(
    request_url: str,
    *,
    total_bytes: int | None,
    etag: str | None = None,
) -> CmsFilePreflight:
    return CmsFilePreflight(
        request_url=request_url,
        final_url=request_url,
        total_bytes=total_bytes,
        media_type="text/csv",
        etag=etag,
        last_modified=None,
        size_source="head" if total_bytes is not None else "unknown",
    )


def test_preflight_uses_bounded_range_when_head_omits_size() -> None:
    requests: list[Request] = []
    responses = iter(
        [
            _Response(b"", headers={"Content-Type": "text/csv", "ETag": '"release"'}),
            _Response(
                b"x",
                status=206,
                headers={
                    "Content-Type": "text/csv",
                    "Content-Length": "1",
                    "Content-Range": "bytes 0-0/31326606",
                    "ETag": '"release"',
                },
            ),
        ]
    )

    def opener(request: Request, _timeout: int) -> _Response:
        requests.append(request)
        return next(responses)

    result = preflight_cms_file("https://data.cms.gov/source.csv", opener=opener)

    assert requests[0].get_method() == "HEAD"
    assert requests[1].get_header("Range") == "bytes=0-0"
    assert result.total_bytes == 31_326_606
    assert result.size_source == "content-range"


def test_download_streams_hashes_and_refuses_to_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _temporary_repository(tmp_path, monkeypatch)
    body = b"column\nvalue\n"
    request_url = "https://data.cms.gov/files/source.csv"
    output = Path("data/raw/cms/example/2024/source.csv")

    result = download_cms_file(
        request_url,
        output,
        max_bytes=1_024,
        chunk_bytes=3,
        opener=lambda _request, _timeout: _Response(
            body,
            headers={
                "Content-Length": str(len(body)),
                "Content-Type": "text/csv",
                "ETag": '"release"',
            },
            url=request_url,
        ),
        preflight_result=_preflight(request_url, total_bytes=len(body), etag='"release"'),
    )

    assert output.read_bytes() == body
    assert result.bytes == len(body)
    assert result.sha256 == sha256(body).hexdigest()
    assert result.max_bytes == 1_024
    assert not output.with_name(".source.csv.part").exists()
    with pytest.raises(FileExistsError, match="Refusing to replace"):
        download_cms_file(request_url, output, max_bytes=1_024)


def test_download_rejects_preflight_size_above_cap_before_full_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _temporary_repository(tmp_path, monkeypatch)
    request_url = "https://data.cms.gov/files/source.csv"
    opened = False

    def opener(_request: Request, _timeout: int) -> _Response:
        nonlocal opened
        opened = True
        return _Response(b"unused")

    with pytest.raises(CmsDownloadLimitExceeded, match="body was not transferred"):
        download_cms_file(
            request_url,
            Path("data/raw/cms/example/source.csv"),
            max_bytes=100,
            opener=opener,
            preflight_result=_preflight(request_url, total_bytes=101),
        )

    assert not opened


def test_download_checks_disk_capacity_before_full_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _temporary_repository(tmp_path, monkeypatch)
    request_url = "https://data.cms.gov/files/source.csv"
    opened = False

    def opener(_request: Request, _timeout: int) -> _Response:
        nonlocal opened
        opened = True
        return _Response(b"unused")

    monkeypatch.setattr(
        "drlf.sources.cms_file.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=69),
    )
    with pytest.raises(CmsInsufficientDiskSpace, match="50 bounded write bytes"):
        download_cms_file(
            request_url,
            Path("data/raw/cms/example/source.csv"),
            max_bytes=100,
            disk_reserve_bytes=20,
            opener=opener,
            preflight_result=_preflight(request_url, total_bytes=50),
        )

    assert not opened


def test_unknown_length_download_stops_at_cap_and_retains_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _temporary_repository(tmp_path, monkeypatch)
    request_url = "https://data.cms.gov/files/source.csv"
    output = Path("data/raw/cms/example/source.csv")

    with pytest.raises(CmsDownloadLimitExceeded, match="explicit 5-byte ceiling"):
        download_cms_file(
            request_url,
            output,
            max_bytes=5,
            chunk_bytes=4,
            opener=lambda _request, _timeout: _Response(b"12345678", url=request_url),
            preflight_result=_preflight(request_url, total_bytes=None),
        )

    assert output.with_name(".source.csv.part").read_bytes() == b"1234"
    assert not output.exists()


def test_download_resumes_only_with_validator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _temporary_repository(tmp_path, monkeypatch)
    request_url = "https://data.cms.gov/files/source.csv"
    output = Path("data/raw/cms/example/source.csv")
    output.parent.mkdir(parents=True)
    output.with_name(".source.csv.part").write_bytes(b"abc")
    output.with_name(".source.csv.part.json").write_text(
        json.dumps(
            {
                "state_version": 1,
                "request_url": request_url,
                "final_url": request_url,
                "etag": '"release"',
                "last_modified": None,
            }
        ),
        encoding="utf-8",
    )
    requests: list[Request] = []

    def opener(request: Request, _timeout: int) -> _Response:
        requests.append(request)
        return _Response(
            b"def",
            status=206,
            headers={
                "Content-Length": "3",
                "Content-Range": "bytes 3-5/6",
                "Content-Type": "text/csv",
                "ETag": '"release"',
            },
            url=request_url,
        )

    result = download_cms_file(
        request_url,
        output,
        max_bytes=10,
        opener=opener,
        preflight_result=_preflight(request_url, total_bytes=6, etag='"release"'),
    )

    assert requests[0].get_header("Range") == "bytes=3-"
    assert requests[0].get_header("If-range") == '"release"'
    assert result.resumed_from == 3
    assert result.sha256 == sha256(b"abcdef").hexdigest()
    assert output.read_bytes() == b"abcdef"


def test_download_rejects_non_cms_url_and_non_raw_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _temporary_repository(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="HTTPS CMS URL"):
        download_cms_file(
            "https://example.com/source.csv",
            Path("data/raw/cms/example/source.csv"),
            max_bytes=100,
        )
    with pytest.raises(ValueError, match="under data/raw"):
        download_cms_file(
            "https://data.cms.gov/source.csv",
            Path("source.csv"),
            max_bytes=100,
        )


def test_inspect_retained_file_hashes_without_reacquiring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _temporary_repository(tmp_path, monkeypatch)
    request_url = "https://data.cms.gov/files/source.csv"
    retained = Path("data/raw/cms/example/source.csv")
    retained.parent.mkdir(parents=True)
    retained.write_bytes(b"column\nvalue\n")

    result = inspect_retained_cms_file(
        request_url,
        retained,
        max_bytes=1_024,
        preflight_result=_preflight(request_url, total_bytes=None),
        chunk_bytes=3,
    )

    assert result.bytes == retained.stat().st_size
    assert result.sha256 == sha256(retained.read_bytes()).hexdigest()
    assert result.resumed_from is None


def test_inspect_retained_file_enforces_upstream_size_and_ceiling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _temporary_repository(tmp_path, monkeypatch)
    request_url = "https://data.cms.gov/files/source.csv"
    retained = Path("data/raw/cms/example/source.csv")
    retained.parent.mkdir(parents=True)
    retained.write_bytes(b"123456")

    with pytest.raises(CmsDownloadLimitExceeded, match="above the 5-byte ceiling"):
        inspect_retained_cms_file(request_url, retained, max_bytes=5)
    with pytest.raises(RuntimeError, match="differs from the current upstream size"):
        inspect_retained_cms_file(
            request_url,
            retained,
            max_bytes=10,
            preflight_result=_preflight(request_url, total_bytes=7),
        )
