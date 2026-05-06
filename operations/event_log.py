from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict


class OperationsEventLog:
    def __init__(self, *, path: str | Path) -> None:
        self.path = Path(path)

    def emit(
        self,
        *,
        action: str,
        status: str,
        details: Dict[str, Any] | None = None,
        override_used: bool = False,
    ) -> Dict[str, Any]:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": str(action),
            "status": str(status),
            "override_used": bool(override_used),
            "details": dict(details or {}),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
        return event
