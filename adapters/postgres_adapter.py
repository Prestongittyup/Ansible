from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from time import perf_counter
from typing import Any, Dict, List

from adapters.base import AdapterBase, AdapterError, AdapterRuntimeConfig
from observability.logger import classify_error, log_event
from runtime.mock import fixtures


@dataclass(frozen=True)
class PostgresConfig:
    mode: str
    host: str
    port: int
    user: str
    password: str
    database: str
    sqlite_path: str = ""
    connection_string: str = ""


class PostgresAdapter(AdapterBase):
    def __init__(self, config: PostgresConfig) -> None:
        super().__init__(AdapterRuntimeConfig(mode=config.mode, name="PostgresAdapter"))
        self._host = str(config.host or "")
        self._port = int(config.port)
        self._user = str(config.user or "")
        self._password = str(config.password or "")
        self._database = str(config.database or "")
        self._sqlite_path = str(config.sqlite_path or "").strip()
        self._connection_string = str(config.connection_string or "").strip()
        self._mock_store: Dict[str, Dict[str, Any]] = {}
        self._pool = None

    def _utc_now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _build_write_fingerprint(self, records: List[Dict[str, Any]]) -> str:
        rendered = json.dumps(records, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(rendered.encode("utf-8")).hexdigest()

    def _normalize_for_storage(self, record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ip_address": str(record.get("ip_address") or "").strip(),
            "hostname": str(record.get("hostname") or "").strip(),
            "serial_number": str(record.get("serial_number") or "").strip(),
            "vendor": str(record.get("vendor") or "").strip(),
            "model": str(record.get("model") or "").strip(),
            "site": str(record.get("site") or "").strip(),
            "status": str(record.get("status") or "unknown").strip(),
            "authority_owner": str(record.get("authority_owner") or "ANSIBLE_POSTGRES").strip(),
            "postgres_canonical": True,
            "sst_source": str(record.get("sst_source") or "ANSIBLE_POSTGRES").strip(),
            "last_seen": self._utc_now(),
        }

    def _read_mock_records(self) -> List[Dict[str, Any]]:
        records = [dict(self._mock_store[key]) for key in sorted(self._mock_store.keys())]
        return self._validate_records(records, source="mock_store") if records else []

    def _mock_fetch_phase2(self) -> List[Dict[str, Any]]:
        stored = self._read_mock_records()
        if stored:
            return stored
        records = fixtures.postgres_canonical_records_for_phase2()
        return self._validate_records(records, source="mock_phase2")

    def _mock_fetch_phase3(self) -> List[Dict[str, Any]]:
        records = fixtures.postgres_mirror_records_for_phase3()
        return self._validate_records(records, source="mock_phase3")

    def _mock_write_canonical_inventory(self, validated: List[Dict[str, Any]]) -> Dict[str, Any]:
        inserted = 0
        updated = 0
        for row in validated:
            canonical = self._normalize_for_storage(row)
            ip_address = canonical["ip_address"]
            if ip_address in self._mock_store:
                updated += 1
            else:
                inserted += 1
            self._mock_store[ip_address] = canonical

        return {
            "status": "PASS",
            "records_written": len(validated),
            "records_inserted": inserted,
            "records_updated": updated,
            "storage": "in_memory_emulation",
            "write_fingerprint": self._build_write_fingerprint(validated),
            "written_at": self._utc_now(),
        }

    def _build_dsn(self) -> str:
        if self._connection_string:
            return self._connection_string
        if not (self._host and self._user and self._password and self._database):
            raise AdapterError("Postgres DSN could not be constructed from configured fields")
        return (
            f"host={self._host} "
            f"port={self._port} "
            f"dbname={self._database} "
            f"user={self._user} "
            f"password={self._password}"
        )

    def _ensure_live_pool(self) -> None:
        if self._pool is not None:
            return

        started = perf_counter()
        log_event(
            level="INFO",
            component="adapter",
            adapter="postgres",
            operation="DB_POOL_INIT",
            status="START",
            external_system="postgres",
        )

        try:
            from psycopg2.pool import SimpleConnectionPool
        except Exception as exc:
            classified = classify_error(exc)
            log_event(
                level="ERROR",
                component="adapter",
                adapter="postgres",
                operation="DB_POOL_INIT",
                status="FAIL",
                duration_ms=int(round((perf_counter() - started) * 1000.0, 0)),
                external_system="postgres",
                **classified,
            )
            raise AdapterError("psycopg2 is required for LIVE Postgres integration") from exc

        dsn = self._build_dsn()
        try:
            self._pool = SimpleConnectionPool(minconn=1, maxconn=4, dsn=dsn)
        except Exception as exc:
            classified = classify_error(exc)
            log_event(
                level="ERROR",
                component="adapter",
                adapter="postgres",
                operation="DB_POOL_INIT",
                status="FAIL",
                duration_ms=int(round((perf_counter() - started) * 1000.0, 0)),
                external_system="postgres",
                **classified,
            )
            raise AdapterError(f"Failed to initialize Postgres connection pool: {exc}") from exc

        log_event(
            level="INFO",
            component="adapter",
            adapter="postgres",
            operation="DB_POOL_INIT",
            status="SUCCESS",
            duration_ms=int(round((perf_counter() - started) * 1000.0, 0)),
            external_system="postgres",
        )

    def _borrow_connection(self):
        if self._pool is None:
            raise AdapterError("Postgres connection pool is not initialized")
        connection = self._pool.getconn()
        if connection is None:
            raise AdapterError("Failed to acquire Postgres connection")
        return connection

    def _return_connection(self, connection: Any) -> None:
        if self._pool is not None and connection is not None:
            self._pool.putconn(connection)

    def _ensure_postgres_schema(self) -> None:
        connection = self._borrow_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS canonical_inventory (
                        ip_address TEXT PRIMARY KEY,
                        payload JSONB NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
            connection.commit()
        except Exception as exc:
            connection.rollback()
            raise AdapterError(f"Postgres schema initialization failed: {exc}") from exc
        finally:
            self._return_connection(connection)

    def _live_fetch_phase2(self) -> List[Dict[str, Any]]:
        started = perf_counter()
        log_event(
            level="INFO",
            component="adapter",
            adapter="postgres",
            operation="SELECT canonical_inventory",
            status="START",
            external_system="postgres",
        )
        self._ensure_live_pool()
        self._ensure_postgres_schema()

        connection = self._borrow_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT payload FROM canonical_inventory ORDER BY ip_address ASC")
                rows = cursor.fetchall()
        except Exception as exc:
            classified = classify_error(exc)
            log_event(
                level="ERROR",
                component="adapter",
                adapter="postgres",
                operation="SELECT canonical_inventory",
                status="FAIL",
                duration_ms=int(round((perf_counter() - started) * 1000.0, 0)),
                external_system="postgres",
                **classified,
            )
            raise AdapterError(f"Postgres phase2 fetch failed: {exc}") from exc
        finally:
            self._return_connection(connection)

        records: List[Dict[str, Any]] = []
        for row in rows:
            payload = row[0] if row and len(row) > 0 else {}
            if isinstance(payload, str):
                payload = json.loads(payload)
            if isinstance(payload, dict):
                records.append(payload)

        if not records:
            raise AdapterError("No live canonical records available")

        log_event(
            level="INFO",
            component="adapter",
            adapter="postgres",
            operation="SELECT canonical_inventory",
            status="SUCCESS",
            duration_ms=int(round((perf_counter() - started) * 1000.0, 0)),
            external_system="postgres",
            records=len(records),
        )

        return self._validate_records(records, source="live_phase2")

    def _live_fetch_phase3(self) -> List[Dict[str, Any]]:
        phase2_records = self._live_fetch_phase2()
        for row in phase2_records:
            row["authority_owner"] = "NAUTOBOT"
            row["sst_source"] = "NAUTOBOT"
            row["nautobot_authoritative"] = True
        return self._validate_records(phase2_records, source="live_phase3")

    def _live_write_canonical_inventory(self, validated: List[Dict[str, Any]]) -> Dict[str, Any]:
        started = perf_counter()
        log_event(
            level="INFO",
            component="adapter",
            adapter="postgres",
            operation="UPSERT canonical_inventory",
            status="START",
            external_system="postgres",
            records=len(validated),
        )
        self._ensure_live_pool()
        self._ensure_postgres_schema()

        inserted = 0
        updated = 0

        connection = self._borrow_connection()
        try:
            connection.autocommit = False
            with connection.cursor() as cursor:
                for row in validated:
                    canonical = self._normalize_for_storage(row)
                    ip_address = canonical["ip_address"]
                    if not ip_address:
                        raise AdapterError("Canonical record missing ip_address")

                    cursor.execute(
                        "SELECT 1 FROM canonical_inventory WHERE ip_address = %s",
                        (ip_address,),
                    )
                    existing = cursor.fetchone()
                    if existing:
                        updated += 1
                    else:
                        inserted += 1

                    cursor.execute(
                        """
                        INSERT INTO canonical_inventory(ip_address, payload, updated_at)
                        VALUES (%s, %s::jsonb, %s::timestamptz)
                        ON CONFLICT(ip_address)
                        DO UPDATE SET payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at
                        """,
                        (
                            ip_address,
                            json.dumps(canonical, sort_keys=True),
                            self._utc_now(),
                        ),
                    )
            connection.commit()
        except Exception as exc:
            connection.rollback()
            classified = classify_error(exc)
            log_event(
                level="ERROR",
                component="adapter",
                adapter="postgres",
                operation="UPSERT canonical_inventory",
                status="FAIL",
                duration_ms=int(round((perf_counter() - started) * 1000.0, 0)),
                external_system="postgres",
                records=len(validated),
                **classified,
            )
            raise AdapterError(f"Postgres canonical write failed: {exc}") from exc
        finally:
            self._return_connection(connection)

        log_event(
            level="INFO",
            component="adapter",
            adapter="postgres",
            operation="UPSERT canonical_inventory",
            status="SUCCESS",
            duration_ms=int(round((perf_counter() - started) * 1000.0, 0)),
            external_system="postgres",
            records=len(validated),
        )

        return {
            "status": "PASS",
            "records_written": len(validated),
            "records_inserted": inserted,
            "records_updated": updated,
            "storage": "postgres_live",
            "write_fingerprint": self._build_write_fingerprint(validated),
            "written_at": self._utc_now(),
        }

    def fetch_phase2_canonical_inventory(self) -> List[Dict[str, Any]]:
        return self._run_mode_operation(
            operation="fetch_phase2_canonical_inventory",
            live_callable=self._live_fetch_phase2,
            mock_callable=self._mock_fetch_phase2,
        )

    def fetch_phase3_mirror_inventory(self) -> List[Dict[str, Any]]:
        return self._run_mode_operation(
            operation="fetch_phase3_mirror_inventory",
            live_callable=self._live_fetch_phase3,
            mock_callable=self._mock_fetch_phase3,
        )

    def write_canonical_inventory(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        validated = self._validate_records(records, source="write_input")
        return self._run_mode_operation(
            operation="write_canonical_inventory",
            live_callable=lambda: self._live_write_canonical_inventory(validated),
            mock_callable=lambda: self._mock_write_canonical_inventory(validated),
        )

    def validate_credentials(self) -> Dict[str, Any]:
        if self.mode == "MOCK":
            status = {
                "status": "PASS",
                "adapter": self.name,
                "mode": self.mode,
                "live_ready": False,
                "message": "mock mode does not require credentials",
            }
            self._record_operation_status(
                operation="validate_credentials",
                status="PASS",
                mode_used=self.mode,
                details={"live_ready": False},
            )
            return status

        has_connection_string = bool(self._connection_string)
        has_legacy_tuple = bool(self._host and self._user and self._password and self._database)

        missing: List[str] = []
        if not has_connection_string and not has_legacy_tuple:
            missing.append("POSTGRES_CONNECTION_STRING|POSTGRES_HOST+POSTGRES_USER+POSTGRES_PASSWORD+POSTGRES_DATABASE")

        if missing and self.mode == "LIVE":
            self._record_operation_status(
                operation="validate_credentials",
                status="FAIL",
                mode_used=self.mode,
                details={"missing": missing},
            )
            raise AdapterError("Postgres missing credentials: " + ", ".join(missing))

        status_token = "PASS" if not missing else "WARN"
        self._record_operation_status(
            operation="validate_credentials",
            status=status_token,
            mode_used=self.mode,
            details={"missing": missing, "live_ready": len(missing) == 0},
        )
        return {
            "status": status_token,
            "adapter": self.name,
            "mode": self.mode,
            "missing": missing,
            "live_ready": len(missing) == 0,
        }

    def init(self) -> Dict[str, Any]:
        try:
            backend = "mock"
            if self.mode in {"LIVE", "HYBRID"}:
                if self._sqlite_path and self.mode == "LIVE":
                    raise AdapterError("POSTGRES_SQLITE_PATH is not allowed in LIVE mode")
                self._ensure_live_pool()
                self._ensure_postgres_schema()
                backend = "postgres_live"

            base_status = super().init()
            base_status["backend"] = backend
            return base_status
        except Exception as exc:
            self._record_operation_status(
                operation="init",
                status="FAIL",
                mode_used=self.mode,
                details={"error": str(exc)},
            )
            raise

    def health_check(self, *, phase: str = "") -> Dict[str, Any]:
        try:
            if self.mode == "MOCK":
                status = {
                    "status": "PASS",
                    "adapter": self.name,
                    "mode": self.mode,
                    "phase": str(phase),
                    "backend": "mock",
                    "check": "schema_validation",
                }
                self._record_operation_status(
                    operation="health_check",
                    status="PASS",
                    mode_used=self.mode,
                    details={"backend": "mock", "phase": str(phase)},
                )
                return status

            self._ensure_live_pool()
            self._ensure_postgres_schema()

            connection = self._borrow_connection()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.execute(
                        """
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = 'public' AND table_name = 'canonical_inventory'
                        """
                    )
                    table = cursor.fetchone()
            finally:
                self._return_connection(connection)

            if not table:
                raise AdapterError("Postgres schema validation failed: canonical_inventory table not found")

            status = {
                "status": "PASS",
                "adapter": self.name,
                "mode": self.mode,
                "phase": str(phase),
                "backend": "postgres_live",
                "check": "schema_validation",
            }
            self._record_operation_status(
                operation="health_check",
                status="PASS",
                mode_used=self.mode,
                details={"backend": "postgres_live", "phase": str(phase)},
            )
            return status
        except Exception as exc:
            self._record_operation_status(
                operation="health_check",
                status="FAIL",
                mode_used=self.mode,
                details={"error": str(exc), "phase": str(phase)},
            )
            raise

    def register(self) -> Dict[str, Any]:
        return super().register()

    def shutdown(self) -> Dict[str, Any]:
        if self._pool is not None:
            self._pool.closeall()
            self._pool = None
        return super().shutdown()