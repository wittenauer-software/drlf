from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from drlf.public_release.bounds import Budget
from drlf.public_release.models import (
    ApprovedInventory,
    CandidateSnapshot,
    DenyInventory,
    Finding,
    PublicReleasePolicy,
    SyntheticRegistry,
)
from drlf.public_release.policy import parse_synthetic_registry

_ARCHIVE_MAGIC = (
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"!<arch>\n",
    b"\x1f\x8b",
    b"BZh",
    b"\xfd7zXZ\x00",
    b"7z\xbc\xaf\x27\x1c",
    b"Rar!\x1a\x07",
    b"\x28\xb5\x2f\xfd",
    b"\x04\x22\x4d\x18",
    b"MSCF\x00\x00\x00\x00",
    b"xar!\x00\x1c\x00\x01",
)
_OPAQUE_MAGIC = (b"%PDF-", b"%!PS-Adobe-")
_MARKDOWN_INLINE_LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*<?([^\s)>]+)>?(?:\s+[^)]*)?\)")
_MARKDOWN_REFERENCE_LINK = re.compile(r"(?m)^\s*\[[^\]\n]+\]:\s*<?([^\s>]+)>?")
_NPI_LIKE = re.compile(r"(?<!\d)[12]\d{9}(?!\d)")
_HEX_DIGEST = re.compile(r"(?<![0-9A-Fa-f])[0-9a-f]{20,}(?![0-9A-Fa-f])")
_SYNTHETIC_TOKEN = re.compile(r"(?<![0-9A-Za-z])(SYNTHETIC-[A-Za-z0-9_-]+)")
_EMAIL_LIKE = re.compile(
    r"(?<![A-Za-z0-9.!#$%&'*+/=?^_`{|}~-])"
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?"
    r"(?![A-Za-z0-9-]|\.[A-Za-z0-9-])"
)
_PRIVATE_RECORD_ID = re.compile(r"\b(?:LEARN|PROMO)-\d{4}\b")
_ABSOLUTE_LOCAL_PATH = re.compile(
    r"(?ix)(?:"
    r"(?<![A-Z0-9\\])[A-Z]:[\\/]"
    r"|(?<![:\\/])\\\\[A-Z0-9][A-Z0-9._-]*\\[A-Z0-9$][A-Z0-9$._ -]*(?:\\[^\s]*)?"
    r"|(?<![A-Z0-9:/<])/(?:Users|home)/[^\s/]+(?:/[^\s]*)?"
    r"|(?<![A-Z0-9:/<])/(?:root|tmp)(?:/[^\s]*)?"
    r"|(?<![A-Z0-9:/<])/(?:private/var/folders|var/folders)/[^\s]+"
    r"|(?<![A-Z0-9:/<])/mnt/[A-Z]/(?:Users)/[^\s/]+(?:/[^\s]*)?"
    r"|\bfile:(?://)?/"
    r")"
)
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private-key-marker",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"),
    ),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("github-fine-grained-token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,255}\b")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws-temporary-access-key", re.compile(r"\bASIA[0-9A-Z]{16}\b")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    (
        "credential-bearing-database-url",
        re.compile(r"\bpostgres(?:ql)?://[^\s:/]+:[^\s@]+@", re.IGNORECASE),
    ),
)
_SAFE_PLACEHOLDER_CONTACTS = {"replace-with-a-local-password@127.0.0.1"}


def _is_valid_npi(value: str) -> bool:
    """Return whether a 10-digit candidate has the CMS NPI Luhn check digit."""
    digits = [int(character) for character in "80840" + value]
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2:
            digit *= 2
        total += digit // 10 + digit % 10
    return total % 10 == 0


def _contains_valid_npi(text: str) -> bool:
    return any(_is_valid_npi(match.group(0)) for match in _NPI_LIKE.finditer(text))


def _content_contact_is_allowed(value: str, approved_contacts: set[str]) -> bool:
    normalized = value.casefold()
    return (
        normalized in approved_contacts
        or normalized.endswith(".invalid")
        or normalized in _SAFE_PLACEHOLDER_CONTACTS
    )


def _secret_match_is_placeholder(rule_id: str, matched: str) -> bool:
    return (
        rule_id == "credential-bearing-database-url"
        and "replace-with-a-local-password" in matched.casefold()
    )


@dataclass(frozen=True)
class ContentCheckResult:
    findings: tuple[Finding, ...]
    truncated: bool


