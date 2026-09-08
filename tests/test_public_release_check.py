from __future__ import annotations

import errno
import json
import os
import stat
import subprocess
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from drlf import cli
from drlf.public_release import PublicReleaseMode, git_tree, run_public_release_check
from drlf.public_release.policy import load_policy, validate_report_document

SAFE_DENY_VALUE = "PRIVATE-DENY-VALUE-NOT-IN-CANDIDATE"


@dataclass(frozen=True)
class PreparedCheck:
    candidate: Path
    policy: Path
    inventory: Path
    deny_inventory: Path
    reports: Path


def _registry_bytes(entries: list[dict[str, str]] | None = None) -> bytes:
    document = {"registry_version": 1, "entries": entries or []}
    return yaml.safe_dump(document, sort_keys=False).encode("utf-8")


def _base_candidate_contents(*, readme: str = "Public release candidate.\n") -> dict[str, bytes]:
    return {
        "PUBLIC-GOVERNANCE.md": b"Synthetic public governance fixture.\n",
        "README.md": readme.encode("utf-8"),
        "examples/synthetic-identities.yaml": _registry_bytes(),
    }


def _policy_document(**updates: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": 1,
        "policy_id": "public-release-test",
        "policy_version": "1",
        "bounds": {
            "max_paths": 100,
            "max_path_bytes": 512,
            "max_file_bytes": 1_048_576,
            "max_total_bytes": 1_048_576,
            "max_runtime_seconds": 30,
            "max_git_output_bytes": 2_097_152,
            "max_git_objects": 100,
            "max_structured_bytes": 262_144,
            "max_deny_rules": 100,
            "max_findings": 100,
        },
        "decisions": {
            "repository_owner": "synthetic-owner",
            "repository_name": "synthetic-public-repository",
            "package_name": "synthetic-public-package",
            "license_spdx": "Apache-2.0",
            "license_path": "PUBLIC-GOVERNANCE.md",
            "contribution_policy_path": "PUBLIC-GOVERNANCE.md",
            "code_of_conduct_path": "PUBLIC-GOVERNANCE.md",
            "governance_path": "PUBLIC-GOVERNANCE.md",
            "security_path": "PUBLIC-GOVERNANCE.md",
            "support_path": "PUBLIC-GOVERNANCE.md",
            "commit_author_name": "Synthetic Test Author",
            "commit_author_email": "synthetic-test@example.invalid",
            "commit_committer_name": "Synthetic Test Author",
            "commit_committer_email": "synthetic-test@example.invalid",
            "approved_public_contacts": ["synthetic-test@example.invalid"],
        },
        "required_paths": [
            "PUBLIC-GOVERNANCE.md",
            "examples/synthetic-identities.yaml",
        ],
        "forbidden_path_prefixes": ["data", "operations/public-sync"],
        "synthetic_registry_path": "examples/synthetic-identities.yaml",
        "check_markdown_links": True,
        "require_clean_worktree": True,
        "require_root_commit": True,
    }
    document.update(updates)
    return document


def _write_candidate(root: Path, contents: dict[str, bytes]) -> None:
    root.mkdir(parents=True)
    for relative, content in contents.items():
        path = root / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _write_controls(
    root: Path,
    *,
    candidate: Path,
    contents: dict[str, bytes],
    policy_updates: dict[str, object] | None = None,
    deny_value: str = SAFE_DENY_VALUE,
    git_modes: dict[str, str] | None = None,
) -> PreparedCheck:
    controls = root / "controls"
    reports = root / "reports"
    controls.mkdir(parents=True)
    reports.mkdir(parents=True)

    inventory = controls / "inventory.yaml"
    inventory_document = {
        "schema_version": 1,
        "inventory_id": "synthetic-reviewed-inventory",
        "files": [
            {
                "path": relative,
                "bytes": len(content),
                "sha256": sha256(content).hexdigest(),
                "git_mode": (git_modes or {}).get(relative, "100644"),
                "classification": (
                    "synthetic-replacement"
                    if relative == "examples/synthetic-identities.yaml"
                    else "rewrite"
                ),
                "content_type": "text",
            }
            for relative, content in sorted(contents.items())
        ],
    }
    inventory.write_text(
        yaml.safe_dump(inventory_document, sort_keys=False), encoding="utf-8"
    )
    deny_inventory = controls / "deny.yaml"
    deny_document = {
        "schema_version": 1,
        "inventory_id": "synthetic-private-deny-inventory",
        "coverage_status": "reviewed-complete-for-scope",
        "source_scope_sha256": "a" * 64,
        "rules": [{"id": "deny-001", "kind": "exact-text", "value": deny_value}],
    }
    deny_inventory.write_text(
        yaml.safe_dump(deny_document, sort_keys=False), encoding="utf-8"
    )
    policy_document = _policy_document(**(policy_updates or {}))
    policy_document["approved_inventory_sha256"] = sha256(inventory.read_bytes()).hexdigest()
    policy_document["deny_inventory_sha256"] = sha256(deny_inventory.read_bytes()).hexdigest()
    policy = controls / "policy.yaml"
    policy.write_text(
        yaml.safe_dump(policy_document, sort_keys=False),
        encoding="utf-8",
    )
    return PreparedCheck(
        candidate=candidate,
        policy=policy,
        inventory=inventory,
        deny_inventory=deny_inventory,
        reports=reports,
    )


