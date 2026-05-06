"""Sprint 22 contract registry tests."""

from __future__ import annotations

import unittest

from contract_registry import (
    ContractRegistryError,
    get_active_version,
    register_contract,
    resolve_contract,
    validate_contract,
)


class ContractRegistryTests(unittest.TestCase):
    def test_default_contract_resolution(self) -> None:
        sci_rules = resolve_contract("SCI_RULES")
        self.assertIsInstance(sci_rules, dict)
        self.assertIn("rules", sci_rules)
        self.assertEqual("1.0", get_active_version("SCI_RULES"))

    def test_validate_contract_rejects_invalid_payload(self) -> None:
        schema_payload = resolve_contract("EXECUTION_SCHEMA")
        schema_payload["schema_version"] = "0.0"

        with self.assertRaises(ContractRegistryError):
            validate_contract("EXECUTION_SCHEMA", schema_payload)

    def test_register_and_activate_custom_contract_version(self) -> None:
        def _validator(payload: dict[str, object]) -> None:
            if "enabled" not in payload:
                raise ValueError("enabled field is required")

        register_contract(
            "UNIT_TEST_CONTRACT",
            "9.9",
            {"enabled": True, "schema_version": "1.0"},
            validator=_validator,
            make_active=True,
            overwrite=True,
        )

        active_payload = resolve_contract("UNIT_TEST_CONTRACT")
        self.assertTrue(bool(active_payload.get("enabled")))
        self.assertEqual("9.9", get_active_version("UNIT_TEST_CONTRACT"))
        self.assertTrue(validate_contract("UNIT_TEST_CONTRACT"))


if __name__ == "__main__":
    unittest.main()
