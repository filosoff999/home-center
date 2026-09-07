from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))

from home_center.release_candidate import (  # noqa: E402
    MAX_ACCEPTANCE_BYTES,
    ReleaseCandidateAcceptanceError,
    load_acceptance_document,
    verify_release_candidate,
)


CANDIDATE_REVISION = "9" * 40
CANDIDATE_ARTIFACT = "a" * 64
PREDECESSOR_REVISION = "8" * 40
PREDECESSOR_ARTIFACT = "b" * 64


def identity(version: str, revision: str, artifact: str) -> dict[str, str]:
    return {"version": version, "revision": revision, "artifact_sha256": artifact}


def pki(seed: str) -> dict[str, str]:
    return {
        "ca_sha256": seed * 64,
        "certificate_sha256": chr(ord(seed) + 1) * 64,
        "public_key_sha256": chr(ord(seed) + 2) * 64,
    }


def evidence() -> dict:
    candidate = identity("0.10.0", CANDIDATE_REVISION, CANDIDATE_ARTIFACT)
    predecessor = identity("0.9.2", PREDECESSOR_REVISION, PREDECESSOR_ARTIFACT)
    rollout = []
    for sequence, node, seed in ((1, "dc02", "1"), (2, "dc01", "4")):
        web = pki(seed)
        peer = pki(chr(ord(seed) + 3))
        rollout.append(
            {
                "sequence": sequence,
                "node": node,
                "status": "passed",
                "observed_candidate": candidate.copy(),
                "backup": {
                    "archive_sha256": ("c" if node == "dc02" else "d") * 64,
                    "manifest_sha256": ("e" if node == "dc02" else "f") * 64,
                    "verified": True,
                    "restore_verified": True,
                },
                "health": {"readiness": "passed", "peer_mtls": "passed", "drs_replication": "passed"},
                "web_identity_before": web,
                "web_identity_after": web.copy(),
                "peer_identity_before": peer,
                "peer_identity_after": peer.copy(),
            }
        )
    return {
        "schema": "home-center.release-candidate-acceptance.v1",
        "acceptance_id": "hm-dm-0.10.0-dev1",
        "candidate": candidate,
        "predecessor": predecessor,
        "rollout": rollout,
        "rollback_drill": {
            "status": "passed",
            "order": ["dc01", "dc02"],
            "restored_nodes": [
                {"node": "dc01", "observed_predecessor": predecessor.copy()},
                {"node": "dc02", "observed_predecessor": predecessor.copy()},
            ],
            "state_restore": "passed",
            "audit_chain": "passed",
            "backups_reverified": "passed",
        },
        "cluster": {
            "domain_dns_name": "hm.dm",
            "domain_sid": "S-1-5-21-483832520-828804035-215000592",
            "nodes": ["dc01", "dc02"],
            "writer_count": 1,
            "automatic_failover": False,
            "version_parity": "passed",
            "revision_parity": "passed",
            "artifact_parity": "passed",
            "drs_replication": "passed",
            "peer_mtls": "passed",
        },
        "external_access": {
            "policy_default_disabled": True,
            "gateway_tls": "passed",
            "desktop_browser": "passed",
            "mobile_browser": "passed",
            "positive_path": "passed",
            "spoof_rejection": "passed",
            "internal_surfaces_hidden": "passed",
            "rate_limit": "passed",
        },
        "safety": {
            "ad_mutations": 0,
            "dns_mutations": 0,
            "dhcp_mutations": 0,
            "gpo_mutations": 0,
            "automatic_failover": False,
            "secret_scan": "passed",
        },
    }


def verify(value: dict):
    return verify_release_candidate(
        value,
        expected_candidate_revision=CANDIDATE_REVISION,
        expected_candidate_artifact_sha256=CANDIDATE_ARTIFACT,
        expected_predecessor_version="0.9.2",
        expected_predecessor_revision=PREDECESSOR_REVISION,
        expected_predecessor_artifact_sha256=PREDECESSOR_ARTIFACT,
    )


