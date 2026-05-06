"""Tests for Sprint 3 canonical persistence ingestion contract."""

from __future__ import annotations

import copy
import unittest
from contextlib import contextmanager

from legacy.persistence.contract_validator import ContractValidationError
from legacy.persistence.ingest_validation import ingest_payload


class FakeDbClient:
    def __init__(self) -> None:
        self.schema_applied = False
        self._clock = 0
        self.devices = {}
        self.validation_state = {}
        self.validation_evidence = []

    def _next_timestamp(self):
        self._clock += 1
        return self._clock

    def apply_schema(self, schema_sql_path) -> None:
        self.schema_applied = True

    @contextmanager
    def transaction(self):
        snapshot_devices = copy.deepcopy(self.devices)
        snapshot_state = copy.deepcopy(self.validation_state)
        snapshot_evidence = copy.deepcopy(self.validation_evidence)
        try:
            yield
        except Exception:
            self.devices = snapshot_devices
            self.validation_state = snapshot_state
            self.validation_evidence = snapshot_evidence
            raise

    def upsert_device(self, ip_address, vendor) -> None:
        existing = self.devices.get(ip_address)
        if existing and existing["vendor"] == vendor:
            return
        self.devices[ip_address] = {
            "ip_address": ip_address,
            "vendor": vendor,
            "last_seen": self._next_timestamp(),
        }

    def upsert_validation_state(self, record) -> None:
        ip_address = record["ip_address"]
        new_state = {
            "execution_state": record["execution_state"],
            "connectivity_status": record["connectivity_status"],
            "validation_status": record["validation_status"],
            "facts_collected": record["facts_collected"],
            "attempt_count": record["attempt_count"],
            "schema_version": record["schema_version"],
        }
        existing = self.validation_state.get(ip_address)
        if existing:
            comparable = {k: existing[k] for k in new_state.keys()}
            if comparable == new_state:
                return
            new_state["last_updated"] = self._next_timestamp()
        else:
            new_state["last_updated"] = self._next_timestamp()

        self.validation_state[ip_address] = new_state

    def insert_validation_evidence(self, record, run_id, timestamp=None, raw_payload=None) -> None:
        self.validation_evidence.append(
            {
                "run_id": run_id,
                "ip_address": record["ip_address"],
                "execution_state": record["execution_state"],
                "error": record["error"],
                "timestamp": self._next_timestamp() if timestamp is None else timestamp,
                "raw_payload": copy.deepcopy(raw_payload if raw_payload is not None else record),
            }
        )


def _record(ip_address: str, execution_state: str = "SUCCESS"):
    if execution_state == "SUCCESS":
        connectivity_status = "REACHABLE"
        validation_status = "PASSED"
    elif execution_state == "FAIL_VALIDATION":
        connectivity_status = "REACHABLE"
        validation_status = "FAILED"
    else:
        connectivity_status = "NOT_EXECUTED"
        validation_status = "NOT_EXECUTED"

    return {
        "ip_address": ip_address,
        "vendor": "aruba",
        "execution_state": execution_state,
        "connectivity_status": connectivity_status,
        "validation_status": validation_status,
        "facts_collected": {"hostname": f"sw-{ip_address.replace('.', '-') }"},
        "error": (
            None
            if execution_state == "SUCCESS"
            else {
                "code": "TEST_FAIL",
                "message": "test failure",
                "source": "unit_test",
                "details": {"reason": "synthetic"},
            }
        ),
        "attempt_count": 1,
        "schema_version": "2.1",
    }


def _payload(records):
    return {
        "metadata": {
            "run_id": "run-001",
            "schema_version": "2.1",
        },
        "results": records,
    }


