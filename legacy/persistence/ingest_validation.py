"""Ingest validated CI output (schema 2.1) into PostgreSQL canonical persistence tables."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from legacy.persistence.db_client import DatabaseClientError, PostgresClient
from legacy.persistence.contract_validator import ContractValidationError, validate_payload

LOGGER = logging.getLogger("persistence.ingest_validation")

REQUIRED_SCHEMA_VERSION = "2.1"

DEFAULT_INPUT_PATH = Path("ansible_validation/outputs/validation_results.json")
DEFAULT_SCHEMA_PATH = Path("persistence/schema.sql")


class IngestionContractError(RuntimeError):
    """Raised when the validated output contract is violated."""


def _result(
    status: str,
    records_processed: int,
    records_inserted: int,
    records_dropped: int,
    schema_version_valid: bool,
    identity_enforced: bool,
    state_evidence_separation: bool,
) -> Dict[str, Any]:
    return {
        "ingestion_status": status,
        "records_processed": records_processed,
        "records_inserted": records_inserted,
        "records_dropped": records_dropped,
        "schema_version_valid": schema_version_valid,
        "identity_enforced": identity_enforced,
        "state_evidence_separation": state_evidence_separation,
    }


def load_validation_results(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise IngestionContractError(f"Input file not found: {path}")

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IngestionContractError(f"Input file is not valid JSON: {exc}") from exc


def ingest_payload(
    payload: Dict[str, Any],
    db_client: Any,
    run_id: str,
    schema_sql_path: Path | None = None,
) -> Dict[str, Any]:
    records_processed = 0
    records_inserted = 0
    records_dropped = 0

    accepted, dropped = validate_payload(payload)
    records_processed = len(payload.get("results", [])) if isinstance(payload.get("results"), list) else 0
    records_dropped = dropped

    if dropped:
        LOGGER.warning("Dropped %s records due to missing/empty ip_address", dropped)

    if schema_sql_path is not None:
        db_client.apply_schema(schema_sql_path)

    with db_client.transaction():
        for _, record in accepted:
            db_client.upsert_device(record["ip_address"], record["vendor"])
            db_client.upsert_validation_state(record)
            db_client.insert_validation_evidence(record, run_id, raw_payload=record)

    records_inserted = len(accepted)

    return _result(
        status="PASS",
        records_processed=records_processed,
        records_inserted=records_inserted,
        records_dropped=records_dropped,
        schema_version_valid=True,
        identity_enforced=True,
        state_evidence_separation=True,
    )


def ingest_validation_file(
    input_path: Path,
    db_client: Any,
    run_id: str,
    schema_sql_path: Path,
) -> Dict[str, Any]:
    payload = load_validation_results(input_path)
    return ingest_payload(payload, db_client=db_client, run_id=run_id, schema_sql_path=schema_sql_path)


def run_ingestion(
    *,
    input_path: Path,
    schema_sql_path: Path,
    database_url: str,
    run_id: str | None,
) -> Dict[str, Any]:
    records_processed = 0
    records_dropped = 0

    try:
        payload = load_validation_results(input_path)
        if isinstance(payload.get("results"), list):
            records_processed = len(payload["results"])

        metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {}
        effective_run_id = run_id or str(metadata.get("run_id", "")).strip()
        if not effective_run_id:
            raise IngestionContractError("run_id is required (metadata.run_id or --run-id)")

        client = PostgresClient(database_url)
        try:
            result = ingest_payload(
                payload,
                db_client=client,
                run_id=effective_run_id,
                schema_sql_path=schema_sql_path,
            )
            return result
        finally:
            client.close()
    except IngestionContractError as exc:
        LOGGER.error("Ingestion contract failure: %s", exc)
        if "missing ip_address" in str(exc).lower() or "ip_address" in str(exc).lower():
            records_dropped = max(records_dropped, 0)
        schema_ok = "schema_version" not in str(exc)
        return _result(
            status="FAIL",
            records_processed=records_processed,
            records_inserted=0,
            records_dropped=records_dropped,
            schema_version_valid=schema_ok,
            identity_enforced=True,
            state_evidence_separation=True,
        )
    except ContractValidationError as exc:
        LOGGER.error("Contract validation failure: %s", json.dumps(exc.to_dict(), sort_keys=True))
        return _result(
            status="FAIL",
            records_processed=records_processed,
            records_inserted=0,
            records_dropped=records_dropped,
            schema_version_valid=exc.code != "INVALID_SCHEMA_VERSION",
            identity_enforced=exc.code != "MISSING_REQUIRED_FIELDS",
            state_evidence_separation=True,
        )
    except DatabaseClientError as exc:
        LOGGER.error("Database write failure: %s", exc)
        return _result(
            status="FAIL",
            records_processed=records_processed,
            records_inserted=0,
            records_dropped=records_dropped,
            schema_version_valid=True,
            identity_enforced=True,
            state_evidence_separation=True,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest validated CI output into canonical PostgreSQL state")
    parser.add_argument("--input", default=str(DEFAULT_INPUT_PATH), help="Path to validation_results.json")
    parser.add_argument("--schema-sql", default=str(DEFAULT_SCHEMA_PATH), help="Path to schema SQL file")
    parser.add_argument("--db-url", default=os.getenv("DATABASE_URL", ""), help="PostgreSQL connection URL")
    parser.add_argument("--run-id", default=None, help="Optional run identifier override")
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args()

    if not args.db_url:
        result = _result(
            status="FAIL",
            records_processed=0,
            records_inserted=0,
            records_dropped=0,
            schema_version_valid=False,
            identity_enforced=True,
            state_evidence_separation=True,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1

    result = run_ingestion(
        input_path=Path(args.input),
        schema_sql_path=Path(args.schema_sql),
        database_url=args.db_url,
        run_id=args.run_id,
    )

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ingestion_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