class ReleaseCandidateAcceptanceTests(unittest.TestCase):
    def test_complete_exact_evidence_is_accepted_and_bound_by_digest(self) -> None:
        first = verify(evidence()).result()
        second = verify(json.loads(json.dumps(evidence(), sort_keys=False))).result()
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "accepted")
        self.assertEqual(first["rollout_order"], ["dc02", "dc01"])
        self.assertEqual(first["rollback_order"], ["dc01", "dc02"])
        self.assertRegex(first["evidence_sha256"], r"^[0-9a-f]{64}$")

    def test_candidate_and_predecessor_are_exact_external_expectations(self) -> None:
        cases = (
            ("candidate.revision", lambda value: value["candidate"].__setitem__("revision", "7" * 40), "acceptance_candidate_identity_rejected"),
            ("candidate.artifact", lambda value: value["candidate"].__setitem__("artifact_sha256", "7" * 64), "acceptance_candidate_identity_rejected"),
            ("predecessor.version", lambda value: value["predecessor"].__setitem__("version", "0.8.0"), "acceptance_predecessor_identity_rejected"),
            ("predecessor.revision", lambda value: value["predecessor"].__setitem__("revision", "7" * 40), "acceptance_predecessor_identity_rejected"),
        )
        for name, mutate, code in cases:
            value = evidence()
            mutate(value)
            with self.subTest(name=name), self.assertRaisesRegex(ReleaseCandidateAcceptanceError, code):
                verify(value)

    def test_rollout_order_parity_and_preserved_pki_are_mandatory(self) -> None:
        cases = (
            ("order", lambda value: value["rollout"].reverse(), "acceptance_rollout_order_rejected"),
            ("parity", lambda value: value["rollout"][1]["observed_candidate"].__setitem__("revision", "7" * 40), "acceptance_candidate_parity_rejected"),
            ("web", lambda value: value["rollout"][0]["web_identity_after"].__setitem__("public_key_sha256", "0" * 64), "acceptance_web_identity_changed"),
            ("peer", lambda value: value["rollout"][1]["peer_identity_after"].__setitem__("certificate_sha256", "0" * 64), "acceptance_peer_identity_changed"),
        )
        for name, mutate, code in cases:
            value = evidence()
            mutate(value)
            with self.subTest(name=name), self.assertRaisesRegex(ReleaseCandidateAcceptanceError, code):
                verify(value)

    def test_backup_restore_rollback_and_cluster_health_are_mandatory(self) -> None:
        cases = (
            ("backup", lambda value: value["rollout"][0]["backup"].__setitem__("verified", False), "acceptance_backup_verification_rejected"),
            ("restore", lambda value: value["rollout"][1]["backup"].__setitem__("restore_verified", False), "acceptance_restore_verification_rejected"),
            ("rollback", lambda value: value["rollback_drill"].__setitem__("order", ["dc02", "dc01"]), "acceptance_rollback_order_rejected"),
            ("domain", lambda value: value["cluster"].__setitem__("domain_sid", "S-1-5-21-1"), "acceptance_domain_identity_rejected"),
            ("writer-bool", lambda value: value["cluster"].__setitem__("writer_count", True), "acceptance_cluster_topology_rejected"),
            ("replication", lambda value: value["cluster"].__setitem__("drs_replication", "failed"), "acceptance_cluster_drs_replication_rejected"),
        )
        for name, mutate, code in cases:
            value = evidence()
            mutate(value)
            with self.subTest(name=name), self.assertRaisesRegex(ReleaseCandidateAcceptanceError, code):
                verify(value)

    def test_external_security_and_no_mutation_proofs_are_mandatory(self) -> None:
        cases = (
            ("spoof", lambda value: value["external_access"].__setitem__("spoof_rejection", "failed"), "acceptance_external_spoof_rejection_rejected"),
            ("mobile", lambda value: value["external_access"].__setitem__("mobile_browser", "failed"), "acceptance_external_mobile_browser_rejected"),
            ("ad", lambda value: value["safety"].__setitem__("ad_mutations", 1), "acceptance_ad_mutations_rejected"),
            ("failover", lambda value: value["safety"].__setitem__("automatic_failover", True), "acceptance_safety_failover_rejected"),
        )
        for name, mutate, code in cases:
            value = evidence()
            mutate(value)
            with self.subTest(name=name), self.assertRaisesRegex(ReleaseCandidateAcceptanceError, code):
                verify(value)

    def test_unknown_fields_duplicate_keys_and_oversize_documents_are_rejected(self) -> None:
        value = evidence()
        value["comment"] = "unbounded free form is forbidden"
        with self.assertRaisesRegex(ReleaseCandidateAcceptanceError, "acceptance_document_shape_rejected"):
            verify(value)
        with self.assertRaisesRegex(ReleaseCandidateAcceptanceError, "acceptance_json_duplicate_key"):
            load_acceptance_document(b'{"schema":"one","schema":"two"}')
        with self.assertRaisesRegex(ReleaseCandidateAcceptanceError, "acceptance_document_size_rejected"):
            load_acceptance_document(b"{" + b" " * MAX_ACCEPTANCE_BYTES + b"}")

    def test_cli_accepts_secure_file_and_returns_only_bounded_result(self) -> None:
        cli = ROOT / "deploy/runtime/release-candidate-verify.py"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "acceptance.json"
            path.write_text(json.dumps(evidence()), encoding="utf-8")
            path.chmod(0o600)
            command = [
                sys.executable,
                "-I",
                str(cli),
                "--evidence",
                str(path),
                "--candidate-revision",
                CANDIDATE_REVISION,
                "--candidate-artifact-sha256",
                CANDIDATE_ARTIFACT,
                "--predecessor-version",
                "0.9.2",
                "--predecessor-revision",
                PREDECESSOR_REVISION,
                "--predecessor-artifact-sha256",
                PREDECESSOR_ARTIFACT,
            ]
            accepted = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=10)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            result = json.loads(accepted.stdout)
            self.assertEqual(result["status"], "accepted")

            insecure = copy.deepcopy(evidence())
            insecure["safety"]["dns_mutations"] = 1
            path.write_text(json.dumps(insecure), encoding="utf-8")
            rejected = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=10)
            self.assertEqual(rejected.returncode, 65)
            self.assertEqual(json.loads(rejected.stdout)["reason_code"], "acceptance_dns_mutations_rejected")
            self.assertNotIn("hm.dm", rejected.stdout)

            path.chmod(0o622)
            permissions = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=10)
            self.assertEqual(json.loads(permissions.stdout)["reason_code"], "acceptance_file_permissions_rejected")

            path.chmod(0o600)
            link = Path(temporary) / "acceptance-link.json"
            link.symlink_to(path)
            symlink_command = command.copy()
            symlink_command[symlink_command.index(str(path))] = str(link)
            symlink = subprocess.run(symlink_command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=10)
            self.assertEqual(json.loads(symlink.stdout)["reason_code"], "acceptance_file_rejected")


if __name__ == "__main__":
    unittest.main()

