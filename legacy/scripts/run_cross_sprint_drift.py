#!/usr/bin/env python3
"""CLI wrapper for Sprint 12 cross-sprint drift comparator."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from legacy.stabilization_layer.cross_sprint_drift_comparator import main


if __name__ == "__main__":
    raise SystemExit(main())
