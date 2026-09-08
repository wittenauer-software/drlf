from pathlib import Path

from drlf.baseline_manifest import build_manifest_document, canonical_manifest_bytes


def test_baseline_manifest_is_canonical_and_excludes_local_namespaces(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_bytes(b"guide\n")
    (tmp_path / ".drlf").mkdir()
    (tmp_path / ".drlf" / "distribution.json").write_bytes(b"ignored\n")
    (tmp_path / "research" / "cases").mkdir(parents=True)
    (tmp_path / "research" / "cases" / "private.md").write_bytes(b"ignored\n")
    (tmp_path / "baseline-manifest.json").write_bytes(b"old manifest\n")

    document = build_manifest_document(tmp_path)

    assert document["files"] == [
        {
            "path": "docs/guide.md",
            "bytes": 6,
            "sha256": "90c390ec1de806bf945885cd0af51e90c3cd8cda0d0ff676051a56c20848c90f",
            "mode": "100644",
        }
    ]
    assert canonical_manifest_bytes(document).endswith(b"\n")


def test_committed_baseline_manifest_matches_candidate_tree() -> None:
    root = Path(__file__).parents[1]
    expected = canonical_manifest_bytes(build_manifest_document(root))

    assert (root / "baseline-manifest.json").read_bytes() == expected
