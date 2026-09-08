from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

from drlf.public_release.models import (
    ApprovedFile,
    ApprovedInventory,
    DenyInventory,
    DenyRule,
    PublicReleasePolicy,
    ReleaseDecisions,
    ScanBounds,
    SyntheticIdentity,
    SyntheticRegistry,
)

CONTROL_FILE_HARD_CAP = 2 * 1024 * 1024
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_PLACEHOLDER_WORDS = {
    "changeme",
    "placeholder",
    "replace",
    "tbd",
    "todo",
    "unknown",
}


class _StrictSafeLoader(yaml.SafeLoader):
    def compose_node(self, parent: object, index: object) -> yaml.Node:
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("aliases are not permitted")
        return super().compose_node(parent, index)

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        if not isinstance(node, yaml.MappingNode):
            raise yaml.YAMLError("expected a mapping")
        result: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in result
            except TypeError as error:
                raise yaml.YAMLError("mapping keys must be scalar") from error
            if duplicate:
                raise yaml.YAMLError("duplicate mapping keys are not permitted")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


@dataclass(frozen=True)
class LoadedControlDocument:
    document: dict[str, Any]
    sha256: str


def _bounded_bytes(path: Path, byte_cap: int, label: str) -> bytes:
    if byte_cap < 1 or byte_cap > CONTROL_FILE_HARD_CAP:
        raise ValueError(f"invalid {label} byte cap")
    try:
        before = path.stat()
    except OSError as error:
        raise ValueError(f"unreadable {label} ({type(error).__name__})") from error
    if not path.is_file():
        raise ValueError(f"{label} must be a regular file")
    if before.st_size > byte_cap:
        raise ValueError(f"{label} exceeds its configured byte limit")
    try:
        with path.open("rb") as handle:
            content = handle.read(byte_cap + 1)
    except OSError as error:
        raise ValueError(f"unreadable {label} ({type(error).__name__})") from error
    if len(content) > byte_cap:
        raise ValueError(f"{label} exceeds its configured byte limit")
    try:
        after = path.stat()
    except OSError as error:
        raise ValueError(f"unstable {label} ({type(error).__name__})") from error
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(content) != before.st_size
    ):
        raise ValueError(f"{label} changed while it was read")
    return content


def _reject_duplicate_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON mapping keys are not permitted")
        result[key] = value
    return result


def _parse_document_bytes(content: bytes, suffix: str, label: str) -> dict[str, Any]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} must be UTF-8") from error
    try:
        if suffix.lower() == ".json":
            document = json.loads(text, object_pairs_hook=_reject_duplicate_json_pairs)
        elif suffix.lower() in {".yaml", ".yml"}:
            document = yaml.load(text, Loader=_StrictSafeLoader)
        else:
            raise ValueError(f"{label} must use a .json, .yaml, or .yml suffix")
    except (json.JSONDecodeError, yaml.YAMLError, UnicodeError) as error:
        raise ValueError(
            f"{label} is not valid strict YAML or JSON ({type(error).__name__})"
        ) from error
    if not isinstance(document, dict):
        raise ValueError(f"{label} must contain a mapping")
    return document


def _schema(name: str) -> Draft202012Validator:
    schema_bytes = (
        files("drlf.public_release.schemas").joinpath(name).read_bytes()
    )
    try:
        schema = json.loads(schema_bytes)
        Draft202012Validator.check_schema(schema)
    except (json.JSONDecodeError, SchemaError) as error:  # pragma: no cover - packaging failure
        raise RuntimeError(f"bundled checker schema is invalid ({name})") from error
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _validate(document: dict[str, Any], schema_name: str, label: str) -> None:
    try:
        _schema(schema_name).validate(document)
    except ValidationError as error:
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        # Do not include error.message because it can repeat a private deny value.
        raise ValueError(
            f"{label} fails schema at {location} ({error.validator})"
        ) from error


def validate_report_document(document: dict[str, Any]) -> None:
    _validate(document, "public-release-report.schema.json", "public-release report")
    if (
        document["expected_approved_inventory_sha256"]
        != document["approved_inventory_sha256"]
    ):
        raise ValueError(
            "public-release report approved-inventory control binding is inconsistent"
        )
    if document["expected_deny_inventory_sha256"] != document["deny_inventory_sha256"]:
        raise ValueError(
            "public-release report deny-inventory control binding is inconsistent"
        )


def load_control_document(
    path: Path,
    *,
    label: str,
    schema_name: str,
    byte_cap: int = CONTROL_FILE_HARD_CAP,
) -> LoadedControlDocument:
    content = _bounded_bytes(path, byte_cap, label)
    document = _parse_document_bytes(content, path.suffix, label)
    _validate(document, schema_name, label)
    return LoadedControlDocument(document=document, sha256=sha256(content).hexdigest())


