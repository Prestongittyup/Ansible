from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Dict, Optional


class ConfigError(RuntimeError):
    """Configuration validation failure."""


_ALLOWED_KERNEL_MODES = {"MOCK", "LIVE", "HYBRID"}
_ALLOWED_EXECUTION_MODES = {"READ_ONLY", "CHANGE_ENABLED"}


@dataclass(frozen=True)
class RuntimeConfig:
    kernel_mode: str
    fail_closed: bool
    window_days: int
    required_consecutive_parity: int
    max_global_drift: float
    critical_events_180d: int
    audit_path: str
    parity_history_path: str


@dataclass(frozen=True)
class AdapterConfig:
    logicmonitor_mode: str
    ansible_mode: str
    nautobot_mode: str
    postgres_mode: str

    logicmonitor_api_key: str
    logicmonitor_account: str
    logicmonitor_base_url: str
    logicmonitor_live_payload_path: str

    ansible_inventory_source_path: str
    ansible_playbook_command: str
    ansible_execution_endpoint: str
    ansible_runner_path: str

    nautobot_token: str
    nautobot_url: str
    nautobot_base_url: str
    nautobot_desired_state_path: str

    postgres_host: str
    postgres_port: int
    postgres_user: str
    postgres_password: str
    postgres_database: str
    postgres_db: str
    postgres_sqlite_path: str
    postgres_connection_string: str

    allow_hybrid_fallback_logicmonitor: bool
    allow_hybrid_fallback_ansible: bool
    allow_hybrid_fallback_nautobot: bool
    allow_hybrid_fallback_postgres: bool


@dataclass(frozen=True)
class KernelConfig:
    phase: str
    target_phase: str
    execution_mode: str
    governance_approved: bool
    root_path: str
    python_executable: str


@dataclass(frozen=True)
class AppConfig:
    runtime: RuntimeConfig
    adapters: AdapterConfig
    kernel: KernelConfig


def _parse_env_file(path: Path) -> Dict[str, str]:
    if not path.exists() or not path.is_file():
        return {}

    values: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if value.startswith(("\"", "'")) and value.endswith(("\"", "'")) and len(value) >= 2:
            value = value[1:-1]

        if key:
            values[key] = value

    return values


def _parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"Invalid boolean value: {value}")


def _parse_int(value: Any, *, default: int, minimum: int = 0) -> int:
    if value is None:
        return default
    try:
        parsed = int(str(value).strip())
    except ValueError as exc:
        raise ConfigError(f"Invalid integer value: {value}") from exc
    if parsed < minimum:
        raise ConfigError(f"Integer value {parsed} must be >= {minimum}")
    return parsed


def _parse_float(value: Any, *, default: float, minimum_exclusive: float = 0.0) -> float:
    if value is None:
        return default
    try:
        parsed = float(str(value).strip())
    except ValueError as exc:
        raise ConfigError(f"Invalid numeric value: {value}") from exc
    if parsed <= minimum_exclusive:
        raise ConfigError(f"Numeric value {parsed} must be > {minimum_exclusive}")
    return parsed


def _normalize_kernel_mode(value: Any) -> str:
    token = str(value or "MOCK").strip().upper()
    if token not in _ALLOWED_KERNEL_MODES:
        raise ConfigError(f"Invalid KERNEL_MODE: {token}")
    return token


def _normalize_execution_mode(value: Any) -> str:
    token = str(value or "READ_ONLY").strip().upper()
    if token not in _ALLOWED_EXECUTION_MODES:
        raise ConfigError(f"Invalid execution mode: {token}")
    return token


def _resolve_adapter_mode(kernel_mode: str, explicit: Optional[str]) -> str:
    if explicit:
        token = explicit.strip().upper()
        if token not in _ALLOWED_KERNEL_MODES:
            raise ConfigError(f"Invalid adapter mode: {token}")
        return token

    return kernel_mode


def _is_blank(value: str) -> bool:
    return not str(value).strip()


def _resolve_env_path(*, root: Path, env_path_override: Optional[str]) -> Path:
    if env_path_override is None:
        return root / ".env"

    candidate = Path(str(env_path_override).strip())
    if not candidate.is_absolute():
        return (root / candidate).resolve()
    return candidate


def _has_postgres_live_connectivity(adapters: AdapterConfig) -> bool:
    has_connection_string = not _is_blank(adapters.postgres_connection_string)
    has_sqlite_stub = not _is_blank(adapters.postgres_sqlite_path)
    has_legacy_triplet = (
        not _is_blank(adapters.postgres_user)
        and not _is_blank(adapters.postgres_password)
        and not _is_blank(adapters.postgres_database)
    )
    return has_connection_string or has_sqlite_stub or has_legacy_triplet


