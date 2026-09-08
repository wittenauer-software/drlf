from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Literal


class PublicReleaseMode(StrEnum):
    """Supported candidate views. Neither mode inspects repository history."""

    STAGING_FILESYSTEM = "staging-filesystem"
    COMMITTED_TREE = "committed-tree"


Outcome = Literal["pass", "fail", "incomplete"]


@dataclass(frozen=True)
class ScanBounds:
    max_paths: int
    max_path_bytes: int
    max_file_bytes: int
    max_total_bytes: int
    max_runtime_seconds: int
    max_git_output_bytes: int
    max_git_objects: int
    max_structured_bytes: int
    max_deny_rules: int
    max_findings: int


@dataclass(frozen=True)
class ReleaseDecisions:
    repository_owner: str
    repository_name: str
    package_name: str
    license_spdx: str
    license_path: str
    contribution_policy_path: str
    code_of_conduct_path: str
    governance_path: str
    security_path: str
    support_path: str
    commit_author_name: str
    commit_author_email: str
    commit_committer_name: str
    commit_committer_email: str
    approved_public_contacts: tuple[str, ...]


@dataclass(frozen=True)
class PublicReleasePolicy:
    schema_version: int
    policy_id: str
    policy_version: str
    bounds: ScanBounds
    decisions: ReleaseDecisions
    required_paths: tuple[str, ...]
    forbidden_path_prefixes: tuple[str, ...]
    synthetic_registry_path: str
    check_markdown_links: bool
    require_clean_worktree: bool
    require_root_commit: bool
    approved_parent_commits: tuple[str, ...]
    approved_inventory_sha256: str
    deny_inventory_sha256: str


@dataclass(frozen=True)
class ApprovedFile:
    path: str
    bytes: int
    sha256: str
    git_mode: str
    classification: str
    content_type: str


@dataclass(frozen=True)
class ApprovedInventory:
    schema_version: int
    inventory_id: str
    files: tuple[ApprovedFile, ...]


@dataclass(frozen=True)
class DenyRule:
    rule_id: str
    kind: str
    value: str


@dataclass(frozen=True)
class DenyInventory:
    schema_version: int
    inventory_id: str
    coverage_status: str
    source_scope_sha256: str
    rules: tuple[DenyRule, ...]


@dataclass(frozen=True)
class SyntheticIdentity:
    value: str
    identity_type: str
    purpose: str
    source_class: str
    validation_rule: str


@dataclass(frozen=True)
class SyntheticRegistry:
    registry_version: int
    identities: tuple[SyntheticIdentity, ...]


@dataclass(frozen=True)
class FileObservation:
    path: str
    bytes: int
    sha256: str
    git_mode: str


@dataclass(frozen=True)
class CandidateSnapshot:
    observations: tuple[FileObservation, ...]
    contents: dict[str, bytes]
    subject: dict[str, str | bool | int | None]
    stable: bool
    clean_state: str
    usage: dict[str, int]
    commit_metadata: bytes | None = None
    commit_message: bytes | None = None
    commit_identity: bytes | None = None


@dataclass(frozen=True)
class Finding:
    check: str
    code: str
    path: str | None = None
    rule_id: str | None = None


@dataclass(frozen=True)
class PublicReleaseReport:
    schema_version: int
    checker_version: str
    mode: str
    subject: dict[str, str | bool | int | None]
    outcome: Outcome
    policy_id: str
    policy_version: str
    policy_sha256: str
    decisions: dict[str, str | list[str]]
    expected_approved_inventory_sha256: str
    expected_deny_inventory_sha256: str
    control_bindings_verified: bool
    approved_inventory_id: str
    approved_inventory_sha256: str
    deny_inventory_id: str
    deny_inventory_sha256: str
    observed_inventory_sha256: str | None
    candidate_stable: bool
    clean_state: str
    history_checked: bool
    archive_inspection: str
    network_checks: str
    configured_bounds: dict[str, int]
    usage: dict[str, int]
    scope_exclusions: tuple[str, ...]
    findings: tuple[Finding, ...]
    human_review_required: bool
    publication_authorized: bool

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["findings"] = [asdict(finding) for finding in self.findings]
        result["scope_exclusions"] = list(self.scope_exclusions)
        return result