def validate_relative_path(value: str, *, max_bytes: int, label: str) -> str:
    if value != unicodedata.normalize("NFC", value):
        raise ValueError(f"{label} is not Unicode NFC")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} is not valid UTF-8") from error
    if len(encoded) > max_bytes:
        raise ValueError(f"{label} exceeds the configured path-byte limit")
    if "\\" in value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{label} contains a forbidden character")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{label} is not a canonical relative path")
    for part in path.parts:
        if len(part.encode("utf-8")) > 255:
            raise ValueError(f"{label} contains an overlong path component")
        if any(character in '<>:"|?*' for character in part):
            raise ValueError(f"{label} contains a Windows-illegal filename character")
        if part.endswith((" ", ".")):
            raise ValueError(f"{label} is not portable to Windows")
        stem = part.split(".", 1)[0].upper()
        if stem in _WINDOWS_RESERVED:
            raise ValueError(f"{label} uses a Windows-reserved name")
    canonical = path.as_posix()
    if canonical != value:
        raise ValueError(f"{label} is not canonical")
    return canonical


def _reject_placeholder(value: str, *, label: str) -> None:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    words = {word for word in re.split(r"[^a-z0-9]+", normalized) if word}
    if value != value.strip() or not normalized or words & _PLACEHOLDER_WORDS:
        raise ValueError(f"{label} contains an unresolved placeholder")


def load_policy(path: Path) -> tuple[PublicReleasePolicy, str]:
    loaded = load_control_document(
        path,
        label="public-release policy",
        schema_name="policy.schema.json",
    )
    document = loaded.document
    bounds = ScanBounds(**document["bounds"])
    if bounds.max_file_bytes > bounds.max_total_bytes:
        raise ValueError("policy max_file_bytes cannot exceed max_total_bytes")
    if bounds.max_structured_bytes > bounds.max_file_bytes:
        raise ValueError("policy max_structured_bytes cannot exceed max_file_bytes")
    minimum_git_output = min(
        41_943_040,
        bounds.max_total_bytes + (bounds.max_git_objects * 160),
    )
    if bounds.max_git_output_bytes < minimum_git_output:
        raise ValueError("policy max_git_output_bytes is too small for the declared Git envelope")

    required_paths = tuple(
        validate_relative_path(
            value, max_bytes=bounds.max_path_bytes, label="required path"
        )
        for value in document["required_paths"]
    )
    forbidden_prefixes = tuple(
        validate_relative_path(
            value, max_bytes=bounds.max_path_bytes, label="forbidden path prefix"
        ).rstrip("/")
        for value in document["forbidden_path_prefixes"]
    )
    registry_path = validate_relative_path(
        document["synthetic_registry_path"],
        max_bytes=bounds.max_path_bytes,
        label="synthetic registry path",
    )
    if registry_path not in required_paths:
        raise ValueError("synthetic_registry_path must also appear in required_paths")
    decision_document = document["decisions"]
    for field in (
        "repository_owner",
        "repository_name",
        "package_name",
        "license_spdx",
        "commit_author_name",
        "commit_author_email",
        "commit_committer_name",
        "commit_committer_email",
    ):
        _reject_placeholder(
            decision_document[field], label=f"policy decision {field}"
        )
    decision_paths = {
        field: validate_relative_path(
            decision_document[field],
            max_bytes=bounds.max_path_bytes,
            label=f"policy decision {field}",
        )
        for field in (
            "license_path",
            "contribution_policy_path",
            "code_of_conduct_path",
            "governance_path",
            "security_path",
            "support_path",
        )
    }
    missing_decision_paths = sorted(set(decision_paths.values()) - set(required_paths))
    if missing_decision_paths:
        raise ValueError("all policy decision document paths must appear in required_paths")
    for field, value in decision_paths.items():
        _reject_placeholder(value, label=f"policy decision {field}")
    for index, value in enumerate(decision_document["approved_public_contacts"]):
        _reject_placeholder(value, label=f"approved public contact {index}")
    decisions = ReleaseDecisions(
        repository_owner=decision_document["repository_owner"],
        repository_name=decision_document["repository_name"],
        package_name=decision_document["package_name"],
        license_spdx=decision_document["license_spdx"],
        **decision_paths,
        commit_author_name=decision_document["commit_author_name"],
        commit_author_email=decision_document["commit_author_email"],
        commit_committer_name=decision_document["commit_committer_name"],
        commit_committer_email=decision_document["commit_committer_email"],
        approved_public_contacts=tuple(decision_document["approved_public_contacts"]),
    )
    approved_contacts = {value.casefold() for value in decisions.approved_public_contacts}
    if decisions.commit_author_email.casefold() not in approved_contacts:
        raise ValueError("commit author email must appear in approved_public_contacts")
    if decisions.commit_committer_email.casefold() not in approved_contacts:
        raise ValueError("commit committer email must appear in approved_public_contacts")
    approved_parents = tuple(document.get("approved_parent_commits", ()))
    if document["require_root_commit"] and approved_parents:
        raise ValueError("root-commit policy cannot also approve parent commits")
    if not document["require_root_commit"] and not approved_parents:
        raise ValueError(
            "non-root committed-tree policy requires explicit approved_parent_commits"
        )
    return (
        PublicReleasePolicy(
            schema_version=document["schema_version"],
            policy_id=document["policy_id"],
            policy_version=document["policy_version"],
            bounds=bounds,
            decisions=decisions,
            required_paths=required_paths,
            forbidden_path_prefixes=forbidden_prefixes,
            synthetic_registry_path=registry_path,
            check_markdown_links=document["check_markdown_links"],
            require_clean_worktree=document["require_clean_worktree"],
            require_root_commit=document["require_root_commit"],
            approved_parent_commits=approved_parents,
            approved_inventory_sha256=document["approved_inventory_sha256"],
            deny_inventory_sha256=document["deny_inventory_sha256"],
        ),
        loaded.sha256,
    )


