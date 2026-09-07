from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))

from home_center.module_manifest import (  # noqa: E402
    MAX_MANIFEST_BYTES,
    ModuleManifestError,
    load_and_validate_manifest,
    validate_manifest,
)


def valid_manifest() -> dict:
    return {
        "schema": "home-center.module-manifest.v2",
        "module": {
            "id": "org.home-center.reference-stateful",
            "version": "1.2.3",
            "name": "Reference Stateful Module",
            "publisher": "org.home-center",
        },
        "compatibility": {
            "home_center": {"minimum": "0.11.0", "maximum_exclusive": "0.12.0"},
            "architectures": ["amd64", "arm64"],
            "operating_systems": ["linux"],
        },
        "dependencies": [
            {
                "id": "org.home-center.backup",
                "minimum_version": "1.0.0",
                "maximum_version_exclusive": "2.0.0",
                "optional": False,
            }
        ],
        "conflicts": [],
        "capabilities": ["systemd.v1", "storage.local.v1"],
        "permissions": ["modules.lifecycle.install", "modules.lifecycle.update", "modules.lifecycle.remove"],
        "actions": [
            {
                "id": "module.install.v1",
                "permission": "modules.lifecycle.install",
                "risk": "mutation",
                "timeout_seconds": 300,
                "idempotent": True,
            },
            {
                "id": "module.install.rollback.v1",
                "permission": "modules.lifecycle.install",
                "risk": "mutation",
                "timeout_seconds": 300,
                "idempotent": True,
            },
            {
                "id": "module.upgrade.v1",
                "permission": "modules.lifecycle.update",
                "risk": "mutation",
                "timeout_seconds": 600,
                "idempotent": True,
            },
            {
                "id": "module.upgrade.rollback.v1",
                "permission": "modules.lifecycle.update",
                "risk": "mutation",
                "timeout_seconds": 600,
                "idempotent": True,
            },
            {
                "id": "module.remove.v1",
                "permission": "modules.lifecycle.remove",
                "risk": "mutation",
                "timeout_seconds": 300,
                "idempotent": True,
            },
        ],
        "network": {
            "inbound": [{"protocol": "tcp", "port": 8443, "scope": "node", "purpose": "module HTTPS"}],
            "outbound": [],
        },
        "storage": [
            {"id": "state", "kind": "persistent", "minimum_bytes": 1048576, "backup_required": True},
            {"id": "cache", "kind": "cache", "minimum_bytes": 0, "backup_required": False},
        ],
        "health": [
            {"id": "ready", "kind": "readiness", "interval_seconds": 30, "timeout_seconds": 5},
            {"id": "live", "kind": "liveness", "interval_seconds": 60, "timeout_seconds": 5},
        ],
        "backup": {"mode": "required", "data_ids": ["state"], "restore_required": True},
        "lifecycle": {
            "install": {"action": "module.install.v1", "rollback_action": "module.install.rollback.v1"},
            "upgrade": {"action": "module.upgrade.v1", "rollback_action": "module.upgrade.rollback.v1"},
            "remove": {"action": "module.remove.v1", "data_policy": "preserve"},
        },
        "artifact": {
            "sha256": "a" * 64,
            "size_bytes": 4096,
            "media_type": "application/vnd.home-center.module.v1+tar+gzip",
            "provenance": {"statement_sha256": "b" * 64, "signer_key_ids": ["market-root-2026"], "threshold": 1},
        },
    }


class ModuleManifestTests(unittest.TestCase):
    def assert_rejected(self, value: dict, code: str) -> None:
        with self.assertRaises(ModuleManifestError) as raised:
            validate_manifest(value)
        self.assertEqual(raised.exception.code, code)

    def test_valid_manifest_returns_immutable_identity(self) -> None:
        identity = validate_manifest(valid_manifest())
        self.assertEqual(identity.module_id, "org.home-center.reference-stateful")
        self.assertEqual(identity.version, "1.2.3")
        self.assertEqual(identity.artifact_sha256, "a" * 64)
        self.assertEqual(identity.artifact_size_bytes, 4096)
        self.assertEqual(identity.actions[0], "module.install.v1")

    def test_json_loader_rejects_duplicate_keys_and_oversized_input(self) -> None:
        with self.assertRaises(ModuleManifestError) as duplicate:
            load_and_validate_manifest(b'{"schema":"home-center.module-manifest.v2","schema":"other"}')
        self.assertEqual(duplicate.exception.code, "manifest_duplicate_key")
        with self.assertRaises(ModuleManifestError) as oversized:
            load_and_validate_manifest(b" " * (MAX_MANIFEST_BYTES + 1))
        self.assertEqual(oversized.exception.code, "manifest_size_rejected")

    def test_loader_accepts_canonical_json_shape(self) -> None:
        payload = json.dumps(valid_manifest(), sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(load_and_validate_manifest(payload).artifact_sha256, "a" * 64)

    def test_unknown_field_fails_closed(self) -> None:
        value = valid_manifest()
        value["install_command"] = "sh -c anything"
        self.assert_rejected(value, "manifest_fields_rejected")

    def test_invalid_compatibility_interval_is_rejected(self) -> None:
        value = valid_manifest()
        value["compatibility"]["home_center"] = {"minimum": "0.12.0", "maximum_exclusive": "0.11.0"}
        self.assert_rejected(value, "home_center_compatibility_interval_rejected")

    def test_duplicate_and_self_relationships_are_rejected(self) -> None:
        value = valid_manifest()
        value["dependencies"].append(copy.deepcopy(value["dependencies"][0]))
        self.assert_rejected(value, "dependency_duplicate_rejected")
        value = valid_manifest()
        value["conflicts"] = [value["module"]["id"]]
        self.assert_rejected(value, "conflict_relationship_rejected")

    def test_action_must_use_declared_permission_and_be_idempotent(self) -> None:
        value = valid_manifest()
        value["actions"][0]["permission"] = "system.shell.execute"
        self.assert_rejected(value, "action_permission_undeclared")
        value = valid_manifest()
        value["actions"][0]["idempotent"] = False
        self.assert_rejected(value, "mutation_action_not_idempotent")

    def test_backup_must_cover_every_declared_persistent_resource(self) -> None:
        value = valid_manifest()
        value["backup"]["data_ids"] = []
        self.assert_rejected(value, "backup_coverage_rejected")
        value = valid_manifest()
        value["storage"][1]["backup_required"] = True
        self.assert_rejected(value, "cache_backup_rejected")

    def test_lifecycle_references_only_declared_actions_and_preserves_data(self) -> None:
        value = valid_manifest()
        value["lifecycle"]["upgrade"]["rollback_action"] = "module.unknown.v1"
        self.assert_rejected(value, "lifecycle_action_unknown")
        value = valid_manifest()
        value["lifecycle"]["remove"]["data_policy"] = "wipe"
        self.assert_rejected(value, "lifecycle_remove_rejected")

    def test_artifact_identity_and_threshold_are_bounded(self) -> None:
        value = valid_manifest()
        value["artifact"]["sha256"] = "not-a-digest"
        self.assert_rejected(value, "artifact_digest_rejected")
        value = valid_manifest()
        value["artifact"]["provenance"]["threshold"] = 2
        self.assert_rejected(value, "provenance_threshold_rejected")


if __name__ == "__main__":
    unittest.main()
