from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

from drlf.sources.cms_api import HttpResponse, fetch_with_retries

CMS_CATALOG_URL = "https://data.cms.gov/data.json"
CMS_CATALOG_MAX_BYTES = 16 * 1024 * 1024
UUID_PATTERN = re.compile(
    r"/dataset(?:-resources)?/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})(?:/|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CmsDatasetVersion:
    dataset_title: str
    dataset_type_uuid: str
    data_year: int
    version_uuid: str
    access_url: str
    modified_at: str | None
    temporal: str

    def as_dict(self) -> dict[str, str | int | None]:
        return asdict(self)


def _uuid_from_url(url: str) -> str:
    match = UUID_PATTERN.search(url)
    if not match:
        raise ValueError(f"CMS catalog URL does not contain a dataset UUID: {url}")
    return match.group(1).lower()


def resolve_dataset_version(
    catalog: dict[str, Any],
    *,
    dataset_title: str,
    data_year: int,
) -> CmsDatasetVersion:
    """Resolve one pinned annual API UUID from the official CMS DCAT catalog."""
    datasets = catalog.get("dataset")
    if not isinstance(datasets, list):
        raise ValueError("CMS catalog does not contain a dataset array")
    matches = [item for item in datasets if item.get("title") == dataset_title]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one exact CMS dataset title match, found {len(matches)}: {dataset_title}"
        )

    dataset = matches[0]
    identifier = dataset.get("identifier")
    if not isinstance(identifier, str):
        raise ValueError("CMS dataset is missing its identifier URL")
    dataset_type_uuid = _uuid_from_url(identifier)

    candidates: list[dict[str, Any]] = []
    for distribution in dataset.get("distribution", []):
        if not isinstance(distribution, dict) or distribution.get("format") != "API":
            continue
        temporal = distribution.get("temporal")
        access_url = distribution.get("accessURL")
        if not isinstance(temporal, str) or not temporal.startswith(f"{data_year}-"):
            continue
        if not isinstance(access_url, str):
            continue
        version_uuid = _uuid_from_url(access_url)
        if version_uuid == dataset_type_uuid or distribution.get("description") == "latest":
            continue
        candidates.append(distribution)

    if len(candidates) != 1:
        raise ValueError(
            f"Expected one pinned API distribution for {dataset_title} {data_year}, "
            f"found {len(candidates)}"
        )
    distribution = candidates[0]
    access_url = str(distribution["accessURL"])
    return CmsDatasetVersion(
        dataset_title=dataset_title,
        dataset_type_uuid=dataset_type_uuid,
        data_year=data_year,
        version_uuid=_uuid_from_url(access_url),
        access_url=access_url,
        modified_at=(
            str(distribution["modified"]) if distribution.get("modified") is not None else None
        ),
        temporal=str(distribution["temporal"]),
    )


def fetch_dataset_version(
    dataset_title: str,
    data_year: int,
    *,
    fetcher: Any | None = None,
) -> CmsDatasetVersion:
    response: HttpResponse = (
        fetcher(CMS_CATALOG_URL)
        if fetcher is not None
        else fetch_with_retries(CMS_CATALOG_URL, max_bytes=CMS_CATALOG_MAX_BYTES)
    )
    try:
        catalog = json.loads(response.body)
    except json.JSONDecodeError as error:
        raise ValueError("CMS catalog response was not JSON") from error
    if not isinstance(catalog, dict):
        raise ValueError("CMS catalog response was not an object")
    return resolve_dataset_version(
        catalog,
        dataset_title=dataset_title,
        data_year=data_year,
    )
