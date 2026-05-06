from __future__ import annotations

import socket
from typing import Any, Dict
from urllib import parse as urllib_parse


class ConnectionManagerError(RuntimeError):
    """Raised when a connectivity operation fails."""


class ConnectionManager:
    def __init__(self, *, timeout_seconds: int = 5) -> None:
        self._timeout_seconds = int(timeout_seconds)

    def probe_endpoint(self, *, endpoint: str) -> Dict[str, Any]:
        parsed = urllib_parse.urlparse(str(endpoint))
        host = str(parsed.hostname or "").strip()
        if not host:
            raise ConnectionManagerError(f"Endpoint host is missing: {endpoint}")

        if parsed.port is not None:
            port = int(parsed.port)
        elif str(parsed.scheme).lower() == "https":
            port = 443
        else:
            port = 80

        dns = self.dns_lookup(host=host)
        tcp = self.tcp_connect(host=host, port=port)
        status = "PASS" if dns["status"] == "PASS" and tcp["status"] == "PASS" else "FAIL"
        return {
            "status": status,
            "endpoint": endpoint,
            "host": host,
            "port": port,
            "dns": dns,
            "tcp": tcp,
        }

    def dns_lookup(self, *, host: str) -> Dict[str, Any]:
        try:
            address = socket.gethostbyname(str(host))
            return {
                "status": "PASS",
                "host": str(host),
                "address": str(address),
            }
        except Exception as exc:
            return {
                "status": "FAIL",
                "host": str(host),
                "reason": str(exc),
            }

    def tcp_connect(self, *, host: str, port: int) -> Dict[str, Any]:
        try:
            with socket.create_connection((str(host), int(port)), timeout=self._timeout_seconds):
                return {
                    "status": "PASS",
                    "host": str(host),
                    "port": int(port),
                }
        except Exception as exc:
            return {
                "status": "FAIL",
                "host": str(host),
                "port": int(port),
                "reason": str(exc),
            }

    def postgres_transaction_handshake(self, *, connection_string: str) -> Dict[str, Any]:
        connection_value = str(connection_string or "").strip()
        if not connection_value:
            return {
                "status": "FAIL",
                "reason": "POSTGRES_CONNECTION_STRING is missing",
            }

        try:
            import psycopg2
        except Exception as exc:
            return {
                "status": "FAIL",
                "reason": f"psycopg2 unavailable: {exc}",
            }

        connection = None
        try:
            connection = psycopg2.connect(connection_value, connect_timeout=self._timeout_seconds)
            connection.autocommit = False
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
                cursor.execute("CREATE TEMP TABLE IF NOT EXISTS bootstrap_probe(id INT)")
            connection.rollback()
            return {
                "status": "PASS",
                "transaction_handshake": "ok",
            }
        except Exception as exc:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
            return {
                "status": "FAIL",
                "reason": str(exc),
            }
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
