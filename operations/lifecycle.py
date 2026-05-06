from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List


OPERATIONAL_STATES = {
    "STARTING",
    "BOOTSTRAPPING",
    "READY",
    "RUNNING",
    "DEGRADED",
    "BLOCKED",
    "STOPPED",
}

_HEALTH_READY = "READY"
_HEALTH_NOT_READY = "NOT_READY"
_HEALTH_DEGRADED = "DEGRADED"

_ALLOWED_TRANSITIONS = {
    "STOPPED": {"STARTING", "BOOTSTRAPPING"},
    "STARTING": {"BOOTSTRAPPING", "BLOCKED", "STOPPED"},
    "BOOTSTRAPPING": {"READY", "DEGRADED", "BLOCKED", "STOPPED"},
    "READY": {"RUNNING", "DEGRADED", "BLOCKED", "STOPPED", "BOOTSTRAPPING"},
    "RUNNING": {"READY", "DEGRADED", "BLOCKED", "STOPPED", "BOOTSTRAPPING"},
    "DEGRADED": {"BOOTSTRAPPING", "BLOCKED", "STOPPED"},
    "BLOCKED": {"BOOTSTRAPPING", "STOPPED"},
}


class LifecycleTransitionError(RuntimeError):
    """Raised for deterministic lifecycle contract violations."""


@dataclass(frozen=True)
class LifecycleTransition:
    from_state: str
    to_state: str
    reason: str
    timestamp: str


class OperationsLifecycleManager:
    def __init__(self, *, initial_state: str = "STOPPED") -> None:
        token = str(initial_state).strip().upper()
        if token not in OPERATIONAL_STATES:
            raise LifecycleTransitionError(f"Invalid initial state: {initial_state}")
        self._state = token
        self._history: List[LifecycleTransition] = []

    @property
    def state(self) -> str:
        return self._state

    def transition(self, *, to_state: str, reason: str) -> LifecycleTransition:
        target = str(to_state).strip().upper()
        if target not in OPERATIONAL_STATES:
            raise LifecycleTransitionError(f"Invalid lifecycle state: {to_state}")

        allowed = _ALLOWED_TRANSITIONS.get(self._state, set())
        if target not in allowed and target != self._state:
            raise LifecycleTransitionError(f"Invalid lifecycle transition: {self._state} -> {target}")

        event = LifecycleTransition(
            from_state=self._state,
            to_state=target,
            reason=str(reason),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._state = target
        self._history.append(event)
        return event

    def history(self) -> List[Dict[str, str]]:
        return [
            {
                "from_state": item.from_state,
                "to_state": item.to_state,
                "reason": item.reason,
                "timestamp": item.timestamp,
            }
            for item in self._history
        ]

    def health_state(self, *, require_bootstrap: bool) -> str:
        if self._state == "DEGRADED":
            return _HEALTH_DEGRADED
        if self._state in {"RUNNING", "READY"} and not require_bootstrap:
            return _HEALTH_READY
        return _HEALTH_NOT_READY

    def operational_truth(self, *, require_bootstrap: bool) -> str:
        if self._state == "RUNNING" and not require_bootstrap:
            return "RUNNING"
        return "NOT_RUNNING"

    @staticmethod
    def assert_no_mutation_allowed(state: str) -> None:
        token = str(state).strip().upper()
        if token == "DEGRADED":
            raise LifecycleTransitionError("Mutation operations are not allowed while lifecycle state is DEGRADED")
