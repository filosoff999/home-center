from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class P23ContractSurfaceTests(unittest.TestCase):
    def test_release_version_is_consistent(self) -> None:
        runtime = (ROOT / "product/control-plane/src/home_center/__init__.py").read_text(encoding="utf-8")
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        builder = (ROOT / "deploy/scripts/build-artifact.sh").read_text(encoding="utf-8")
        self.assertIn('__version__ = "0.4.0"', runtime)
        self.assertIn('version = "0.4.0"', project)
        self.assertIn("HOME_CENTER_VERSION:-0.4.0", builder)

    def test_openapi_publishes_tls_status_and_public_trust_anchor(self) -> None:
        value = json.loads((ROOT / "contracts/openapi/home-center.v1.openapi.json").read_text(encoding="utf-8"))
        self.assertEqual(value["openapi"], "3.1.0")
        self.assertEqual(value["info"]["version"], "0.4.0")
        self.assertEqual(value["servers"], [{"url": "https://dc01.hm.dm:8443", "description": "Current canonical Home Center production endpoint"}])
        paths = value["paths"]
        self.assertIn("/api/v1/tls", paths)
        self.assertIn("/api/v1/tls/ca.crt", paths)
        self.assertNotIn("security", paths["/api/v1/tls"]["get"])
        self.assertEqual(paths["/api/v1/tls/ca.crt"]["get"]["security"], [])
        self.assertEqual(
            paths["/api/v1/tls"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/TlsStatus",
        )

    def test_tls_status_schema_is_closed_and_exposes_presence_only_for_private_key(self) -> None:
        value = json.loads((ROOT / "contracts/openapi/home-center.v1.openapi.json").read_text(encoding="utf-8"))
        schemas = value["components"]["schemas"]
        schema = schemas["TlsStatus"]
        self.assertFalse(schema["additionalProperties"])
        top_properties = schema["properties"]
        self.assertNotIn("private_key", top_properties)
        self.assertNotIn("private_key_pem", top_properties)
        candidate_properties = top_properties["candidate"]["properties"]
        self.assertEqual(candidate_properties["private_key_present"], {"type": "boolean"})
        self.assertNotIn("private_key", candidate_properties)
        self.assertNotIn("private_key_pem", candidate_properties)
        certificate_properties = schemas["CertificateStatus"]["properties"]
        self.assertIn("fingerprint_sha256", certificate_properties)
        self.assertIn("chain_valid", certificate_properties)
        self.assertIn("hostname_match", certificate_properties)
        serialized = json.dumps(schema, sort_keys=True)
        self.assertNotIn("tls.key", serialized)
        self.assertIn("candidate", serialized)
        self.assertIn("renewal", serialized)


if __name__ == "__main__":
    unittest.main()
