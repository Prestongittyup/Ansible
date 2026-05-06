"""Deterministic contract registry.

This registry is intentionally independent of filesystem paths. Contracts are
resolved by logical identifiers and semantic version only.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, Dict, Mapping

ContractPayload = Mapping[str, Any]
ContractValidator = Callable[[ContractPayload], None]


class ContractRegistryError(RuntimeError):
    """Raised when contract registration or resolution fails."""


@dataclass(frozen=True)
class _ContractRecord:
    name: str
    version: str
    payload: ContractPayload
    validator: ContractValidator | None


_LOCK = RLock()
_CONTRACTS: Dict[str, Dict[str, _ContractRecord]] = {}
_ACTIVE_VERSIONS: Dict[str, str] = {}
_DEFAULTS_LOADED = False


def _normalize_name(name: str) -> str:
    token = str(name).strip().upper()
    if not token:
        raise ContractRegistryError("Contract name is required")
    return token


def _normalize_version(version: str) -> str:
    token = str(version).strip()
    if not token:
        raise ContractRegistryError("Contract version is required")
    return token


def _register_contract_internal(
    *,
    contract_name: str,
    version: str,
    payload: ContractPayload,
    validator: ContractValidator | None,
    make_active: bool,
    overwrite: bool,
) -> None:
    name = _normalize_name(contract_name)
    normalized_version = _normalize_version(version)

    if not isinstance(payload, Mapping):
        raise ContractRegistryError(f"Contract payload for {name}@{normalized_version} must be a mapping")

    bucket = _CONTRACTS.setdefault(name, {})
    if normalized_version in bucket and not overwrite:
        raise ContractRegistryError(f"Contract already registered: {name}@{normalized_version}")

    record = _ContractRecord(
        name=name,
        version=normalized_version,
        payload=copy.deepcopy(dict(payload)),
        validator=validator,
    )
    bucket[normalized_version] = record

    if make_active or name not in _ACTIVE_VERSIONS:
        _ACTIVE_VERSIONS[name] = normalized_version


def _ensure_default_contracts_loaded() -> None:
    global _DEFAULTS_LOADED
    if _DEFAULTS_LOADED:
        return

    with _LOCK:
        if _DEFAULTS_LOADED:
            return

        from contract_registry.contracts import get_contract_definitions

        definitions = get_contract_definitions()
        if not definitions:
            raise ContractRegistryError("No default contracts available for registration")

        for definition in definitions:
            try:
                name = str(definition["name"])
                version = str(definition["version"])
                payload = definition["payload"]
            except Exception as exc:  # pragma: no cover
                raise ContractRegistryError("Malformed default contract definition") from exc

            validator = definition.get("validator")
            if validator is not None and not callable(validator):
                raise ContractRegistryError(f"Validator for {name}@{version} must be callable")

            _register_contract_internal(
                contract_name=name,
                version=version,
                payload=payload,
                validator=validator,
                make_active=True,
                overwrite=False,
            )

        _DEFAULTS_LOADED = True


def _resolve_record(contract_name: str, version: str | None = None) -> _ContractRecord:
    _ensure_default_contracts_loaded()

    name = _normalize_name(contract_name)
    bucket = _CONTRACTS.get(name)
    if not bucket:
        raise ContractRegistryError(f"Unknown contract: {name}")

    if version is not None:
        target_version = _normalize_version(version)
    else:
        target_version = _ACTIVE_VERSIONS.get(name, "")
        if not target_version:
            raise ContractRegistryError(f"No active version configured for contract: {name}")

    record = bucket.get(target_version)
    if record is None:
        raise ContractRegistryError(f"Unknown contract version: {name}@{target_version}")

    return record


def register_contract(
    contract_name: str,
    version: str,
    payload: ContractPayload,
    *,
    validator: ContractValidator | None = None,
    make_active: bool = True,
    overwrite: bool = False,
) -> None:
    """Register or extend a contract version."""

    _ensure_default_contracts_loaded()

    with _LOCK:
        _register_contract_internal(
            contract_name=contract_name,
            version=version,
            payload=payload,
            validator=validator,
            make_active=make_active,
            overwrite=overwrite,
        )


def resolve_contract(contract_name: str, version: str | None = None) -> Dict[str, Any]:
    """Resolve a contract payload by name and optional version."""

    record = _resolve_record(contract_name, version=version)
    return copy.deepcopy(dict(record.payload))


def validate_contract(
    contract_name: str,
    contract_payload: Mapping[str, Any] | None = None,
    *,
    version: str | None = None,
) -> bool:
    """Validate a contract payload using its registered validator."""

    record = _resolve_record(contract_name, version=version)
    payload: Mapping[str, Any]
    if contract_payload is None:
        payload = dict(record.payload)
    else:
        if not isinstance(contract_payload, Mapping):
            raise ContractRegistryError(f"Contract payload for {record.name} must be a mapping")
        payload = contract_payload

    if record.validator is None:
        return True

    try:
        record.validator(payload)
    except Exception as exc:
        raise ContractRegistryError(f"Contract validation failed for {record.name}@{record.version}: {exc}") from exc

    return True


def get_active_version(contract_name: str) -> str:
    """Return active contract version for a contract name."""

    record = _resolve_record(contract_name)
    return record.version


def list_registered_contracts() -> Dict[str, Dict[str, Any]]:
    """List all contracts and their active versions."""

    _ensure_default_contracts_loaded()
    snapshot: Dict[str, Dict[str, Any]] = {}
    for name, versions in sorted(_CONTRACTS.items()):
        snapshot[name] = {
            "active_version": _ACTIVE_VERSIONS.get(name, ""),
            "versions": sorted(versions.keys()),
        }
    return snapshot