def _validate_adapter_live_requirements(adapter_name: str, adapters: AdapterConfig) -> list[str]:
    missing: list[str] = []

    if adapter_name == "LOGICMONITOR":
        if _is_blank(adapters.logicmonitor_api_key):
            missing.append("LOGICMONITOR_API_KEY")
        if _is_blank(adapters.logicmonitor_account):
            missing.append("LOGICMONITOR_ACCOUNT")
        return missing

    if adapter_name == "ANSIBLE":
        if _is_blank(adapters.ansible_execution_endpoint) and _is_blank(adapters.ansible_runner_path):
            missing.append("ANSIBLE_EXECUTION_ENDPOINT|ANSIBLE_RUNNER_PATH")
        return missing

    if adapter_name == "NAUTOBOT":
        if _is_blank(adapters.nautobot_token):
            missing.append("NAUTOBOT_TOKEN|NAUTOBOT_API_TOKEN")
        if _is_blank(adapters.nautobot_url):
            missing.append("NAUTOBOT_URL")
        return missing

    if adapter_name == "POSTGRES":
        if _is_blank(adapters.postgres_connection_string):
            missing.append("POSTGRES_CONNECTION_STRING")
        if _is_blank(adapters.postgres_db):
            missing.append("POSTGRES_DB")
        if _is_blank(adapters.postgres_user):
            missing.append("POSTGRES_USER")
        if _is_blank(adapters.postgres_password):
            missing.append("POSTGRES_PASSWORD")
        return missing

    raise ConfigError(f"Unsupported adapter name for validation: {adapter_name}")


def _validate_live_requirements(*, adapters: AdapterConfig) -> None:
    missing: list[str] = []

    if adapters.logicmonitor_mode == "LIVE":
        missing.extend(_validate_adapter_live_requirements("LOGICMONITOR", adapters))

    if adapters.ansible_mode == "LIVE":
        missing.extend(_validate_adapter_live_requirements("ANSIBLE", adapters))

    if adapters.nautobot_mode == "LIVE":
        missing.extend(_validate_adapter_live_requirements("NAUTOBOT", adapters))

    if adapters.postgres_mode == "LIVE":
        missing.extend(_validate_adapter_live_requirements("POSTGRES", adapters))

    if missing:
        ordered = sorted(set(missing))
        raise ConfigError("Missing LIVE mode configuration: " + ", ".join(ordered))


def _validate_hybrid_requirements(*, adapters: AdapterConfig) -> None:
    missing: list[str] = []

    if adapters.logicmonitor_mode == "HYBRID":
        lm_missing = _validate_adapter_live_requirements("LOGICMONITOR", adapters)
        if lm_missing:
            missing.extend([f"LOGICMONITOR::{item}" for item in lm_missing])

    if adapters.ansible_mode == "HYBRID":
        ansible_missing = _validate_adapter_live_requirements("ANSIBLE", adapters)
        if ansible_missing:
            missing.extend([f"ANSIBLE::{item}" for item in ansible_missing])

    if adapters.nautobot_mode == "HYBRID":
        nautobot_missing = _validate_adapter_live_requirements("NAUTOBOT", adapters)
        if nautobot_missing:
            missing.extend([f"NAUTOBOT::{item}" for item in nautobot_missing])

    if adapters.postgres_mode == "HYBRID":
        postgres_missing = _validate_adapter_live_requirements("POSTGRES", adapters)
        if postgres_missing:
            missing.extend([f"POSTGRES::{item}" for item in postgres_missing])

    if missing:
        ordered = sorted(set(missing))
        raise ConfigError("Missing HYBRID live-preferred configuration: " + ", ".join(ordered))