class IngestValidationTests(unittest.TestCase):
    def test_happy_path_ingests_all_records(self) -> None:
        payload = _payload([_record("10.0.0.1"), _record("10.0.0.2", "FAIL_RUNTIME")])
        db = FakeDbClient()

        result = ingest_payload(payload, db_client=db, run_id="run-001")

        self.assertEqual("PASS", result["ingestion_status"])
        self.assertEqual(2, result["records_processed"])
        self.assertEqual(2, result["records_inserted"])
        self.assertEqual(0, result["records_dropped"])
        self.assertEqual(2, len(db.devices))
        self.assertEqual(2, len(db.validation_state))
        self.assertEqual(2, len(db.validation_evidence))

    def test_missing_ip_record_is_dropped(self) -> None:
        bad = _record("10.0.0.3")
        del bad["ip_address"]
        payload = _payload([_record("10.0.0.1"), bad])
        db = FakeDbClient()

        with self.assertLogs("persistence.ingest_validation", level="WARNING"):
            result = ingest_payload(payload, db_client=db, run_id="run-001")

        self.assertEqual("PASS", result["ingestion_status"])
        self.assertEqual(2, result["records_processed"])
        self.assertEqual(1, result["records_inserted"])
        self.assertEqual(1, result["records_dropped"])
        self.assertEqual(1, len(db.validation_evidence))

    def test_schema_version_mismatch_rejects_run(self) -> None:
        payload = _payload([_record("10.0.0.1")])
        payload["metadata"]["schema_version"] = "2.0"
        db = FakeDbClient()

        with self.assertRaises(ContractValidationError):
            ingest_payload(payload, db_client=db, run_id="run-001")

        self.assertEqual(0, len(db.devices))
        self.assertEqual(0, len(db.validation_state))
        self.assertEqual(0, len(db.validation_evidence))

    def test_invalid_enum_rejects_run(self) -> None:
        payload = _payload([_record("10.0.0.1")])
        payload["results"][0]["validation_status"] = "PASS"
        db = FakeDbClient()

        with self.assertRaises(ContractValidationError):
            ingest_payload(payload, db_client=db, run_id="run-001")

        self.assertEqual(0, len(db.devices))
        self.assertEqual(0, len(db.validation_state))
        self.assertEqual(0, len(db.validation_evidence))

    def test_malformed_error_object_rejects_run(self) -> None:
        payload = _payload([_record("10.0.0.1")])
        payload["results"][0]["error"] = {"code": "BROKEN"}
        db = FakeDbClient()

        with self.assertRaises(ContractValidationError):
            ingest_payload(payload, db_client=db, run_id="run-001")

        self.assertEqual(0, len(db.devices))
        self.assertEqual(0, len(db.validation_state))
        self.assertEqual(0, len(db.validation_evidence))

    def test_duplicate_run_keeps_state_and_appends_evidence(self) -> None:
        payload = _payload([_record("10.0.0.1", "SUCCESS")])
        db = FakeDbClient()

        first = ingest_payload(payload, db_client=db, run_id="run-001")
        first_state_timestamp = db.validation_state["10.0.0.1"]["last_updated"]
        second = ingest_payload(payload, db_client=db, run_id="run-001")
        second_state_timestamp = db.validation_state["10.0.0.1"]["last_updated"]

        self.assertEqual("PASS", first["ingestion_status"])
        self.assertEqual("PASS", second["ingestion_status"])
        self.assertEqual(1, len(db.validation_state))
        self.assertEqual(first_state_timestamp, second_state_timestamp)
        self.assertEqual(2, len(db.validation_evidence))
        self.assertIn("timestamp", db.validation_evidence[0])
        self.assertIn("raw_payload", db.validation_evidence[0])

    def test_transaction_rolls_back_on_failure(self) -> None:
        payload = _payload([_record("10.0.0.1", "SUCCESS"), _record("10.0.0.2", "SUCCESS")])

        class FailingDbClient(FakeDbClient):
            def __init__(self) -> None:
                super().__init__()
                self._failed_once = False

            def insert_validation_evidence(self, record, run_id, timestamp=None, raw_payload=None) -> None:
                super().insert_validation_evidence(record, run_id, timestamp=timestamp, raw_payload=raw_payload)
                if not self._failed_once:
                    self._failed_once = True
                    raise RuntimeError("simulated evidence write failure")

        db = FailingDbClient()

        with self.assertRaises(RuntimeError):
            ingest_payload(payload, db_client=db, run_id="run-001")

        self.assertEqual({}, db.devices)
        self.assertEqual({}, db.validation_state)
        self.assertEqual([], db.validation_evidence)


if __name__ == "__main__":
    unittest.main()
