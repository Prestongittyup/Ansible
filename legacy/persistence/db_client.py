"""PostgreSQL client for Sprint 3 canonical persistence ingestion."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None


class DatabaseClientError(RuntimeError):
    """Raised when PostgreSQL operations fail."""


class PostgresClient:
    """Transaction-aware PostgreSQL client with deterministic upsert helpers."""

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise DatabaseClientError("PostgreSQL DSN is required")
        self._dsn = dsn
        self._conn = None

    def connect(self) -> None:
        if psycopg is None:  # pragma: no cover
            raise DatabaseClientError("psycopg is required for PostgreSQL access")
        if self._conn is None:
            try:
                self._conn = psycopg.connect(self._dsn)
            except Exception as exc:  # pragma: no cover
                raise DatabaseClientError(f"Failed to connect to PostgreSQL: {exc}") from exc

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.connect()
        assert self._conn is not None
        try:
            with self._conn.transaction():
                yield
        except Exception as exc:
            raise DatabaseClientError(f"Transaction failed: {exc}") from exc

    def apply_schema(self, schema_path: Path) -> None:
        try:
            sql = Path(schema_path).read_text(encoding="utf-8")
        except OSError as exc:
            raise DatabaseClientError(f"Failed to read schema SQL file: {exc}") from exc
        self.connect()
        assert self._conn is not None
        try:
            with self._conn.transaction():
                self._conn.execute(sql)
        except Exception as exc:
            raise DatabaseClientError(f"Failed to apply schema: {exc}") from exc

    def upsert_device(self, ip_address: str, vendor: str, last_seen: datetime | None = None) -> None:
        if not ip_address:
            raise DatabaseClientError("ip_address is required for upsert_device")
        if not vendor:
            raise DatabaseClientError("vendor is required for upsert_device")

        effective_last_seen = last_seen or datetime.now(timezone.utc)
        self.connect()
        assert self._conn is not None
        self._conn.execute(
            """
            INSERT INTO devices (ip_address, vendor, last_seen)
            VALUES (%s, %s, %s)
            ON CONFLICT (ip_address)
            DO UPDATE SET
                vendor = EXCLUDED.vendor,
                last_seen = EXCLUDED.last_seen
            WHERE devices.vendor IS DISTINCT FROM EXCLUDED.vendor
            """,
            (ip_address, vendor, effective_last_seen),
        )

    def upsert_validation_state(self, record: Dict[str, Any], last_updated: datetime | None = None) -> None:
        ip_address = str(record.get("ip_address", "")).strip()
        if not ip_address:
            raise DatabaseClientError("ip_address is required for upsert_validation_state")

        facts_collected = record.get("facts_collected")
        facts_payload = json.dumps(facts_collected) if facts_collected is not None else None
        attempt_count = int(record.get("attempt_count", 0))
        effective_last_updated = last_updated or datetime.now(timezone.utc)

        self.connect()
        assert self._conn is not None
        self._conn.execute(
            """
            INSERT INTO validation_state (
                ip_address,
                execution_state,
                connectivity_status,
                validation_status,
                facts_collected,
                attempt_count,
                last_updated
            )
            VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (ip_address)
            DO UPDATE SET
                execution_state = EXCLUDED.execution_state,
                connectivity_status = EXCLUDED.connectivity_status,
                validation_status = EXCLUDED.validation_status,
                facts_collected = EXCLUDED.facts_collected,
                attempt_count = EXCLUDED.attempt_count,
                last_updated = EXCLUDED.last_updated
            WHERE
                validation_state.execution_state IS DISTINCT FROM EXCLUDED.execution_state
                OR validation_state.connectivity_status IS DISTINCT FROM EXCLUDED.connectivity_status
                OR validation_state.validation_status IS DISTINCT FROM EXCLUDED.validation_status
                OR validation_state.facts_collected IS DISTINCT FROM EXCLUDED.facts_collected
                OR validation_state.attempt_count IS DISTINCT FROM EXCLUDED.attempt_count
            """,
            (
                ip_address,
                str(record.get("execution_state", "")),
                str(record.get("connectivity_status", "")),
                str(record.get("validation_status", "")),
                facts_payload,
                attempt_count,
                effective_last_updated,
            ),
        )

    def insert_validation_evidence(
        self,
        record: Dict[str, Any],
        run_id: str,
        timestamp: datetime | None = None,
        raw_payload: Dict[str, Any] | None = None,
    ) -> None:
        ip_address = str(record.get("ip_address", "")).strip()
        if not ip_address:
            raise DatabaseClientError("ip_address is required for insert_validation_evidence")
        if not run_id:
            raise DatabaseClientError("run_id is required for insert_validation_evidence")

        error_payload = record.get("error")
        error_json = json.dumps(error_payload) if error_payload is not None else None
        evidence_payload = raw_payload if raw_payload is not None else record
        raw_payload_json = json.dumps(evidence_payload, sort_keys=True)
        effective_timestamp = timestamp or datetime.now(timezone.utc)

        self.connect()
        assert self._conn is not None
        self._conn.execute(
            """
            INSERT INTO validation_evidence (
                run_id,
                ip_address,
                execution_state,
                error,
                raw_payload,
                timestamp
            )
            VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s)
            """,
            (
                run_id,
                ip_address,
                str(record.get("execution_state", "")),
                error_json,
                raw_payload_json,
                effective_timestamp,
            ),
        )
