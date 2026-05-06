"""Read-only PostgreSQL access client for Sprint 4 query layer."""

from __future__ import annotations

from typing import Any, Dict, List

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover
    psycopg = None
    dict_row = None


class QueryClientError(RuntimeError):
    """Raised when read-only database query operations fail."""


class QueryClient:
    """Read-only DB client with parameterized queries and deterministic ordering."""

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise QueryClientError("PostgreSQL DSN is required")
        self._dsn = dsn
        self._conn = None

    def connect(self) -> None:
        if psycopg is None or dict_row is None:  # pragma: no cover
            raise QueryClientError("psycopg is required for query layer database access")
        if self._conn is None:
            try:
                self._conn = psycopg.connect(self._dsn, row_factory=dict_row)
            except Exception as exc:  # pragma: no cover
                raise QueryClientError(f"Failed to connect to PostgreSQL: {exc}") from exc

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _fetch_one(self, sql: str, params: tuple[Any, ...]) -> Dict[str, Any] | None:
        self.connect()
        assert self._conn is not None
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(sql, params)
                row = cursor.fetchone()
            return dict(row) if row is not None else None
        except Exception as exc:
            raise QueryClientError(f"Read query failed: {exc}") from exc

    def _fetch_all(self, sql: str, params: tuple[Any, ...]) -> List[Dict[str, Any]]:
        self.connect()
        assert self._conn is not None
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(sql, params)
                rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as exc:
            raise QueryClientError(f"Read query failed: {exc}") from exc

    def get_device(self, ip_address: str) -> Dict[str, Any] | None:
        return self._fetch_one(
            """
            SELECT ip_address, vendor, last_seen
            FROM devices
            WHERE ip_address = %s
            """,
            (ip_address,),
        )

    def list_devices_by_vendor(self, vendor: str) -> List[Dict[str, Any]]:
        return self._fetch_all(
            """
            SELECT ip_address, vendor, last_seen
            FROM devices
            WHERE vendor = %s
            ORDER BY ip_address ASC
            """,
            (vendor,),
        )

    def list_devices_by_execution_state(self, execution_state: str) -> List[Dict[str, Any]]:
        return self._fetch_all(
            """
            SELECT d.ip_address, d.vendor, vs.execution_state, vs.last_updated
            FROM validation_state AS vs
            INNER JOIN devices AS d
                ON d.ip_address = vs.ip_address
            WHERE vs.execution_state = %s
            ORDER BY d.ip_address ASC
            """,
            (execution_state,),
        )

    def get_latest_validation_state(self, ip_address: str) -> Dict[str, Any] | None:
        return self._fetch_one(
            """
            SELECT
                ip_address,
                execution_state,
                connectivity_status,
                validation_status,
                facts_collected,
                attempt_count,
                last_updated
            FROM validation_state
            WHERE ip_address = %s
            """,
            (ip_address,),
        )

    def get_validation_evidence(self, ip_address: str, limit: int, offset: int) -> List[Dict[str, Any]]:
        return self._fetch_all(
            """
            SELECT ip_address, run_id, timestamp, execution_state, raw_payload
            FROM validation_evidence
            WHERE ip_address = %s
            ORDER BY timestamp ASC, run_id ASC
            LIMIT %s OFFSET %s
            """,
            (ip_address, limit, offset),
        )

    def get_evidence_by_run(self, run_id: str, limit: int, offset: int) -> List[Dict[str, Any]]:
        return self._fetch_all(
            """
            SELECT ip_address, run_id, timestamp, execution_state, raw_payload
            FROM validation_evidence
            WHERE run_id = %s
            ORDER BY timestamp ASC, ip_address ASC
            LIMIT %s OFFSET %s
            """,
            (run_id, limit, offset),
        )

    def get_evidence_by_time_range(
        self,
        start_timestamp: str,
        end_timestamp: str,
        limit: int,
        offset: int,
    ) -> List[Dict[str, Any]]:
        return self._fetch_all(
            """
            SELECT ip_address, run_id, timestamp, execution_state, raw_payload
            FROM validation_evidence
            WHERE timestamp >= %s AND timestamp <= %s
            ORDER BY timestamp ASC, ip_address ASC, run_id ASC
            LIMIT %s OFFSET %s
            """,
            (start_timestamp, end_timestamp, limit, offset),
        )