def _prepare(
    root: Path,
    *,
    contents: dict[str, bytes] | None = None,
    policy_updates: dict[str, object] | None = None,
    deny_value: str = SAFE_DENY_VALUE,
    git_modes: dict[str, str] | None = None,
) -> PreparedCheck:
    candidate_contents = contents or _base_candidate_contents()
    candidate = root / "candidate"
    _write_candidate(candidate, candidate_contents)
    return _write_controls(
        root,
        candidate=candidate,
        contents=candidate_contents,
        policy_updates=policy_updates,
        deny_value=deny_value,
        git_modes=git_modes,
    )


def _run_staging(prepared: PreparedCheck, report_name: str = "report.json"):
    return run_public_release_check(
        PublicReleaseMode.STAGING_FILESYSTEM,
        prepared.candidate,
        policy_path=prepared.policy,
        approved_inventory_path=prepared.inventory,
        deny_inventory_path=prepared.deny_inventory,
        report_path=prepared.reports / report_name,
    )


def _git_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
    )
    return environment


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        env=_git_environment(),
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return completed.stdout.strip()


def _initialize_git_repository(root: Path) -> str:
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Synthetic Test Author")
    _git(root, "config", "user.email", "synthetic-test@example.invalid")
    _git(root, "config", "core.autocrlf", "false")
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "--no-gpg-sign", "-m", "Synthetic root")
    return _git(root, "rev-parse", "HEAD")


def _run_committed(
    prepared: PreparedCheck,
    commit: str,
    report_name: str = "report.json",
):
    return run_public_release_check(
        PublicReleaseMode.COMMITTED_TREE,
        prepared.candidate,
        policy_path=prepared.policy,
        approved_inventory_path=prepared.inventory,
        deny_inventory_path=prepared.deny_inventory,
        report_path=prepared.reports / report_name,
        commit=commit,
    )


