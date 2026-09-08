import json

import pytest

from drlf.sources.cms_api import HttpResponse
from drlf.sources.cms_catalog import fetch_dataset_version, resolve_dataset_version


def _catalog() -> dict:
    title = "Example annual dataset"
    return {
        "dataset": [
            {
                "title": title,
                "identifier": (
                    "https://data.cms.gov/data-api/v1/dataset/"
                    "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/data-viewer"
                ),
                "distribution": [
                    {
                        "format": "API",
                        "description": "latest",
                        "accessURL": (
                            "https://data.cms.gov/data-api/v1/dataset/"
                            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/data"
                        ),
                        "temporal": "2024-01-01/2024-12-31",
                    },
                    {
                        "format": "API",
                        "accessURL": (
                            "https://data.cms.gov/data-api/v1/dataset/"
                            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb/data"
                        ),
                        "modified": "2026-05-21",
                        "temporal": "2024-01-01/2024-12-31",
                    },
                    {
                        "format": "CSV",
                        "downloadURL": "https://data.cms.gov/example.csv",
                        "temporal": "2024-01-01/2024-12-31",
                    },
                ],
            }
        ]
    }


def test_resolve_dataset_version_uses_pinned_distribution() -> None:
    result = resolve_dataset_version(
        _catalog(),
        dataset_title="Example annual dataset",
        data_year=2024,
    )

    assert result.dataset_type_uuid == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert result.version_uuid == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    assert result.modified_at == "2026-05-21"


def test_fetch_dataset_version_parses_bounded_response() -> None:
    body = json.dumps(_catalog()).encode()
    result = fetch_dataset_version(
        "Example annual dataset",
        2024,
        fetcher=lambda _url: HttpResponse(body, "application/json"),
    )

    assert result.data_year == 2024


def test_resolver_rejects_missing_pinned_version() -> None:
    with pytest.raises(ValueError, match="found 0"):
        resolve_dataset_version(
            _catalog(),
            dataset_title="Example annual dataset",
            data_year=2023,
        )
