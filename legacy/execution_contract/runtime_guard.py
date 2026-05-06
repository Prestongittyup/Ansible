"""Runtime guard for Sprint 2 pre-import execution enforcement."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Sequence, Set

EXECUTION_LAYER_RUNTIME_STANDARD = "Python 3.11.x"
_REQUIRED_PYTHON_MAJOR = 3
_REQUIRED_PYTHON_MINOR = 11

_ANSIBLE_PYTHON_VERSION_PATTERN = re.compile(
    r"python\s*version\s*=\s*([0-9]+\.[0-9]+\.[0-9]+)",
    re.IGNORECASE,
)
_NON_311_HINT_PATTERN = re.compile(r"python\s*3\.(1[2-9]|[2-9][0-9])|python31[2-9]", re.IGNORECASE)

_ALLOWED_PREIMPORT_MODULES = {"execution_contract", "execution_contract.runtime_guard"}
_APPLICATION_MODULE_PREFIXES = ("ansible_validation", "inventory_pipeline", "api_client")


@dataclass(frozen=True)
class RuntimeContext:
    """Runtime metadata produced only after successful pre-import guard checks."""

    runtime_standard: str
    python_runtime: str
    guard_mode: str


class RuntimeGuardError(RuntimeError):
    """Raised when runtime preflight cannot prove Python 3.11 compliance."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "RUNTIME_GUARD_FAILURE",
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})

    def to_payload(self) -> Dict[str, Any]:
        """Return deterministic fail-closed payload for guard violations."""
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "runtime_guard_blocked",
            "status": "FAIL",
            "execution_state": "FAIL_RUNTIME",
            "error": {
                "code": self.code,
                "message": str(self),
                "source": "runtime_guard",
                "details": dict(self.details),
            },
        }


def current_python_runtime() -> str:
    """Return the currently executing Python runtime version."""
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def _trim_text(value: str, limit: int = 1200) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit]


def _extract_ansible_python_version(output_text: str) -> Optional[str]:
    match = _ANSIBLE_PYTHON_VERSION_PATTERN.search(output_text)
    if not match:
        return None
    return match.group(1)


def _contains_non_311_hint(text: str) -> bool:
    lowered = text.lower()
    if _NON_311_HINT_PATTERN.search(lowered):
        return True
    if "python 3.11" in lowered or "python311" in lowered:
        return False
    return "python313" in lowered or "python312" in lowered


def _is_application_module(name: str) -> bool:
    if name.startswith("execution_contract"):
        return True
    return any(name == prefix or name.startswith(prefix + ".") for prefix in _APPLICATION_MODULE_PREFIXES)


def detect_preimport_violations(allowed_preimports: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Detect application module imports that happened before runtime validation."""
    allowed: Set[str] = set(_ALLOWED_PREIMPORT_MODULES)
    if allowed_preimports:
        allowed.update(str(item).strip() for item in allowed_preimports if str(item).strip())

    imported_before_guard = []
    partial_initialization = []

    for module_name, module_obj in sys.modules.items():
        if module_name in allowed:
            continue
        if not _is_application_module(module_name):
            continue

        imported_before_guard.append(module_name)
        if module_obj is None or getattr(module_obj, "__spec__", None) is None:
            partial_initialization.append(module_name)

    imported_before_guard.sort()
    partial_initialization.sort()

    return {
        "imported_before_guard": imported_before_guard,
        "partial_initialization": partial_initialization,
    }


def enforce_preimport_boundary(allowed_preimports: Optional[Sequence[str]] = None) -> None:
    """Fail closed when application modules are imported before runtime guard runs."""
    violations = detect_preimport_violations(allowed_preimports=allowed_preimports)

    if violations["partial_initialization"]:
        raise RuntimeGuardError(
            "partial initialization detected before runtime guard",
            code="PARTIAL_INITIALIZATION_DETECTED",
            details=violations,
        )

    if violations["imported_before_guard"]:
        raise RuntimeGuardError(
            "import order violation: application modules loaded before runtime guard",
            code="IMPORT_ORDER_VIOLATION",
            details=violations,
        )


def enforce_python_runtime_311() -> None:
    """Fail closed when the active interpreter is not Python 3.11.x."""
    major = sys.version_info.major
    minor = sys.version_info.minor
    if major == _REQUIRED_PYTHON_MAJOR and minor == _REQUIRED_PYTHON_MINOR:
        return

    raise RuntimeGuardError(
        f"execution runtime mismatch: required {EXECUTION_LAYER_RUNTIME_STANDARD}, found Python {current_python_runtime()}",
        code="RUNTIME_VERSION_MISMATCH",
        details={
            "required_runtime": EXECUTION_LAYER_RUNTIME_STANDARD,
            "detected_runtime": f"Python {current_python_runtime()}",
        },
    )


def enforce_ansible_runtime_311(timeout_seconds: int = 20) -> None:
    """Fail closed when ansible-playbook runtime cannot be proven as Python 3.11.x."""
    command = ["ansible-playbook", "--version"]

    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(5, int(timeout_seconds)),
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeGuardError(
            "ansible-playbook executable not found",
            code="ANSIBLE_EXECUTABLE_MISSING",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeGuardError(
            "ansible runtime preflight timed out",
            code="ANSIBLE_RUNTIME_TIMEOUT",
        ) from exc

    combined_output = "\n".join(part for part in [proc.stdout, proc.stderr] if part)
    combined_output = _trim_text(combined_output)

    if proc.returncode != 0:
        error_code = "RUNTIME_VERSION_MISMATCH" if _contains_non_311_hint(combined_output) else "ANSIBLE_RUNTIME_FAILURE"
        raise RuntimeGuardError(
            "ansible runtime preflight failed",
            code=error_code,
            details={"ansible_output": combined_output},
        )

    detected = _extract_ansible_python_version(combined_output)
    if detected is None:
        raise RuntimeGuardError(
            "ansible runtime version could not be confirmed as Python 3.11",
            code="RUNTIME_VERSION_UNCONFIRMED",
            details={"ansible_output": combined_output},
        )

    if not detected.startswith("3.11"):
        raise RuntimeGuardError(
            f"ansible runtime mismatch: expected Python 3.11.x, found Python {detected}",
            code="RUNTIME_VERSION_MISMATCH",
            details={"ansible_python_version": detected},
        )


def enforce_preimport_runtime_311(allowed_preimports: Optional[Sequence[str]] = None) -> RuntimeContext:
    """Execute strict pre-import runtime validation for the execution layer."""
    enforce_preimport_boundary(allowed_preimports=allowed_preimports)
    enforce_python_runtime_311()
    enforce_ansible_runtime_311()
    return RuntimeContext(
        runtime_standard=EXECUTION_LAYER_RUNTIME_STANDARD,
        python_runtime=current_python_runtime(),
        guard_mode="pre_import",
    )


def enforce_execution_runtime_311() -> RuntimeContext:
    """Compatibility alias for strict pre-import runtime enforcement."""
    return enforce_preimport_runtime_311()
