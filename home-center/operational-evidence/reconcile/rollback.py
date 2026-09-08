"""P2.5 rollback safety primitives.

Rollback is intentionally conservative: no mutation is performed here.
The module only models and validates rollback decisions.
"""

from dataclasses import dataclass
from enum import Enum


class RollbackState(str, Enum):
    READY = "ready"
    BLOCKED = "blocked"
    REQUIRED = "required"


@dataclass(frozen=True)
class RollbackDecision:
    state: RollbackState
    reason: str


def require_rollback(reason: str) -> RollbackDecision:
    if not reason:
        raise ValueError("rollback reason is required")
    return RollbackDecision(
        state=RollbackState.REQUIRED,
        reason=reason,
    )


def block_rollback(reason: str) -> RollbackDecision:
    if not reason:
        raise ValueError("rollback block reason is required")
    return RollbackDecision(
        state=RollbackState.BLOCKED,
        reason=reason,
    )
