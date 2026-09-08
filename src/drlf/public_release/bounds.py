from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from drlf.public_release.models import ScanBounds


class ScanFailure(ValueError):
    """A redaction-safe, fail-closed candidate error."""

    def __init__(
        self,
        code: str,
        *,
        path: str | None = None,
        incomplete: bool = True,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.path = path
        self.incomplete = incomplete


@dataclass
class Budget:
    bounds: ScanBounds
    started: float
    paths: int = 0
    files: int = 0
    bytes_read: int = 0
    verification_bytes_read: int = 0
    git_objects: int = 0
    git_output_bytes: int = 0

    @classmethod
    def start(cls, bounds: ScanBounds) -> Budget:
        return cls(bounds=bounds, started=monotonic())

    def check_deadline(self) -> None:
        if monotonic() - self.started > self.bounds.max_runtime_seconds:
            raise ScanFailure("runtime-cap-exceeded")

    def remaining_seconds(self) -> float:
        remaining = self.bounds.max_runtime_seconds - (monotonic() - self.started)
        if remaining <= 0:
            raise ScanFailure("runtime-cap-exceeded")
        return remaining

    def add_path(self) -> None:
        self.paths += 1
        if self.paths > self.bounds.max_paths:
            raise ScanFailure("path-count-cap-exceeded")
        self.check_deadline()

    def add_file(self, size: int) -> None:
        self.files += 1
        if size > self.bounds.max_file_bytes:
            raise ScanFailure("per-file-byte-cap-exceeded")
        if self.bytes_read + size > self.bounds.max_total_bytes:
            raise ScanFailure("aggregate-byte-cap-exceeded")

    def add_bytes_read(self, count: int) -> None:
        self.bytes_read += count
        if self.bytes_read > self.bounds.max_total_bytes:
            raise ScanFailure("aggregate-byte-cap-exceeded")
        self.check_deadline()

    def add_verification_bytes(self, count: int) -> None:
        self.verification_bytes_read += count
        if self.verification_bytes_read > self.bounds.max_total_bytes:
            raise ScanFailure("verification-byte-cap-exceeded")
        self.check_deadline()

    def add_git_objects(self, count: int) -> None:
        self.git_objects += count
        if self.git_objects > self.bounds.max_git_objects:
            raise ScanFailure("git-object-count-cap-exceeded")

    def add_git_output(self, count: int) -> None:
        self.git_output_bytes += count
        if self.git_output_bytes > self.bounds.max_git_output_bytes:
            raise ScanFailure("git-output-byte-cap-exceeded")
        self.check_deadline()

    def remaining_git_output_bytes(self) -> int:
        remaining = self.bounds.max_git_output_bytes - self.git_output_bytes
        if remaining <= 0:
            raise ScanFailure("git-output-byte-cap-exceeded")
        return remaining

    def usage(self) -> dict[str, int]:
        return {
            "paths": self.paths,
            "files": self.files,
            "bytes_read": self.bytes_read,
            "verification_bytes_read": self.verification_bytes_read,
            "git_objects": self.git_objects,
            "git_output_bytes": self.git_output_bytes,
        }
