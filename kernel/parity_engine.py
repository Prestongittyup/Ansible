from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, Iterable, List, Tuple

from kernel.state import ParityMetrics
from observability.logger import log_event

CANONICAL_FIELDS = (
    "ip_address",
    "hostname",
    "serial_number",
    "vendor",
    "model",
    "site",
    "status",
    "authority_owner",
)

HARD_DRIFT_FIELDS = {
    "authority_owner",
}


@dataclass
class ParityComputation:
    metrics: ParityMetrics
    normalized_source: Dict[str, Dict[str, str]]
    normalized_target: Dict[str, Dict[str, str]]


def _stable_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).strip()


def _to_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def apply_ip_change_events(
    records: List[Dict[str, Any]],
    identity_events: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    updated = [dict(record) for record in records]
    conflicts: List[Dict[str, Any]] = []

    for index, event in enumerate(identity_events):
        old_ip = _stable_text(event.get("old_ip"))
        new_ip = _stable_text(event.get("new_ip"))

        if not old_ip or not new_ip:
            conflicts.append(
                {
                    "code": "IDENTITY_EVENT_INVALID",
                    "severity": "HARD",
                    "event_index": index,
                    "details": "old_ip and new_ip are required",
                }
            )
            continue

        matches = [row for row in updated if _stable_text(row.get("ip_address")) == old_ip]
        if not matches:
            conflicts.append(
                {
                    "code": "IDENTITY_EVENT_OLD_IP_NOT_FOUND",
                    "severity": "HARD",
                    "event_index": index,
                    "old_ip": old_ip,
                    "new_ip": new_ip,
                }
            )
            continue

        if old_ip != new_ip and any(_stable_text(row.get("ip_address")) == new_ip for row in updated):
            conflicts.append(
                {
                    "code": "IDENTITY_EVENT_NEW_IP_EXISTS",
                    "severity": "HARD",
                    "event_index": index,
                    "old_ip": old_ip,
                    "new_ip": new_ip,
                }
            )
            continue

        for row in matches:
            row["ip_address"] = new_ip
            row["ip_change_event_applied"] = True

    return updated, conflicts


def normalize_records(
    records: Iterable[Dict[str, Any]],
    *,
    dataset_label: str,
) -> Tuple[Dict[str, Dict[str, str]], List[Dict[str, Any]]]:
    normalized: Dict[str, Dict[str, str]] = {}
    conflicts: List[Dict[str, Any]] = []

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            conflicts.append(
                {
                    "code": "INVALID_RECORD_TYPE",
                    "severity": "HARD",
                    "dataset": dataset_label,
                    "record_index": index,
                }
            )
            continue

        ip_address = _stable_text(record.get("ip_address"))
        if not ip_address:
            conflicts.append(
                {
                    "code": "MISSING_IP_ADDRESS",
                    "severity": "HARD",
                    "dataset": dataset_label,
                    "record_index": index,
                }
            )
            continue

        if ip_address in normalized:
            conflicts.append(
                {
                    "code": "DUPLICATE_IP_ADDRESS",
                    "severity": "HARD",
                    "dataset": dataset_label,
                    "ip_address": ip_address,
                    "record_index": index,
                }
            )
            continue

        row: Dict[str, str] = {}
        for field in CANONICAL_FIELDS:
            if field == "ip_address":
                row[field] = ip_address
            else:
                row[field] = _stable_text(record.get(field))

        row["record_source"] = dataset_label
        normalized[ip_address] = row

    return normalized, conflicts


def _stable_hash_payload(
    source: Dict[str, Dict[str, str]],
    target: Dict[str, Dict[str, str]],
    drift_details: List[Dict[str, Any]],
    identity_conflicts: List[Dict[str, Any]],
) -> str:
    payload = {
        "source": [source[key] for key in sorted(source.keys())],
        "target": [target[key] for key in sorted(target.keys())],
        "drift_details": drift_details,
        "identity_conflicts": identity_conflicts,
    }
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _classify_record_drift(
    source_record: Dict[str, str],
    target_record: Dict[str, str],
) -> Tuple[str, List[Dict[str, str]]]:
    changed_fields: List[Dict[str, str]] = []

    for field in CANONICAL_FIELDS:
        source_value = _stable_text(source_record.get(field))
        target_value = _stable_text(target_record.get(field))
        if source_value != target_value:
            changed_fields.append(
                {
                    "field": field,
                    "source": source_value,
                    "target": target_value,
                }
            )

    if not changed_fields:
        return "NONE", []

    if any(item["field"] in HARD_DRIFT_FIELDS for item in changed_fields):
        return "HARD", changed_fields

    return "SOFT", changed_fields


def calculate_parity(
    source_records: List[Dict[str, Any]],
    target_records: List[Dict[str, Any]],
    *,
    max_global_drift: float,
) -> ParityComputation:
    started = perf_counter()
    log_event(
        level="INFO",
        component="parity",
        operation="CALCULATE_PARITY",
        status="START",
        source_records=len(source_records),
        target_records=len(target_records),
        max_global_drift=max_global_drift,
    )

    source_norm, source_conflicts = normalize_records(source_records, dataset_label="source")
    target_norm, target_conflicts = normalize_records(target_records, dataset_label="target")

    identity_conflicts = source_conflicts + target_conflicts
    drift_details: List[Dict[str, Any]] = []

    compared_ips = sorted(set(source_norm.keys()) | set(target_norm.keys()))
    hard_drift_count = 0
    soft_drift_count = 0

    for ip_address in compared_ips:
        source_row = source_norm.get(ip_address)
        target_row = target_norm.get(ip_address)

        if source_row is None or target_row is None:
            hard_drift_count += 1
            drift_details.append(
                {
                    "ip_address": ip_address,
                    "classification": "HARD",
                    "code": "MISSING_RECORD_SIDE",
                    "source_present": source_row is not None,
                    "target_present": target_row is not None,
                }
            )
            continue

        classification, changed_fields = _classify_record_drift(source_row, target_row)
        if classification == "NONE":
            continue
        if classification == "HARD":
            hard_drift_count += 1
        else:
            soft_drift_count += 1

        drift_details.append(
            {
                "ip_address": ip_address,
                "classification": classification,
                "code": "FIELD_DRIFT",
                "changed_fields": changed_fields,
            }
        )

    drifted_records = hard_drift_count + soft_drift_count
    compared_records_total = len(compared_ips)
    global_drift_pct = 0.0
    if compared_records_total > 0:
        global_drift_pct = round((drifted_records / compared_records_total) * 100.0, 6)

    if global_drift_pct >= max_global_drift:
        drift_details.append(
            {
                "classification": "HARD",
                "code": "GLOBAL_DRIFT_THRESHOLD_EXCEEDED",
                "max_global_drift": max_global_drift,
                "global_drift_pct": global_drift_pct,
            }
        )
        hard_drift_count += 1

    deterministic_hash = _stable_hash_payload(
        source=source_norm,
        target=target_norm,
        drift_details=drift_details,
        identity_conflicts=identity_conflicts,
    )

    metrics = ParityMetrics(
        compared_records_total=compared_records_total,
        drifted_records_total=drifted_records,
        global_drift_pct=global_drift_pct,
        hard_drift_count=hard_drift_count,
        soft_drift_count=soft_drift_count,
        identity_conflicts_total=len(identity_conflicts),
        identity_conflicts=identity_conflicts,
        drift_details=drift_details,
        deterministic_hash=deterministic_hash,
    )

    log_event(
        level="INFO",
        component="parity",
        operation="CALCULATE_PARITY",
        status="SUCCESS",
        duration_ms=int(round((perf_counter() - started) * 1000.0, 0)),
        compared_records_total=compared_records_total,
        drifted_records_total=drifted_records,
        global_drift_pct=global_drift_pct,
        hard_drift_count=hard_drift_count,
        soft_drift_count=soft_drift_count,
        identity_conflicts_total=len(identity_conflicts),
    )

    return ParityComputation(
        metrics=metrics,
        normalized_source=source_norm,
        normalized_target=target_norm,
    )


class ParityStabilityTracker:
    def __init__(self, history: List[Dict[str, Any]] | None = None) -> None:
        self.history: List[Dict[str, Any]] = list(history or [])

    def record_run(self, *, timestamp: str, parity_pass: bool, deterministic_hash: str) -> None:
        self.history.append(
            {
                "timestamp": timestamp,
                "parity_pass": bool(parity_pass),
                "deterministic_hash": deterministic_hash,
            }
        )

    def evaluate(
        self,
        *,
        required_consecutive_passes: int,
        window_days: int,
        now: str,
    ) -> Dict[str, Any]:
        if required_consecutive_passes < 1:
            raise ValueError("required_consecutive_passes must be >= 1")
        if window_days < 1:
            raise ValueError("window_days must be >= 1")

        now_dt = _to_utc(now)
        cutoff = now_dt - timedelta(days=window_days)

        filtered = [
            item
            for item in self.history
            if "timestamp" in item and _to_utc(str(item["timestamp"])) >= cutoff
        ]
        filtered.sort(key=lambda item: _to_utc(str(item["timestamp"])))

        consecutive = 0
        for item in reversed(filtered):
            if bool(item.get("parity_pass")):
                consecutive += 1
            else:
                break

        return {
            "window_days": window_days,
            "required_consecutive_passes": required_consecutive_passes,
            "considered_runs": len(filtered),
            "consecutive_passes": consecutive,
            "pass": consecutive >= required_consecutive_passes,
            "history": filtered,
        }


def load_history(path: str | None) -> List[Dict[str, Any]]:
    if not path:
        return []
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        return []
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def save_history(path: str | None, history: List[Dict[str, Any]]) -> None:
    if not path:
        return
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(history, indent=2, sort_keys=True), encoding="utf-8")
