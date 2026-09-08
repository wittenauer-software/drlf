import ast
import csv
import re
from pathlib import Path

import yaml

from drlf.analysis.dmepos_shortlist import SCREEN_ALGORITHM, SCREEN_ALGORITHM_VERSION

REPOSITORY = Path(__file__).parents[1]
DOCS = REPOSITORY / "docs"
REQUIRED_DOCS = {
    "architecture.md",
    "capabilities.md",
    "data-sources/README.md",
    "data-sources/adding-a-source.md",
    "getting-started.md",
    "methods/README.md",
}
EXPECTED_SKILLS = {
    "acquire-cms-data",
    "investigate-dmepos-supplier-outliers",
    "investigate-part-b-service-utilization",
    "investigate-pharmacy-protocol-attribution",
    "manage-research-cases",
    "synthesize-research-learnings",
    "validate-claims-anomalies",
}
EXPECTED_PRODUCT_LABELS = {
    "Medicare Part D Prescribers by Provider and Drug",
    "Medicare Part D Prescribers by Geography and Drug",
    "Medicare Physician & Other Practitioners by Provider",
    "Medicare Physician & Other Practitioners by Provider and Service",
    "Medicare DMEPOS by Supplier",
    "Medicare DMEPOS by Supplier and Service",
    "CMS Open Payments General Payments",
    "NPPES NPI Registry",
    "Medicare FFS Public Provider Enrollment",
    "Revoked Medicare Providers and Suppliers",
}


def test_part_b_fixture_service_literals_are_registered_test_tokens() -> None:
    registry = yaml.safe_load(
        (REPOSITORY / "examples/synthetic-identities.yaml").read_text(encoding="utf-8")
    )
    codes = {entry["value"] for entry in registry["entries"] if entry["type"] == "service-code"}
    assert codes == {"TSTAA", "TSTAB", "TSTAC", "TSTAD"}
    assert all(code.isalpha() and len(code) == 5 for code in codes)
    malformed = {"TSTA", "TOO-LONG"}  # Deliberate format-rejection fixtures.
    observed = set()
    keys = {"HCPCS_Cd", "hcpcs_code", "HCPCS_Desc", "hcpcs_description"}

    def check_literal(key: str, value: ast.AST) -> None:
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            return
        if key in {"HCPCS_Cd", "hcpcs_code"}:
            assert value.value in codes | malformed, value.value
            observed.add(value.value)
        else:
            assert value.value in {f"Synthetic test service {letter}" for letter in "ABCD"}

    for path in sorted((REPOSITORY / "tests").glob("test_part_b*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=True):
                    if isinstance(key, ast.Constant) and key.value in keys:
                        check_literal(key.value, value)
            elif isinstance(node, ast.keyword) and node.arg in keys:
                check_literal(node.arg, node.value)
            elif isinstance(node, ast.FunctionDef):
                arguments = node.args.posonlyargs + node.args.args
                for arg, value in zip(
                    arguments[len(arguments) - len(node.args.defaults):],
                    node.args.defaults,
                    strict=True,
                ):
                    if arg.arg in keys:
                        check_literal(arg.arg, value)
                for arg, value in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True):
                    if arg.arg in keys and value is not None:
                        check_literal(arg.arg, value)
    assert codes <= observed
    with (REPOSITORY / "examples/synthetic-part-b-screen/scan.csv").open(
        encoding="utf-8", newline=""
    ) as source:
        rows = list(csv.DictReader(source))
    assert rows and {row["hcpcs_code"] for row in rows} == {"TSTAA"}


def _documentation_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(DOCS.rglob("*.md"))
    )


def test_public_onboarding_and_capability_documents_are_complete() -> None:
    paths = {
        path.relative_to(DOCS).as_posix()
        for path in DOCS.rglob("*.md")
        if path.is_file()
    }

    assert REQUIRED_DOCS <= paths

    combined = _documentation_text()
    for skill in EXPECTED_SKILLS:
        assert f"`{skill}`" in combined
    for product in EXPECTED_PRODUCT_LABELS:
        assert product in combined

    assert "22" in combined
    assert "two are explicitly experimental" in combined
    assert "repository-first" in combined
    assert "Windows 11 x64" in combined
    assert "Linux/POSIX" in combined
    assert "separate private research workspace" in combined.lower()
    assert "data/raw/" in combined
    assert "PostgreSQL" in combined
    assert "Evidence ceiling" in combined
    assert "explicit human permission" in combined


def test_public_docs_use_only_drlf_and_windows_v01_instructions() -> None:
    combined = _documentation_text()

    assert "fraud" + "-research" not in combined
    assert "fraud" + "_research" not in combined
    assert "```bash" not in combined
    assert "macOS" not in combined
    assert "3.14" not in combined
    assert re.search(r"(?<!\d)\d{10}(?!\d)", combined) is None