def load_approved_inventory(
    path: Path, policy: PublicReleasePolicy
) -> tuple[ApprovedInventory, str]:
    loaded = load_control_document(
        path,
        label="approved candidate inventory",
        schema_name="candidate-inventory.schema.json",
        byte_cap=policy.bounds.max_structured_bytes,
    )
    seen: set[str] = set()
    casefolded: dict[str, str] = {}
    approved: list[ApprovedFile] = []
    total_bytes = 0
    for index, record in enumerate(loaded.document["files"]):
        relative = validate_relative_path(
            record["path"],
            max_bytes=policy.bounds.max_path_bytes,
            label=f"approved inventory path {index}",
        )
        if relative in seen:
            raise ValueError("approved candidate inventory contains a duplicate path")
        folded = relative.casefold()
        if folded in casefolded:
            raise ValueError("approved candidate inventory contains a case-fold path collision")
        seen.add(relative)
        casefolded[folded] = relative
        if record["bytes"] > policy.bounds.max_file_bytes:
            raise ValueError("approved candidate inventory exceeds the per-file byte limit")
        total_bytes += record["bytes"]
        if total_bytes > policy.bounds.max_total_bytes:
            raise ValueError("approved candidate inventory exceeds the aggregate byte limit")
        approved.append(
            ApprovedFile(
                path=relative,
                bytes=record["bytes"],
                sha256=record["sha256"],
                git_mode=record["git_mode"],
                classification=record["classification"],
                content_type=record["content_type"],
            )
        )
    if len(approved) > policy.bounds.max_paths:
        raise ValueError("approved candidate inventory exceeds the path-count limit")
    missing = sorted(set(policy.required_paths) - seen)
    if missing:
        raise ValueError("approved candidate inventory omits a policy-required path")
    return (
        ApprovedInventory(
            schema_version=loaded.document["schema_version"],
            inventory_id=loaded.document["inventory_id"],
            files=tuple(sorted(approved, key=lambda item: item.path)),
        ),
        loaded.sha256,
    )


def load_deny_inventory(
    path: Path, policy: PublicReleasePolicy
) -> tuple[DenyInventory, str]:
    loaded = load_control_document(
        path,
        label="private deny inventory",
        schema_name="deny-inventory.schema.json",
        byte_cap=policy.bounds.max_structured_bytes,
    )
    records = loaded.document["rules"]
    if len(records) > policy.bounds.max_deny_rules:
        raise ValueError("private deny inventory exceeds the configured rule-count limit")
    seen_ids: set[str] = set()
    rules: list[DenyRule] = []
    for record in records:
        rule_id = record["id"]
        if rule_id in seen_ids:
            raise ValueError("private deny inventory contains a duplicate rule id")
        seen_ids.add(rule_id)
        value = record["value"]
        if record["kind"] == "file-sha256":
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ValueError("file-sha256 deny rules must use lowercase SHA-256")
        rules.append(DenyRule(rule_id=rule_id, kind=record["kind"], value=value))
    return (
        DenyInventory(
            schema_version=loaded.document["schema_version"],
            inventory_id=loaded.document["inventory_id"],
            coverage_status=loaded.document["coverage_status"],
            source_scope_sha256=loaded.document["source_scope_sha256"],
            rules=tuple(rules),
        ),
        loaded.sha256,
    )


def parse_synthetic_registry(
    content: bytes,
    *,
    suffix: str,
    policy: PublicReleasePolicy,
) -> SyntheticRegistry:
    if len(content) > policy.bounds.max_structured_bytes:
        raise ValueError("synthetic identity registry exceeds the configured structured-file limit")
    document = _parse_document_bytes(content, suffix, "synthetic identity registry")
    _validate(document, "synthetic-identities.schema.json", "synthetic identity registry")
    seen_values: set[str] = set()
    identities: list[SyntheticIdentity] = []
    for record in document["entries"]:
        if record["value"] in seen_values:
            raise ValueError("synthetic identity registry contains a duplicate value")
        seen_values.add(record["value"])
        identities.append(
            SyntheticIdentity(
                value=record["value"],
                identity_type=record["type"],
                purpose=record["purpose"],
                source_class=record["source_class"],
                validation_rule=record["validation_rule"],
            )
        )
    return SyntheticRegistry(
        registry_version=document["registry_version"], identities=tuple(identities)
    )
