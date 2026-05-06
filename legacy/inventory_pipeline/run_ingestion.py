#!/usr/bin/env python3
"""Sprint 1 LogicMonitor ingestion runner.

Scope:
- LogicMonitor ingestion (simulated by default)
- IP-based normalization
- Vendor classification
- JSON inventory output
- Structured logging
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

try:
    from legacy.inventory_pipeline.ingestion.inventory_builder import build_inventory_json
    from legacy.inventory_pipeline.ingestion.logicmonitor_client import LogicMonitorClient, LogicMonitorConfig
    from legacy.inventory_pipeline.ingestion.normalizer import normalize_logicmonitor_devices
    from legacy.inventory_pipeline.ingestion.vendor_classifier import apply_vendor_classification
except ModuleNotFoundError:
    from ingestion.inventory_builder import build_inventory_json
    from ingestion.logicmonitor_client import LogicMonitorClient, LogicMonitorConfig
    from ingestion.normalizer import normalize_logicmonitor_devices
    from ingestion.vendor_classifier import apply_vendor_classification


class JsonLogFormatter(logging.Formatter):
    """Compact JSON formatter for structured logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        return json.dumps(payload, sort_keys=True)


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("inventory_ingestion")
    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sprint 1 LogicMonitor ingestion pipeline")
    parser.add_argument(
        "--mode",
        choices=["simulate", "live"],
        default="simulate",
        help="Execution mode for LogicMonitor fetch. Default is simulate.",
    )
    parser.add_argument(
        "--output",
        default="inventory_output.json",
        help="Output JSON file path.",
    )
    parser.add_argument(
        "--page-limit",
        type=int,
        default=500,
        help="Pagination limit per API request.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Max retries for page fetch operations.",
    )
    parser.add_argument(
        "--simulated-device-total",
        type=int,
        default=2500,
        help="Total simulated devices when mode=simulate.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Optional run identifier.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logger = _build_logger()

    run_id = args.run_id or datetime.now(timezone.utc).strftime("ingestion-%Y%m%dT%H%M%SZ")
    logger.info(json.dumps({"event": "run_start", "run_id": run_id, "mode": args.mode}, sort_keys=True))

    config = LogicMonitorConfig(
        base_url="https://<LM_INSTANCE>/api/v3",
        account="<ACCOUNT_ID>",
        token="<TOKEN>",
    )

    try:
        client = LogicMonitorClient(
            config,
            timeout_seconds=20,
            page_limit=args.page_limit,
            max_retries=args.max_retries,
            simulate=(args.mode == "simulate"),
            simulated_device_total=args.simulated_device_total,
            logger=logger,
        )

        fetched = client.fetch_devices()
        normalized = normalize_logicmonitor_devices(fetched.devices, logger=logger)
        classified = apply_vendor_classification(normalized.records, logger=logger)
        built = build_inventory_json(classified, run_id=run_id, logger=logger)

        output_path = Path(args.output)
        output_path.write_text(json.dumps(built.inventory, indent=2, sort_keys=True), encoding="utf-8")

        summary = {
            "run_id": run_id,
            "fetched_records": len(fetched.devices),
            "pages_fetched": fetched.pages_fetched,
            "retries_performed": fetched.retries_performed,
            "dropped_missing_ip": normalized.dropped_missing_ip,
            "dropped_invalid_ip": normalized.dropped_invalid_ip,
            "valid_records": len(classified),
            "duplicate_ip_discarded": built.duplicate_ip_count,
            "output_file": str(output_path),
            "status": "SUCCESS",
        }

        logger.info(json.dumps({"event": "run_complete", **summary}, sort_keys=True))
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    except Exception as exc:
        failure = {
            "run_id": run_id,
            "status": "BLOCKED",
            "reason_code": "INGESTION_FAILURE",
            "error": str(exc),
        }
        logger.error(json.dumps({"event": "run_failed", **failure}, sort_keys=True))
        print(json.dumps(failure, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