def test_getting_started_matches_baseline_and_environment_interfaces() -> None:
    guide = (DOCS / "getting-started.md").read_text(encoding="utf-8")
    environment = (REPOSITORY / ".env.example").read_text(encoding="utf-8")

    assert "baseline-manifest.json" in guide
    assert "Get-FileHash -Algorithm SHA256" in guide
    assert "--expected-manifest-sha256 $baselineHash" in guide
    assert "drlf-change-this-workspace-id" in environment
    assert guide.count("drlf-change-this-workspace-id") == 1
    assert environment.count("replace-with-a-local-password") == 2
    assert "writes an ignored `.env` with a generated password" in guide
    assert ".\\.venv\\Scripts\\drlf.exe release-list" in guide
    assert ".\\.venv\\Scripts\\drlf.exe source-status" in guide
    assert "`doctor` does not replace either source command" in guide


def test_public_docs_state_runtime_boundaries_without_overclaiming() -> None:
    architecture = (DOCS / "architecture.md").read_text(encoding="utf-8")
    capabilities = (DOCS / "capabilities.md").read_text(encoding="utf-8")
    methods = (DOCS / "methods" / "README.md").read_text(encoding="utf-8")

    assert "classifies every registered command as public-safe or real-data" in architecture
    assert "not a universal\nfilesystem sandbox" in architecture
    assert "Workspace separation is not a universal filesystem sandbox" in capabilities
    assert "bounded screening helpers rather than complete registered case runs" in methods


def test_public_documentation_links_resolve_inside_the_candidate() -> None:
    markdown_link = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
    missing: list[str] = []

    for document in sorted(DOCS.rglob("*.md")):
        for target in markdown_link.findall(document.read_text(encoding="utf-8")):
            if target.startswith(("https://", "http://", "mailto:", "#")):
                continue
            relative_target = target.split("#", 1)[0]
            resolved = (document.parent / relative_target).resolve()
            try:
                resolved.relative_to(REPOSITORY.resolve())
            except ValueError:
                missing.append(f"{document.relative_to(REPOSITORY)} -> {target} (escapes root)")
                continue
            if not resolved.exists():
                missing.append(f"{document.relative_to(REPOSITORY)} -> {target}")

    assert missing == []


def _missing_literal_dependencies(root: Path) -> list[str]:
    """Check concrete dependency paths, excluding generated research outputs."""
    literal = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
    file_path = re.compile(
        r"(?:docs|src|sql|research/methods|\.agents/skills)/[a-zA-Z0-9_./-]+\.[a-z]+"
    )
    missing = []
    documents = [*(root / "docs").rglob("*.md"), *(root / ".agents/skills").rglob("*.md")]
    for document in documents:
        for target in literal.findall(document.read_text(encoding="utf-8")):
            if file_path.fullmatch(target):
                resolved = (root / target).resolve()
                if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
                    missing.append(f"{document.relative_to(root).as_posix()} -> {target}")
    return missing


def test_public_literal_method_and_implementation_dependencies_exist() -> None:
    assert _missing_literal_dependencies(REPOSITORY) == []


def test_dependency_check_rejects_missing_and_escaping_inline_paths(tmp_path: Path) -> None:
    reference = tmp_path / ".agents/skills/example/references/contract.md"
    reference.parent.mkdir(parents=True)
    reference.write_text("Read `docs/methods/example.md`.\n", encoding="utf-8")
    assert len(_missing_literal_dependencies(tmp_path)) == 1
    target = tmp_path / "docs/methods/example.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Synthetic method\n", encoding="utf-8")
    assert _missing_literal_dependencies(tmp_path) == []
    reference.write_text("Read `docs/../../outside.md`.\n", encoding="utf-8")
    assert len(_missing_literal_dependencies(tmp_path)) == 1


def test_dmepos_method_parameters_match_the_shipped_sql_gate() -> None:
    method = (DOCS / "methods/dmepos-supplier-outliers.md").read_text(encoding="utf-8")
    sql = (REPOSITORY / "sql/analysis/dmepos/supplier_summary_screen.sql").read_text(
        encoding="utf-8"
    )
    settings, rest = sql.split("), parameter_gate as (", 1)
    gate = rest.split("), requested_years", 1)[0]
    aliases = dict(re.findall(r"%\((\w+)\)s::[a-z]+(?:\[\])? as (\w+)", settings))
    required = dict(re.findall(r"(\w+) = (?:array)?(\[[0-9, ]+\]|[0-9.]+)", gate))
    documented = dict(re.findall(r"^\| `(\w+)` \| `([^`]+)` \|$", method, re.MULTILINE))
    assert len(aliases) == 10
    assert documented == {parameter: required[alias] for parameter, alias in aliases.items()}
    assert f"`{SCREEN_ALGORITHM}` version `{SCREEN_ALGORITHM_VERSION}`" in method