class _Findings:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.items: list[Finding] = []
        self.truncated = False

    def add(
        self,
        check: str,
        code: str,
        *,
        path: str | None = None,
        rule_id: str | None = None,
    ) -> None:
        if len(self.items) >= self.maximum:
            self.truncated = True
            return
        self.items.append(Finding(check=check, code=code, path=path, rule_id=rule_id))

    def result(self) -> ContentCheckResult:
        ordered = tuple(
            sorted(
                self.items,
                key=lambda item: (
                    item.check,
                    item.code,
                    item.path or "",
                    item.rule_id or "",
                ),
            )
        )
        return ContentCheckResult(findings=ordered, truncated=self.truncated)


def observed_inventory_sha256(snapshot: CandidateSnapshot) -> str:
    records = [
        {
            "bytes": item.bytes,
            "git_mode": item.git_mode,
            "path": item.path,
            "sha256": item.sha256,
        }
        for item in sorted(snapshot.observations, key=lambda item: item.path)
    ]
    canonical = json.dumps(records, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return sha256((canonical + "\n").encode("utf-8")).hexdigest()


def _forbidden_path(
    path: str,
    policy: PublicReleasePolicy,
) -> bool:
    hard_forbidden = (".git", ".hg", ".svn")
    for prefix in (*hard_forbidden, *policy.forbidden_path_prefixes):
        folded_path = path.casefold()
        folded_prefix = prefix.casefold()
        if folded_path == folded_prefix or folded_path.startswith(folded_prefix + "/"):
            return True
    return PurePosixPath(path).name.casefold() == ".gitmodules"


def _looks_like_archive(content: bytes) -> bool:
    if any(content.startswith(magic) for magic in _ARCHIVE_MAGIC):
        return True
    return len(content) > 262 and content[257:262] == b"ustar"


def _looks_opaque(content: bytes) -> bool:
    return any(content.startswith(magic) for magic in _OPAQUE_MAGIC)


def _normalize_link(source_path: str, target: str) -> str | None:
    target = unquote(target)
    if "\\" in target or any(ord(character) < 32 for character in target):
        return None
    parsed = urlsplit(target)
    if parsed.scheme:
        if parsed.scheme.casefold() in {"http", "https", "mailto"}:
            return "<external>"
        return None
    if parsed.netloc:
        return None
    if not parsed.path and parsed.fragment:
        return "<anchor>"
    if parsed.path.startswith("/"):
        return None
    combined = PurePosixPath(source_path).parent / PurePosixPath(parsed.path)
    parts: list[str] = []
    for part in combined.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    if not parts:
        return None
    return PurePosixPath(*parts).as_posix()


def _check_local_links(
    path: str,
    text: str,
    candidate_paths: set[str],
    candidate_directories: set[str],
    findings: _Findings,
    budget: Budget,
) -> None:
    for pattern in (_MARKDOWN_INLINE_LINK, _MARKDOWN_REFERENCE_LINK):
        for match in pattern.finditer(text):
            budget.check_deadline()
            normalized = _normalize_link(path, match.group(1))
            if normalized in {"<external>", "<anchor>"}:
                continue
            if normalized is None:
                findings.add("local-links", "invalid-or-escaping-local-link", path=path)
            elif normalized not in candidate_paths and normalized not in candidate_directories:
                findings.add("local-links", "missing-local-link-target", path=path)
            if findings.truncated:
                return


def _candidate_directories(paths: set[str]) -> set[str]:
    result: set[str] = set()
    for value in paths:
        parent = PurePosixPath(value).parent
        while parent.parts and parent.as_posix() != ".":
            result.add(parent.as_posix())
            parent = parent.parent
    return result


def _registry_values(registry: SyntheticRegistry) -> set[str]:
    return {identity.value for identity in registry.identities}


def run_content_checks(
    snapshot: CandidateSnapshot,
    policy: PublicReleasePolicy,
    inventory: ApprovedInventory,
    deny_inventory: DenyInventory,
    budget: Budget,
) -> ContentCheckResult:
    findings = _Findings(policy.bounds.max_findings)
    observations = {record.path: record for record in snapshot.observations}
    candidate_paths = set(observations)
    candidate_directories = _candidate_directories(candidate_paths)

    for path in sorted(candidate_paths):
        budget.check_deadline()
        if _forbidden_path(path, policy):
            findings.add("paths", "forbidden-path", path=path)
    for required in policy.required_paths:
        if required not in candidate_paths:
            findings.add("paths", "required-path-missing", path=required)

    registry: SyntheticRegistry | None = None
    registry_content = snapshot.contents.get(policy.synthetic_registry_path)
    if registry_content is None:
        findings.add(
            "synthetic-registry",
            "registry-missing",
            path=policy.synthetic_registry_path,
        )
    else:
        try:
            registry = parse_synthetic_registry(
                registry_content,
                suffix=PurePosixPath(policy.synthetic_registry_path).suffix,
                policy=policy,
            )
        except ValueError:
            findings.add(
                "synthetic-registry",
                "registry-invalid",
                path=policy.synthetic_registry_path,
            )

    registered_values = _registry_values(registry) if registry is not None else set()
    approved_by_path = {record.path: record for record in inventory.files}
    deny_exact = [rule for rule in deny_inventory.rules if rule.kind == "exact-text"]
    deny_casefold = [rule for rule in deny_inventory.rules if rule.kind == "casefold-text"]
    deny_hash = [rule for rule in deny_inventory.rules if rule.kind == "file-sha256"]
    deny_path = [rule for rule in deny_inventory.rules if rule.kind == "path-fragment"]
    approved_contacts = {
        value.casefold() for value in policy.decisions.approved_public_contacts
    }

    for path in sorted(candidate_paths):
        budget.check_deadline()
        observation = observations[path]
        content = snapshot.contents[path]
        for rule in deny_hash:
            budget.check_deadline()
            if observation.sha256 == rule.value:
                findings.add(
                    "private-deny", "denied-file-hash", path=path, rule_id=rule.rule_id
                )
        for rule in deny_path:
            budget.check_deadline()
            normalized_path = unicodedata.normalize("NFKC", path).casefold()
            normalized_rule = unicodedata.normalize("NFKC", rule.value).casefold()
            if normalized_rule in normalized_path:
                findings.add(
                    "private-deny", "denied-path-fragment", path=path, rule_id=rule.rule_id
                )
        if approved_by_path[path].content_type != "text":
            findings.add("file-type", "unsupported-content-type", path=path)
            continue
        if _looks_like_archive(content):
            findings.add("file-type", "archive-content-rejected", path=path)
            continue
        if _looks_opaque(content) or b"\x00" in content:
            findings.add("file-type", "opaque-or-binary-content-rejected", path=path)
            continue
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            findings.add("file-type", "non-utf8-content-rejected", path=path)
            continue

        for rule in deny_exact:
            budget.check_deadline()
            if rule.value in text:
                findings.add("private-deny", "denied-text", path=path, rule_id=rule.rule_id)
        folded_text: str | None = None
        for rule in deny_casefold:
            budget.check_deadline()
            if folded_text is None:
                folded_text = unicodedata.normalize("NFKC", text).casefold()
            normalized_rule = unicodedata.normalize("NFKC", rule.value).casefold()
            if normalized_rule in folded_text:
                findings.add(
                    "private-deny", "denied-casefold-text", path=path, rule_id=rule.rule_id
                )

        for rule_id, pattern in _SECRET_PATTERNS:
            match = pattern.search(text)
            if match is not None and not _secret_match_is_placeholder(rule_id, match.group(0)):
                findings.add(
                    "secrets", "high-confidence-secret-pattern", path=path, rule_id=rule_id
                )
        if _ABSOLUTE_LOCAL_PATH.search(text):
            findings.add("private-paths", "absolute-user-or-unc-path", path=path)
        if _PRIVATE_RECORD_ID.search(text):
            findings.add("private-references", "numbered-private-record-reference", path=path)
        for match in _EMAIL_LIKE.finditer(text):
            budget.check_deadline()
            if not _content_contact_is_allowed(match.group(0), approved_contacts):
                findings.add("public-contacts", "unapproved-email-like-value", path=path)

        identity_scan_text = _HEX_DIGEST.sub("<digest>", text)
        if _contains_valid_npi(identity_scan_text):
            findings.add(
                "synthetic-registry",
                "ten-digit-npi-like-value-rejected",
                path=path,
            )
        for match in _SYNTHETIC_TOKEN.finditer(text):
            budget.check_deadline()
            value = match.group(1)
            if value not in registered_values:
                findings.add("synthetic-registry", "unregistered-identity-like-value", path=path)
            if findings.truncated:
                break

        if policy.check_markdown_links and PurePosixPath(path).suffix.casefold() == ".md":
            _check_local_links(
                path,
                text,
                candidate_paths,
                candidate_directories,
                findings,
                budget,
            )

    if snapshot.commit_metadata is not None:
        metadata_path = "<commit-metadata>"
        metadata_hash = sha256(snapshot.commit_metadata).hexdigest()
        try:
            metadata_text = snapshot.commit_metadata.decode("utf-8")
        except UnicodeDecodeError:
            findings.add("commit-metadata", "non-utf8-commit-metadata", path=metadata_path)
        else:
            for rule in deny_hash:
                budget.check_deadline()
                if metadata_hash == rule.value:
                    findings.add(
                        "private-deny",
                        "denied-commit-metadata-hash",
                        path=metadata_path,
                        rule_id=rule.rule_id,
                    )
            for rule in deny_exact:
                budget.check_deadline()
                if rule.value in metadata_text:
                    findings.add(
                        "private-deny",
                        "denied-commit-metadata-text",
                        path=metadata_path,
                        rule_id=rule.rule_id,
                    )
            folded_metadata: str | None = None
            for rule in deny_casefold:
                budget.check_deadline()
                if folded_metadata is None:
                    folded_metadata = unicodedata.normalize("NFKC", metadata_text).casefold()
                normalized_rule = unicodedata.normalize("NFKC", rule.value).casefold()
                if normalized_rule in folded_metadata:
                    findings.add(
                        "private-deny",
                        "denied-commit-metadata-casefold-text",
                        path=metadata_path,
                        rule_id=rule.rule_id,
                    )
            for rule_id, pattern in _SECRET_PATTERNS:
                if pattern.search(metadata_text):
                    findings.add(
                        "secrets",
                        "commit-metadata-secret-pattern",
                        path=metadata_path,
                        rule_id=rule_id,
                    )
            if _ABSOLUTE_LOCAL_PATH.search(metadata_text):
                findings.add(
                    "private-paths", "commit-metadata-absolute-path", path=metadata_path
                )
            if _PRIVATE_RECORD_ID.search(metadata_text):
                findings.add(
                    "private-references",
                    "commit-metadata-private-record-reference",
                    path=metadata_path,
                )
            for match in _EMAIL_LIKE.finditer(metadata_text):
                budget.check_deadline()
                if match.group(0).casefold() not in approved_contacts:
                    findings.add(
                        "public-contacts",
                        "unapproved-commit-email-like-value",
                        path=metadata_path,
                    )

    if snapshot.commit_message is not None:
        try:
            message_text = snapshot.commit_message.decode("utf-8")
        except UnicodeDecodeError:
            findings.add("commit-metadata", "non-utf8-commit-message", path="<commit-message>")
        else:
            if _contains_valid_npi(message_text):
                findings.add(
                    "synthetic-registry",
                    "commit-message-ten-digit-npi-like-value-rejected",
                    path="<commit-message>",
                )
            for match in _SYNTHETIC_TOKEN.finditer(message_text):
                budget.check_deadline()
                if match.group(1) not in registered_values:
                    findings.add(
                        "synthetic-registry",
                        "commit-message-unregistered-identity-like-value",
                        path="<commit-message>",
                    )

    if snapshot.commit_identity is not None:
        try:
            identity_text = snapshot.commit_identity.decode("utf-8")
        except UnicodeDecodeError:
            findings.add(
                "commit-metadata", "non-utf8-commit-identity", path="<commit-identity>"
            )
        else:
            expected_identity = "\n".join(
                (
                    "author "
                    f"{policy.decisions.commit_author_name} "
                    f"<{policy.decisions.commit_author_email}>",
                    "committer "
                    f"{policy.decisions.commit_committer_name} "
                    f"<{policy.decisions.commit_committer_email}>",
                )
            )
            if identity_text != expected_identity:
                findings.add(
                    "commit-metadata",
                    "commit-identity-does-not-match-policy",
                    path="<commit-identity>",
                )
            if _contains_valid_npi(identity_text):
                findings.add(
                    "synthetic-registry",
                    "commit-identity-ten-digit-npi-like-value-rejected",
                    path="<commit-identity>",
                )
            for match in _SYNTHETIC_TOKEN.finditer(identity_text):
                budget.check_deadline()
                if match.group(1) not in registered_values:
                    findings.add(
                        "synthetic-registry",
                        "commit-identity-unregistered-identity-like-value",
                        path="<commit-identity>",
                    )

    return findings.result()
