from __future__ import annotations

import json
import sys
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from schema_validator import ValidationError, validate  # noqa: E402


class CoreContractTests0100(unittest.TestCase):
    def load(self, name: str) -> dict:
        return json.loads((ROOT / "contracts/core" / name).read_text(encoding="utf-8"))

    def test_plan_only_command_contract(self) -> None:
        schema = self.load("core-command.v1.schema.json")
        value = {
            "schema": "home-center.core-command.v1",
            "operation_id": str(uuid.uuid4()),
            "idempotency_key": "contract-0100",
            "correlation_id": "contract-0100",
            "actor": "local-admin",
            "reason": "validate closed command contract",
            "module": "node-manager",
            "action": "node.drain.plan.v1",
            "target_id": "test-node",
            "input": {
                "schema": "home-center.node-drain-plan-input.v1",
                "quorum_safe": True,
                "mandatory_services_safe": True,
            },
            "mode": "plan",
        }
        validate(schema, value)
        value["mode"] = "execute"
        with self.assertRaises(ValidationError):
            validate(schema, value)

    def test_result_and_error_contracts_are_closed(self) -> None:
        operation_id = str(uuid.uuid4())
        validate(
            self.load("core-result.v1.schema.json"),
            {
                "schema": "home-center.core-result.v1",
                "operation_id": operation_id,
                "module": "node-manager",
                "target_id": "test-node",
                "state": "blocked",
                "code": "quorum_not_safe",
                "idempotent_replay": False,
            },
        )
        error = {
            "schema": "home-center.error.v1",
            "code": "default_deny",
            "message": "Операция запрещена политикой",
            "correlation_id": "contract-0100",
            "retryable": False,
        }
        validate(self.load("error.v1.schema.json"), error)
        error["internal_exception"] = "forbidden"
        with self.assertRaises(ValidationError):
            validate(self.load("error.v1.schema.json"), error)


if __name__ == "__main__":
    unittest.main()
