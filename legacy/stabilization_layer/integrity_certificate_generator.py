"""Integrity certification report generator for Sprint 12."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

from legacy.stabilization_layer.cross_sprint_drift_comparator import (
    compare_cross_sprint_baselines,
    enforce_zero_frozen_drift,
)
from legacy.stabilization_layer.determinism_certification_engine import (
    certify_unified_runner_determinism,
    enforce_determinism,
)
from legacy.stabilization_layer.execution_replay_validator import (
    enforce_replay_consistency,
    validate_execution_replay,
)

SCHEMA_VERSION = "2.1"


class IntegrityCertificateError(RuntimeError):
    """Raised when certificate generation detects fail-closed conditions."""


def _as_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _score(drift_ok: bool, determinism_ok: bool, replay_ok: bool) -> tuple[int, int, int]:
    drift_score = 0 if drift_ok else 100
    determinism_score = 100 if determinism_ok else 0
    compliance_score = 100 if (drift_ok and determinism_ok and replay_ok) else 0
    return drift_score, determinism_score, compliance_score


def generate_system_integrity_certificate(
    *,
    root: Path,
    sprint10_baseline: str | Path,
    sprint11_baseline: str | Path,
    sprint12_baseline: str | Path,
    output_path: str | Path | None = None,
    python_executable: str | None = None,
    run_count: int = 2,
) -> dict[str, Any]:
    baseline12 = json.loads(Path(sprint12_baseline).read_text(encoding="utf-8"))
    reference_hash = str(baseline12.get("determinism_baseline", {}).get("reference_hash", "")) or None

    drift_report = compare_cross_sprint_baselines(
        sprint10_baseline=sprint10_baseline,
        sprint11_baseline=sprint11_baseline,
        sprint12_baseline=sprint12_baseline,
        root=root,
    )

    determinism_report = certify_unified_runner_determinism(
        root=root,
        python_executable=python_executable,
        run_count=run_count,
        reference_hash=reference_hash,
    )

    replay_report = validate_execution_replay(
        root=root,
        python_executable=python_executable,
        replay_count=2,
    )

    drift_ok = False
    determinism_ok = False
    replay_ok = False

    try:
        enforce_zero_frozen_drift(drift_report)
        drift_ok = True
    except Exception:
        drift_ok = False

    try:
        enforce_determinism(determinism_report)
        determinism_ok = True
    except Exception:
        determinism_ok = False

    try:
        enforce_replay_consistency(replay_report)
        replay_ok = True
    except Exception:
        replay_ok = False

    drift_score, determinism_score, compliance_score = _score(drift_ok, determinism_ok, replay_ok)

    certificate = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _as_iso_utc(),
        "certificate_status": "PASS" if (drift_ok and determinism_ok and replay_ok) else "FAIL",
        "drift_score": drift_score,
        "determinism_score": determinism_score,
        "compliance_score": compliance_score,
        "freeze_verification_status": "PASS" if drift_ok else "FAIL",
        "reports": {
            "drift": drift_report,
            "determinism": determinism_report,
            "execution_replay": replay_report,
        },
        "source_baselines": {
            "sprint10": str(Path(sprint10_baseline)),
            "sprint11": str(Path(sprint11_baseline)),
            "sprint12": str(Path(sprint12_baseline)),
        },
    }

    path = Path(output_path) if output_path else (root / "system_integrity_certificate.json")
    path.write_text(json.dumps(certificate, indent=2, sort_keys=True), encoding="utf-8")
    certificate["output_path"] = str(path)
    return certificate


def enforce_certificate(certificate: dict[str, Any]) -> None:
    if str(certificate.get("certificate_status", "")).upper() != "PASS":
        raise IntegrityCertificateError("integrity certificate status is FAIL")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate Sprint 12 system integrity certificate")
    parser.add_argument("--sprint10", default="sprint_10_pre_edit_baseline.json", help="Sprint 10 baseline path")
    parser.add_argument("--sprint11", default="sprint_11_pre_edit_baseline.json", help="Sprint 11 baseline path")
    parser.add_argument("--sprint12", default="sprint_12_pre_edit_baseline.json", help="Sprint 12 baseline path")
    parser.add_argument("--output", default="system_integrity_certificate.json", help="Certificate output path")
    parser.add_argument("--python-executable", default=sys.executable, help="Python executable")
    parser.add_argument("--run-count", type=int, default=2, help="Determinism repeated run count")
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    certificate = generate_system_integrity_certificate(
        root=Path.cwd(),
        sprint10_baseline=args.sprint10,
        sprint11_baseline=args.sprint11,
        sprint12_baseline=args.sprint12,
        output_path=args.output,
        python_executable=args.python_executable,
        run_count=args.run_count,
    )

    try:
        enforce_certificate(certificate)
        status = 0
    except IntegrityCertificateError:
        status = 1

    print(json.dumps(certificate, indent=2, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
