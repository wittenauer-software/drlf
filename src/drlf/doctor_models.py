from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

CheckStatus = Literal["pass", "warn", "fail", "skip"]


@dataclass(frozen=True)
class DoctorCheck:
    """One credential-safe workspace preflight result."""

    name: str
    status: CheckStatus
    detail: str


@dataclass(frozen=True)
class DoctorReport:
    """Aggregate local setup health without configuration secrets."""

    ready: bool
    checks: tuple[DoctorCheck, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"ready": self.ready, "checks": [asdict(check) for check in self.checks]}