def _hard_link_or_skip(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except OSError as error:
        unsupported_errnos = {
            value
            for value in (
                getattr(errno, "EACCES", None),
                getattr(errno, "ENOSYS", None),
                getattr(errno, "ENOTSUP", None),
                getattr(errno, "EOPNOTSUPP", None),
                getattr(errno, "EPERM", None),
            )
            if value is not None
        }
        unsupported_winerrors = {1, 5, 50}
        winerror = getattr(error, "winerror", None)
        if error.errno in unsupported_errnos or winerror in unsupported_winerrors:
            pytest.skip(
                "hard links are unavailable on the pytest temporary filesystem "
                f"(errno={error.errno}, winerror={winerror})"
            )
        raise
    link_count = os.lstat(source).st_nlink
    if link_count < 2:
        pytest.skip(
            "hard-link creation succeeded but the pytest temporary filesystem did not expose "
            f"a usable link count (st_nlink={link_count})"
        )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("placeholder", "unresolved placeholder"),
        ("unknown", "additionalProperties"),
        ("duplicate-yaml", "valid strict YAML or JSON"),
    ],
)
def test_policy_loading_is_strict(
    tmp_path: Path,
    mutation: str,
    expected: str,
) -> None:
    prepared = _prepare(tmp_path)
    document = yaml.safe_load(prepared.policy.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    if mutation == "placeholder":
        decisions = document["decisions"]
        assert isinstance(decisions, dict)
        decisions["repository_name"] = "replace-with-repository-name"
        prepared.policy.write_text(
            yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
        )
    elif mutation == "unknown":
        document["unexpected_policy_field"] = True
        prepared.policy.write_text(
            yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
        )
    else:
        prepared.policy.write_text(
            prepared.policy.read_text(encoding="utf-8") + "schema_version: 1\n",
            encoding="utf-8",
        )

    with pytest.raises(ValueError, match=expected):
        load_policy(prepared.policy)


def test_staging_filesystem_passes_with_an_approved_hidden_file(tmp_path: Path) -> None:
    contents = _base_candidate_contents()
    contents[".approved-hidden"] = b"Reviewed hidden configuration.\n"
    prepared = _prepare(tmp_path, contents=contents)

    report = _run_staging(prepared)

    assert report.outcome == "pass"
    assert report.candidate_stable is True
    assert report.clean_state == "stable-exact-filesystem-snapshot"
    assert report.usage["files"] == len(contents)
    assert report.findings == ()
    assert report.human_review_required is True
    assert report.publication_authorized is False
    written = json.loads((prepared.reports / "report.json").read_text(encoding="utf-8"))
    assert written["outcome"] == "pass"
    assert written["publication_authorized"] is False
    assert written["decisions"]["code_of_conduct_path"] == "PUBLIC-GOVERNANCE.md"


def test_staging_filesystem_rejects_candidate_file_hard_linked_outside(
    tmp_path: Path,
) -> None:
    prepared = _prepare(tmp_path)
    candidate_file = prepared.candidate / "README.md"
    outside_link = tmp_path / "outside-candidate-hard-link"
    _hard_link_or_skip(candidate_file, outside_link)

    report = _run_staging(prepared)

    assert report.outcome == "fail"
    assert [finding.code for finding in report.findings] == [
        "candidate-hard-linked-file-present"
    ]
    assert report.publication_authorized is False


def test_staging_executable_git_mode_is_platform_aware(tmp_path: Path) -> None:
    prepared = _prepare(tmp_path, git_modes={"README.md": "100755"})
    if os.name != "nt":
        readme = prepared.candidate / "README.md"
        readme.chmod(readme.stat().st_mode | stat.S_IXUSR)

    report = _run_staging(prepared)

    if os.name == "nt":
        assert report.outcome == "fail"
        assert [finding.code for finding in report.findings] == [
            "executable-git-mode-not-verifiable-on-platform"
        ]
    else:
        assert report.outcome == "pass"
        assert report.findings == ()


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX execute-bit mode mapping is not observable on Windows",
)
@pytest.mark.parametrize("execute_bit", [stat.S_IXGRP, stat.S_IXOTH])
def test_staging_any_posix_execute_bit_conflicts_with_approved_non_executable_mode(
    tmp_path: Path,
    execute_bit: int,
) -> None:
    prepared = _prepare(tmp_path)
    readme = prepared.candidate / "README.md"
    original_mode = stat.S_IMODE(readme.stat().st_mode)
    execute_mask = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    readme.chmod((original_mode & ~execute_mask) | execute_bit)

    try:
        report = _run_staging(prepared)
    finally:
        readme.chmod(original_mode)

    assert report.outcome == "fail"
    assert [finding.code for finding in report.findings] == [
        "approved-git-mode-mismatch"
    ]


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("extra", "unexpected-candidate-file"),
        ("missing", "missing-candidate-file"),
        ("modified", "approved-hash-mismatch"),
    ],
)
def test_staging_filesystem_fails_on_inventory_drift(
    tmp_path: Path,
    mutation: str,
    expected_code: str,
) -> None:
    prepared = _prepare(tmp_path)
    readme = prepared.candidate / "README.md"
    if mutation == "extra":
        (prepared.candidate / ".unapproved-hidden").write_text("extra\n", encoding="utf-8")
    elif mutation == "missing":
        readme.unlink()
    else:
        original = readme.read_bytes()
        readme.write_bytes(bytes([original[0] ^ 1]) + original[1:])

    report = _run_staging(prepared)

    assert report.outcome == "fail"
    assert [finding.code for finding in report.findings] == [expected_code]
    assert report.publication_authorized is False


def test_report_must_be_outside_candidate(tmp_path: Path) -> None:
    prepared = _prepare(tmp_path)

    with pytest.raises(ValueError, match="outside the candidate"):
        run_public_release_check(
            PublicReleaseMode.STAGING_FILESYSTEM,
            prepared.candidate,
            policy_path=prepared.policy,
            approved_inventory_path=prepared.inventory,
            deny_inventory_path=prepared.deny_inventory,
            report_path=prepared.candidate / "report.json",
        )


def test_report_is_never_overwritten(tmp_path: Path) -> None:
    prepared = _prepare(tmp_path)
    report_path = prepared.reports / "existing.json"
    report_path.write_text("preserve me", encoding="utf-8")

    with pytest.raises(ValueError, match="overwrite"):
        run_public_release_check(
            PublicReleaseMode.STAGING_FILESYSTEM,
            prepared.candidate,
            policy_path=prepared.policy,
            approved_inventory_path=prepared.inventory,
            deny_inventory_path=prepared.deny_inventory,
            report_path=report_path,
        )

    assert report_path.read_text(encoding="utf-8") == "preserve me"


