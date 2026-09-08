from pathlib import Path

import yaml

from drlf.foundation_catalog import validate_foundation_catalog

REPOSITORY = Path(__file__).parents[1]
CATALOG = REPOSITORY / "foundations" / "catalog.yaml"


def test_public_foundation_catalog_is_complete_and_valid() -> None:
    assert validate_foundation_catalog(CATALOG) == []
    document = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))

    assert len(document["records"]) == 22
    assert sum(record["status"] == "experimental" for record in document["records"]) == 2


def test_public_foundations_do_not_contain_private_lineage() -> None:
    text = CATALOG.read_text(encoding="utf-8")

    assert "LEARN" + "-" not in text
    assert "CASE-" not in text
    assert "fraud" + "-research-healthcare" not in text
    assert "wittenauer" + "-software" not in text


def test_validator_rejects_a_promoted_experiment(tmp_path: Path) -> None:
    document = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    experiment = next(
        record for record in document["records"] if record["status"] == "experimental"
    )
    experiment["status"] = "maintained"
    changed = tmp_path / "catalog.yaml"
    changed.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    errors = validate_foundation_catalog(changed)

    assert "the two routing hypotheses must remain explicitly experimental" in errors
