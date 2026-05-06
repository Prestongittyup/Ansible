"""Default contract definitions."""

from __future__ import annotations

from typing import Any, Dict, List

from .adapter_contract import get_contracts as get_adapter_contracts
from .execution_contract import get_contracts as get_execution_contracts
from .governance_contract import get_contracts as get_governance_contracts
from .schema_contract import get_contracts as get_schema_contracts


def get_contract_definitions() -> List[Dict[str, Any]]:
    contracts: List[Dict[str, Any]] = []
    contracts.extend(get_execution_contracts())
    contracts.extend(get_schema_contracts())
    contracts.extend(get_governance_contracts())
    contracts.extend(get_adapter_contracts())
    return contracts