def test_policy_bound_control_hash_rejects_inventory_substitution(tmp_path: Path) -> None:
    prepared = _prepare(tmp_path)
    prepared.inventory.write_text(
        prepared.inventory.read_text(encoding="utf-8") + "# changed after policy approval\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="policy-bound SHA-256"):
        _run_staging(prepared)

    assert not (prepared.reports / "report.json").exists()


def test_private_deny_match_is_reported_without_echoing_the_value(tmp_path: Path) -> None:
    private_value = "PRIVATE-SUBJECT-VALUE-MUST-NOT-ECHO"
    contents = _base_candidate_contents(readme=f"Unsafe literal: {private_value}\n")
    prepared = _prepare(tmp_path, contents=contents, deny_value=private_value)

    report = _run_staging(prepared)

    assert report.outcome == "fail"
    assert any(
        finding.code == "denied-text" and finding.rule_id == "deny-001"
        for finding in report.findings
    )
    rendered = json.dumps(report.as_dict()) + (prepared.reports / "report.json").read_text(
        encoding="utf-8"
    )
    assert private_value not in rendered


def test_unapproved_email_like_value_is_a_finding(tmp_path: Path) -> None:
    email = "unreviewed-person@" + "public.example"
    contents = _base_candidate_contents(readme=f"Contact {email}.\n")
    prepared = _prepare(tmp_path, contents=contents)

    report = _run_staging(prepared)

    assert report.outcome == "fail"
    assert any(finding.code == "unapproved-email-like-value" for finding in report.findings)


def _npi_checksum_is_valid(value: str) -> bool:
    digits = [int(character) for character in "80840" + value]
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2:
            digit *= 2
        total += digit // 10 + digit % 10
    return total % 10 == 0


@pytest.mark.parametrize(
    ("identity_value", "expected_code"),
    [
        ("12345" + "67893", "ten-digit-npi-like-value-rejected"),
        ("SYNTHETIC-" + "UNREGISTERED", "unregistered-identity-like-value"),
    ],
)
def test_unregistered_identity_like_values_are_findings(
    tmp_path: Path,
    identity_value: str,
    expected_code: str,
) -> None:
    if identity_value.isdecimal():
        assert _npi_checksum_is_valid(identity_value)
    contents = _base_candidate_contents(readme=f"Identity: {identity_value}\n")
    prepared = _prepare(tmp_path, contents=contents)

    report = _run_staging(prepared)

    assert report.outcome == "fail"
    assert any(finding.code == expected_code for finding in report.findings)


@pytest.mark.parametrize(
    ("markdown", "expected_code"),
    [
        ("[missing](docs/missing.md)\n", "missing-local-link-target"),
        ("[escape](../outside.md)\n", "invalid-or-escaping-local-link"),
    ],
)
def test_markdown_broken_and_escaping_links_are_findings(
    tmp_path: Path,
    markdown: str,
    expected_code: str,
) -> None:
    prepared = _prepare(tmp_path, contents=_base_candidate_contents(readme=markdown))

    report = _run_staging(prepared)

    assert report.outcome == "fail"
    assert any(finding.code == expected_code for finding in report.findings)


@pytest.mark.parametrize(
    "target",
    [
        "data:text/plain,synthetic",
        "file:" + "///tmp/synthetic.txt",
        "/" + "/public.example/synthetic",
        "custom-scheme:synthetic",
    ],
)
def test_markdown_rejects_non_allowlisted_and_scheme_relative_links(
    tmp_path: Path,
    target: str,
) -> None:
    prepared = _prepare(
        tmp_path,
        contents=_base_candidate_contents(readme=f"[target]({target})\n"),
    )

    report = _run_staging(prepared)

    assert report.outcome == "fail"
    assert any(
        finding.check == "local-links"
        and finding.code == "invalid-or-escaping-local-link"
        for finding in report.findings
    )


def test_markdown_allows_http_https_and_approved_mailto_links(tmp_path: Path) -> None:
    markdown = "\n".join(
        [
            "[http](http://public.example/synthetic)",
            "[https](https://public.example/synthetic)",
            "[mail](mailto:synthetic-test@example.invalid)",
            "",
        ]
    )
    prepared = _prepare(
        tmp_path,
        contents=_base_candidate_contents(readme=markdown),
    )

    report = _run_staging(prepared)

    assert report.outcome == "pass"
    assert not any(finding.check == "local-links" for finding in report.findings)


@pytest.mark.parametrize(
    ("signature", "expected_code"),
    [
        (b"!<arch>\nsynthetic archive bytes", "archive-content-rejected"),
        (b"%PDF-1.7\nsynthetic document bytes", "opaque-or-binary-content-rejected"),
    ],
)
def test_archive_and_opaque_document_signatures_are_rejected(
    tmp_path: Path,
    signature: bytes,
    expected_code: str,
) -> None:
    contents = _base_candidate_contents()
    contents["README.md"] = signature
    prepared = _prepare(tmp_path, contents=contents)

    report = _run_staging(prepared)

    assert report.outcome == "fail"
    assert any(finding.code == expected_code for finding in report.findings)


@pytest.mark.parametrize(
    ("secret", "expected_rule_id"),
    [
        ("github_pat_" + "A" * 24, "github-fine-grained-token"),
        ("ASIA" + "A1" * 8, "aws-temporary-access-key"),
        ("-----BEGIN ENCRYPTED " + "PRIVATE KEY-----", "private-key-marker"),
    ],
)
def test_additional_high_confidence_secret_markers_are_detected(
    tmp_path: Path,
    secret: str,
    expected_rule_id: str,
) -> None:
    prepared = _prepare(
        tmp_path,
        contents=_base_candidate_contents(readme=f"Credential: {secret}\n"),
    )

    report = _run_staging(prepared)

    assert report.outcome == "fail"
    assert any(
        finding.check == "secrets"
        and finding.code == "high-confidence-secret-pattern"
        and finding.rule_id == expected_rule_id
        for finding in report.findings
    )
    assert secret not in json.dumps(report.as_dict())


@pytest.mark.parametrize(
    "absolute_path",
    ["C:" + "\\private", "/" + "root", "\\\\" + "server\\share"],
)
def test_private_absolute_path_forms_are_detected(
    tmp_path: Path,
    absolute_path: str,
) -> None:
    prepared = _prepare(
        tmp_path,
        contents=_base_candidate_contents(readme=f"Local path: {absolute_path}\n"),
    )

    report = _run_staging(prepared)

    assert report.outcome == "fail"
    assert any(
        finding.check == "private-paths"
        and finding.code == "absolute-user-or-unc-path"
        for finding in report.findings
    )


@pytest.mark.parametrize(
    "public_path",
    [
        "/api/v1/resources",
        "/docs/getting-started.md",
        "/$defs/example",
    ],
)
def test_public_route_and_repository_paths_are_not_local_path_findings(
    tmp_path: Path,
    public_path: str,
) -> None:
    prepared = _prepare(
        tmp_path,
        contents=_base_candidate_contents(readme=f"Public path: {public_path}\n"),
    )

    report = _run_staging(prepared)

    assert report.outcome == "pass"
    assert not any(finding.check == "private-paths" for finding in report.findings)


def test_npi_prefixed_by_letters_is_still_rejected(tmp_path: Path) -> None:
    valid_npi = "12345" + "67893"
    prepared = _prepare(
        tmp_path,
        contents=_base_candidate_contents(readme=f"Identifier: NPI{valid_npi}\n"),
    )

    report = _run_staging(prepared)

    assert report.outcome == "fail"
    assert any(
        finding.check == "synthetic-registry"
        and finding.code == "ten-digit-npi-like-value-rejected"
        for finding in report.findings
    )


def test_invalid_npi_format_example_is_not_an_identity_finding(tmp_path: Path) -> None:
    invalid_npi = "12345" + "67890"
    prepared = _prepare(
        tmp_path,
        contents=_base_candidate_contents(readme=f"Invalid format fixture: {invalid_npi}\n"),
    )

    report = _run_staging(prepared)

    assert report.outcome == "pass"
    assert not any(
        finding.code == "ten-digit-npi-like-value-rejected" for finding in report.findings
    )


def test_npi_like_digits_inside_a_long_hex_artifact_are_ignored(tmp_path: Path) -> None:
    valid_npi = "12345" + "67893"
    artifact_id = "a" * 15 + valid_npi + "b" * 35
    prepared = _prepare(
        tmp_path,
        contents=_base_candidate_contents(
            readme=f"Artifact: https://public.example/packages/{artifact_id}/fixture\n"
        ),
    )

    report = _run_staging(prepared)

    assert report.outcome == "pass"
    assert not any(
        finding.code == "ten-digit-npi-like-value-rejected" for finding in report.findings
    )


@pytest.mark.parametrize(
    "contradiction",
    [
        "unstable-pass",
        "null-observed-hash",
        "pass-with-finding",
        "approved-inventory-binding",
        "deny-inventory-binding",
        "unexpected-subject-key",
        "unexpected-bounds-key",
        "unexpected-usage-key",
    ],
)
def test_report_contract_rejects_contradictory_pass_documents(
    tmp_path: Path,
    contradiction: str,
) -> None:
    prepared = _prepare(tmp_path)
    document = _run_staging(prepared).as_dict()
    validate_report_document(document)

    if contradiction == "unstable-pass":
        document["candidate_stable"] = False
    elif contradiction == "null-observed-hash":
        document["observed_inventory_sha256"] = None
    elif contradiction == "pass-with-finding":
        document["findings"] = [
            {
                "check": "candidate-read",
                "code": "synthetic-finding",
                "path": None,
                "rule_id": None,
            }
        ]
    elif contradiction == "approved-inventory-binding":
        document["expected_approved_inventory_sha256"] = "0" * 64
    elif contradiction == "deny-inventory-binding":
        document["expected_deny_inventory_sha256"] = "0" * 64
    elif contradiction == "unexpected-subject-key":
        document["subject"]["unexpected"] = True
    elif contradiction == "unexpected-bounds-key":
        document["configured_bounds"]["unexpected"] = 0
    else:
        document["usage"]["unexpected"] = 0

    with pytest.raises(ValueError):
        validate_report_document(document)


def test_committed_tree_passes_for_exact_root_commit(tmp_path: Path) -> None:
    contents = _base_candidate_contents()
    contents[".approved-hidden"] = b"Reviewed hidden configuration.\n"
    prepared = _prepare(tmp_path, contents=contents)
    commit = _initialize_git_repository(prepared.candidate)

    report = _run_committed(prepared, commit)

    assert len(commit) == 40
    assert report.outcome == "pass"
    assert report.subject["commit"] == commit
    assert report.subject["root_commit_required"] is True
    assert report.clean_state == "clean-worktree"
    assert report.history_checked is False


def test_committed_tree_detects_mode_only_change(tmp_path: Path) -> None:
    contents = _base_candidate_contents()
    candidate = tmp_path / "candidate"
    _write_candidate(candidate, contents)
    root_commit = _initialize_git_repository(candidate)
    _git(candidate, "update-index", "--chmod=+x", "--", "README.md")
    _git(candidate, "commit", "--quiet", "--no-gpg-sign", "-m", "Change mode only")
    mode_commit = _git(candidate, "rev-parse", "HEAD")
    assert _git(candidate, "ls-tree", mode_commit, "README.md").startswith("100755 blob ")
    prepared = _write_controls(
        tmp_path / "control-set",
        candidate=candidate,
        contents=contents,
        policy_updates={
            "require_root_commit": False,
            "approved_parent_commits": [root_commit],
        },
    )

    report = _run_committed(prepared, mode_commit)

    assert report.outcome == "fail"
    assert [finding.code for finding in report.findings] == ["approved-git-mode-mismatch"]


def test_committed_tree_enforces_the_policy_bound_commit_identity(tmp_path: Path) -> None:
    prepared = _prepare(tmp_path)
    commit = _initialize_git_repository(prepared.candidate)
    policy_document = yaml.safe_load(prepared.policy.read_text(encoding="utf-8"))
    assert isinstance(policy_document, dict)
    decisions = policy_document["decisions"]
    assert isinstance(decisions, dict)
    decisions["commit_author_name"] = "Different Public Author"
    prepared.policy.write_text(
        yaml.safe_dump(policy_document, sort_keys=False), encoding="utf-8"
    )

    report = _run_committed(prepared, commit)

    assert report.outcome == "fail"
    assert any(
        finding.code == "commit-identity-does-not-match-policy"
        for finding in report.findings
    )


def test_committed_tree_rejects_hard_linked_loose_git_object_without_path_leak(
    tmp_path: Path,
) -> None:
    prepared = _prepare(tmp_path)
    commit = _initialize_git_repository(prepared.candidate)
    object_root = prepared.candidate / ".git" / "objects"
    loose_objects = sorted(
        path
        for directory in object_root.iterdir()
        if directory.is_dir() and len(directory.name) == 2
        for path in directory.iterdir()
        if path.is_file()
    )
    assert loose_objects, "synthetic Git repository should contain loose objects"
    outside_link = tmp_path / "outside-object-store-hard-link"
    _hard_link_or_skip(loose_objects[0], outside_link)

    report = _run_committed(prepared, commit)

    assert report.outcome == "fail"
    assert [finding.code for finding in report.findings] == [
        "git-object-store-linked-file-present"
    ]
    rendered = json.dumps(report.as_dict()) + (prepared.reports / "report.json").read_text(
        encoding="utf-8"
    )
    assert str(outside_link) not in rendered
    assert outside_link.name not in rendered


def test_committed_tree_rejects_external_common_directory_without_path_leak(
    tmp_path: Path,
) -> None:
    prepared = _prepare(tmp_path)
    commit = _initialize_git_repository(prepared.candidate)
    external_common = tmp_path / "outside-git-common-directory"
    external_common.mkdir()
    git_directory = prepared.candidate / ".git"
    (git_directory / "commondir").write_text(
        os.path.relpath(external_common, git_directory) + "\n",
        encoding="utf-8",
    )

    report = _run_committed(prepared, commit)

    assert report.outcome == "fail"
    assert [finding.code for finding in report.findings] == [
        "git-linked-worktree-or-common-directory-present"
    ]
    assert report.findings[0].path is None
    rendered = json.dumps(report.as_dict()) + (prepared.reports / "report.json").read_text(
        encoding="utf-8"
    )
    assert str(external_common) not in rendered
    assert external_common.name not in rendered


def test_committed_tree_terminal_check_detects_late_alternates_without_path_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepare(tmp_path)
    commit = _initialize_git_repository(prepared.candidate)
    external_objects = tmp_path / "outside-late-alternate-objects"
    external_objects.mkdir()
    alternates = prepared.candidate / ".git" / "objects" / "info" / "alternates"
    original_run_git = git_tree._run_git_bounded
    injected = False

    def run_git_and_inject_alternates(
        git: str,
        arguments: list[str],
        **kwargs: object,
    ) -> bytes:
        nonlocal injected
        output = original_run_git(git, arguments, **kwargs)
        if arguments == ["cat-file", "--batch"] and not injected:
            alternates.write_text(str(external_objects) + "\n", encoding="utf-8")
            injected = True
        return output

    monkeypatch.setattr(git_tree, "_run_git_bounded", run_git_and_inject_alternates)

    report = _run_committed(prepared, commit)

    assert injected is True
    assert report.outcome == "fail"
    assert [finding.code for finding in report.findings] == [
        "git-external-or-rewritten-objects-configured"
    ]
    assert report.findings[0].path is None
    rendered = json.dumps(report.as_dict()) + (prepared.reports / "report.json").read_text(
        encoding="utf-8"
    )
    assert str(external_objects) not in rendered
    assert external_objects.name not in rendered


def test_committed_tree_terminal_check_detects_late_replace_ref_without_leakage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepare(tmp_path)
    commit = _initialize_git_repository(prepared.candidate)
    replace_ref = prepared.candidate / ".git" / "refs" / "replace" / commit
    original_run_git = git_tree._run_git_bounded
    injected = False
    terminal_replace_ref_scan_reached = False

    def run_git_and_inject_replace_ref(
        git: str,
        arguments: list[str],
        **kwargs: object,
    ) -> bytes:
        nonlocal injected, terminal_replace_ref_scan_reached
        output = original_run_git(git, arguments, **kwargs)
        if arguments == ["cat-file", "--batch"] and not injected:
            replace_ref.parent.mkdir(parents=True)
            replace_ref.write_text(commit + "\n", encoding="ascii")
            injected = True
        elif (
            arguments
            == ["for-each-ref", "--format=%(refname)", "refs/replace"]
            and injected
        ):
            terminal_replace_ref_scan_reached = True
        return output

    monkeypatch.setattr(git_tree, "_run_git_bounded", run_git_and_inject_replace_ref)

    report = _run_committed(prepared, commit)

    assert injected is True
    assert terminal_replace_ref_scan_reached is True
    assert report.outcome == "fail"
    assert [finding.code for finding in report.findings] == ["git-replace-refs-present"]
    assert report.findings[0].path is None
    assert report.findings[0].rule_id is None
    rendered = json.dumps(report.as_dict()) + (prepared.reports / "report.json").read_text(
        encoding="utf-8"
    )
    assert str(replace_ref) not in rendered
    assert f"refs/replace/{commit}" not in rendered


def test_committed_tree_terminal_check_detects_loose_object_metadata_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepare(tmp_path)
    commit = _initialize_git_repository(prepared.candidate)
    object_root = prepared.candidate / ".git" / "objects"
    loose_objects = sorted(
        path
        for directory in object_root.iterdir()
        if directory.is_dir() and len(directory.name) == 2
        for path in directory.iterdir()
        if path.is_file()
    )
    assert loose_objects, "synthetic Git repository should contain loose objects"
    loose_object = loose_objects[0]
    original_run_git = git_tree._run_git_bounded
    injected = False

    def run_git_and_touch_object(
        git: str,
        arguments: list[str],
        **kwargs: object,
    ) -> bytes:
        nonlocal injected
        output = original_run_git(git, arguments, **kwargs)
        if arguments == ["cat-file", "--batch"] and not injected:
            before = os.lstat(loose_object)
            shifted_mtime_ns = before.st_mtime_ns + 4_000_000_000
            os.utime(
                loose_object,
                ns=(before.st_atime_ns, shifted_mtime_ns),
            )
            after = os.lstat(loose_object)
            assert after.st_mtime_ns != before.st_mtime_ns
            injected = True
        return output

    monkeypatch.setattr(git_tree, "_run_git_bounded", run_git_and_touch_object)

    report = _run_committed(prepared, commit)

    assert injected is True
    assert report.outcome == "incomplete"
    assert [finding.code for finding in report.findings] == [
        "git-object-store-changed-during-scan"
    ]
    assert report.findings[0].path is None


@pytest.mark.parametrize(
    ("require_clean", "expected_outcome", "expected_code", "expected_clean_state"),
    [
        (False, "pass", None, "not-required-by-policy"),
        (True, "fail", "Git-worktree-not-clean", "not-established"),
    ],
)
def test_committed_tree_dirty_worktree_semantics(
    tmp_path: Path,
    require_clean: bool,
    expected_outcome: str,
    expected_code: str | None,
    expected_clean_state: str,
) -> None:
    contents = _base_candidate_contents()
    candidate = tmp_path / "candidate"
    _write_candidate(candidate, contents)
    commit = _initialize_git_repository(candidate)
    (candidate / "README.md").write_text("Dirty worktree bytes.\n", encoding="utf-8")
    (candidate / "untracked.txt").write_text("Untracked.\n", encoding="utf-8")
    prepared = _write_controls(
        tmp_path / "control-set",
        candidate=candidate,
        contents=contents,
        policy_updates={"require_clean_worktree": require_clean},
    )

    report = _run_committed(prepared, commit)

    assert report.outcome == expected_outcome
    assert report.clean_state == expected_clean_state
    if expected_code is None:
        assert report.findings == ()
    else:
        assert [finding.code for finding in report.findings] == [expected_code]


@pytest.mark.parametrize("commit", ["HEAD", "--help"])
def test_committed_tree_rejects_non_full_and_option_like_commit_values(
    tmp_path: Path,
    commit: str,
) -> None:
    prepared = _prepare(tmp_path)
    _initialize_git_repository(prepared.candidate)

    report = _run_committed(prepared, commit)

    assert report.outcome == "fail"
    assert [finding.code for finding in report.findings] == [
        "commit-must-be-full-lowercase-object-id"
    ]


def test_committed_tree_root_commit_gate_rejects_a_descendant(tmp_path: Path) -> None:
    contents = _base_candidate_contents()
    candidate = tmp_path / "candidate"
    _write_candidate(candidate, contents)
    _initialize_git_repository(candidate)
    updated_contents = deepcopy(contents)
    updated_contents["README.md"] = b"Second synthetic revision.\n"
    (candidate / "README.md").write_bytes(updated_contents["README.md"])
    _git(candidate, "add", "README.md")
    _git(candidate, "commit", "--quiet", "--no-gpg-sign", "-m", "Second revision")
    descendant = _git(candidate, "rev-parse", "HEAD")
    prepared = _write_controls(
        tmp_path / "control-set",
        candidate=candidate,
        contents=updated_contents,
        policy_updates={"require_root_commit": True},
    )

    report = _run_committed(prepared, descendant)

    assert report.outcome == "fail"
    assert [finding.code for finding in report.findings] == ["candidate-commit-is-not-root"]


@pytest.mark.parametrize(
    ("make_failure", "expected_exit", "expected_word"),
    [(False, 0, "PASS"), (True, 1, "FAIL")],
)
def test_public_release_cli_exit_and_manual_review_disclaimer(
    tmp_path: Path,
    make_failure: bool,
    expected_exit: int,
    expected_word: str,
) -> None:
    prepared = _prepare(tmp_path)
    if make_failure:
        (prepared.candidate / "unexpected.txt").write_text("extra\n", encoding="utf-8")
    report_path = prepared.reports / "cli-report.json"

    result = CliRunner().invoke(
        cli.app,
        [
            "public-release-check",
            "staging-filesystem",
            str(prepared.candidate),
            "--policy",
            str(prepared.policy),
            "--approved-inventory",
            str(prepared.inventory),
            "--deny-inventory",
            str(prepared.deny_inventory),
            "--report",
            str(report_path),
        ],
    )

    assert result.exit_code == expected_exit, result.output
    assert expected_word in result.output
    assert "Human review is still required" in result.output
    assert "does not authorize publication" in result.output
    assert report_path.is_file()