def load_app_config(
    *,
    root_path: str | Path,
    env_path_override: Optional[str] = None,
    mode_override: Optional[str] = None,
    phase_override: Optional[str] = None,
    target_phase_override: Optional[str] = None,
    fail_closed_override: Optional[bool] = None,
    audit_path_override: Optional[str] = None,
    window_days_override: Optional[int] = None,
    required_consecutive_parity_override: Optional[int] = None,
    max_global_drift_override: Optional[float] = None,
    execution_mode_override: Optional[str] = None,
    governance_approved_override: Optional[bool] = None,
    critical_events_override: Optional[int] = None,
    python_executable_override: Optional[str] = None,
    validate_env: bool = True,
) -> AppConfig:
    root = Path(root_path)
    env_file = _parse_env_file(_resolve_env_path(root=root, env_path_override=env_path_override))

    merged: Dict[str, str] = {}
    merged.update(env_file)
    merged.update({key: value for key, value in os.environ.items() if isinstance(value, str)})

    kernel_mode = _normalize_kernel_mode(mode_override or merged.get("KERNEL_MODE") or "MOCK")

    fail_closed_default = _parse_bool(merged.get("FAIL_CLOSED"), default=True)
    fail_closed = fail_closed_default if fail_closed_override is None else bool(fail_closed_override)

    runtime = RuntimeConfig(
        kernel_mode=kernel_mode,
        fail_closed=fail_closed,
        window_days=_parse_int(
            window_days_override if window_days_override is not None else merged.get("WINDOW_DAYS"),
            default=30,
            minimum=1,
        ),
        required_consecutive_parity=_parse_int(
            required_consecutive_parity_override
            if required_consecutive_parity_override is not None
            else merged.get("REQUIRED_CONSECUTIVE_PARITY"),
            default=3,
            minimum=1,
        ),
        max_global_drift=_parse_float(
            max_global_drift_override if max_global_drift_override is not None else merged.get("MAX_GLOBAL_DRIFT"),
            default=5.0,
            minimum_exclusive=0.0,
        ),
        critical_events_180d=_parse_int(
            critical_events_override if critical_events_override is not None else merged.get("CRITICAL_EVENTS_180D"),
            default=0,
            minimum=0,
        ),
        audit_path=str(audit_path_override or merged.get("AUDIT_PATH") or "./logs"),
        parity_history_path=str(merged.get("PARITY_HISTORY_PATH") or "./logs/parity_history.json"),
    )

    adapters = AdapterConfig(
        logicmonitor_mode=_resolve_adapter_mode(kernel_mode, merged.get("LOGICMONITOR_MODE")),
        ansible_mode=_resolve_adapter_mode(kernel_mode, merged.get("ANSIBLE_MODE")),
        nautobot_mode=_resolve_adapter_mode(kernel_mode, merged.get("NAUTOBOT_MODE")),
        postgres_mode=_resolve_adapter_mode(kernel_mode, merged.get("POSTGRES_MODE")),
        logicmonitor_api_key=str(merged.get("LOGICMONITOR_API_KEY") or ""),
        logicmonitor_account=str(merged.get("LOGICMONITOR_ACCOUNT") or ""),
        logicmonitor_base_url=str(merged.get("LOGICMONITOR_BASE_URL") or "").strip(),
        logicmonitor_live_payload_path=str(merged.get("LOGICMONITOR_LIVE_PAYLOAD_PATH") or ""),
        ansible_inventory_source_path=str(
            merged.get("ANSIBLE_INVENTORY_SOURCE_PATH")
            or merged.get("ANSIBLE_INVENTORY_PATH")
            or ""
        ),
        ansible_playbook_command=str(merged.get("ANSIBLE_PLAYBOOK_COMMAND") or ""),
        ansible_execution_endpoint=str(merged.get("ANSIBLE_EXECUTION_ENDPOINT") or ""),
        ansible_runner_path=str(merged.get("ANSIBLE_RUNNER_PATH") or ""),
        nautobot_token=str(merged.get("NAUTOBOT_TOKEN") or merged.get("NAUTOBOT_API_TOKEN") or ""),
        nautobot_url=str(merged.get("NAUTOBOT_URL") or merged.get("NAUTOBOT_BASE_URL") or ""),
        nautobot_base_url=str(merged.get("NAUTOBOT_BASE_URL") or merged.get("NAUTOBOT_URL") or ""),
        nautobot_desired_state_path=str(merged.get("NAUTOBOT_DESIRED_STATE_PATH") or ""),
        postgres_host=str(merged.get("POSTGRES_HOST") or "localhost"),
        postgres_port=_parse_int(merged.get("POSTGRES_PORT"), default=5432, minimum=1),
        postgres_user=str(merged.get("POSTGRES_USER") or ""),
        postgres_password=str(merged.get("POSTGRES_PASSWORD") or ""),
        postgres_database=str(merged.get("POSTGRES_DATABASE") or ""),
        postgres_db=str(merged.get("POSTGRES_DB") or merged.get("POSTGRES_DATABASE") or ""),
        postgres_sqlite_path=str(merged.get("POSTGRES_SQLITE_PATH") or ""),
        postgres_connection_string=str(merged.get("POSTGRES_CONNECTION_STRING") or ""),
        allow_hybrid_fallback_logicmonitor=_parse_bool(
            merged.get("ALLOW_HYBRID_FALLBACK_LOGICMONITOR"),
            default=False,
        ),
        allow_hybrid_fallback_ansible=_parse_bool(
            merged.get("ALLOW_HYBRID_FALLBACK_ANSIBLE"),
            default=False,
        ),
        allow_hybrid_fallback_nautobot=_parse_bool(
            merged.get("ALLOW_HYBRID_FALLBACK_NAUTOBOT"),
            default=False,
        ),
        allow_hybrid_fallback_postgres=_parse_bool(
            merged.get("ALLOW_HYBRID_FALLBACK_POSTGRES"),
            default=False,
        ),
    )

    kernel = KernelConfig(
        phase=str(phase_override or merged.get("PHASE") or "auto"),
        target_phase=str(target_phase_override or merged.get("TARGET_PHASE") or "auto"),
        execution_mode=_normalize_execution_mode(execution_mode_override or merged.get("EXECUTION_MODE") or "READ_ONLY"),
        governance_approved=
            _parse_bool(merged.get("GOVERNANCE_APPROVED"), default=False)
            if governance_approved_override is None
            else bool(governance_approved_override),
        root_path=str(root.resolve()),
        python_executable=str(python_executable_override or merged.get("PYTHON_EXECUTABLE") or os.sys.executable),
    )

    if not runtime.fail_closed:
        raise ConfigError("Fail-closed mode must be enabled")

    if validate_env:
        _validate_live_requirements(adapters=adapters)
        _validate_hybrid_requirements(adapters=adapters)

    return AppConfig(runtime=runtime, adapters=adapters, kernel=kernel)
