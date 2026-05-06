from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict


STATE_SCHEMA_VERSION = "1.0"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_operations_state() -> Dict[str, Any]:
    timestamp = _utc_now()
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "lifecycle_state": "STOPPED",
        "operational_truth": "NOT_RUNNING",
        "health_state": "NOT_READY",
        "require_bootstrap": True,
        "failure_reason": "",
        "failure_count": 0,
        "last_bootstrap_result": {},
        "integration_readiness_status": {},
        "phase_state": {
            "phase": "",
            "sst_owner": "",
        },
        "adapter_health_snapshot": {},
        "last_successful_run_id": "",
        "last_run_status": {},
        "last_audit_path": "",
        "metrics": {
            "bootstrap_attempts_total": 0,
            "bootstrap_failures_total": 0,
            "run_attempts_total": 0,
            "run_failures_total": 0,
        },
        "service": {
            "pid": 0,
            "host": "127.0.0.1",
            "port": 8088,
            "started_at": "",
        },
        "updated_at": timestamp,
        "created_at": timestamp,
    }


class OperationsStateStore:
    def __init__(self, *, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> Dict[str, Any]:
        if not self.path.exists() or not self.path.is_file():
            return default_operations_state()

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Operations state payload is not an object")

        merged = default_operations_state()
        merged.update(payload)

        metrics = merged.get("metrics")
        if not isinstance(metrics, dict):
            metrics = {}
        default_metrics = default_operations_state()["metrics"]
        default_metrics.update(metrics)
        merged["metrics"] = default_metrics

        service_info = merged.get("service")
        if not isinstance(service_info, dict):
            service_info = {}
        default_service = default_operations_state()["service"]
        default_service.update(service_info)
        merged["service"] = default_service

        phase_state = merged.get("phase_state")
        if not isinstance(phase_state, dict):
            phase_state = {}
        default_phase = default_operations_state()["phase_state"]
        default_phase.update(phase_state)
        merged["phase_state"] = default_phase

        return merged

    def save(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        document = dict(payload)
        document["updated_at"] = _utc_now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(document, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return document
