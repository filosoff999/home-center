"""Deterministic two-node 0.9 release-candidate E2E.

This runs entirely on the CI host.  It proves the artifact/runtime/evidence
chain; live HM.DM production acceptance remains a separate explicit gate.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))

from home_center import __version__  # noqa: E402
from home_center.backup import create_backup, verify_backup  # noqa: E402
from home_center.config import Config, ExternalAccessConfig, Peer  # noqa: E402
from home_center.local_admin_auth import (  # noqa: E402
    CREDENTIAL_SCHEMA,
    KDF_DKLEN,
    KDF_MAXMEM,
    KDF_N,
    KDF_P,
    KDF_R,
    SALT_BYTES,
)
from home_center.release_candidate import ReleaseCandidateAcceptanceError, verify_release_candidate  # noqa: E402
from home_center.runtime import Runtime  # noqa: E402
from home_center.server import HomeCenterServer  # noqa: E402
from home_center.api_v2 import RuntimeRequestHandlerV2  # noqa: E402
from home_center.util import sha256_file  # noqa: E402


CANDIDATE_REVISION = "9" * 40
PREDECESSOR_REVISION = "29b2f61071067028c14febbbaf0103c5600380e9"
PREDECESSOR_ARTIFACT = "66531867f806c6665f41d2bb82dccfb5670403acd0c9988271714da09172f668"
USERNAME = "admin"
PASSWORD = "e2e correct horse battery staple"


def _credential() -> dict[str, object]:
    salt = b"e" * SALT_BYTES
    verifier = hashlib.scrypt(
        PASSWORD.encode("utf-8"),
        salt=salt,
        n=KDF_N,
        r=KDF_R,
        p=KDF_P,
        maxmem=KDF_MAXMEM,
        dklen=KDF_DKLEN,
    )
    return {
        "schema": CREDENTIAL_SCHEMA,
        "username": USERNAME,
        "kdf": "scrypt",
        "n": KDF_N,
        "r": KDF_R,
        "p": KDF_P,
        "dklen": KDF_DKLEN,
        "salt_b64": base64.b64encode(salt).decode("ascii"),
        "verifier_b64": base64.b64encode(verifier).decode("ascii"),
    }


def _pki(seed: str) -> dict[str, str]:
    return {
        "ca_sha256": hashlib.sha256(f"{seed}:ca".encode()).hexdigest(),
        "certificate_sha256": hashlib.sha256(f"{seed}:certificate".encode()).hexdigest(),
        "public_key_sha256": hashlib.sha256(f"{seed}:public-key".encode()).hexdigest(),
    }


class _Node:
    def __init__(self, root: Path, name: str) -> None:
        node_id = f"hm-dm-{name}"
        peer_name = "dc01" if name == "dc02" else "dc02"
        peer_id = f"hm-dm-{peer_name}"
        node_root = root / name
        web = node_root / "web"
        secrets = node_root / "secrets"
        web.mkdir(parents=True)
        secrets.mkdir()
        (web / "index.html").write_text("<!doctype html><meta name=viewport content='width=device-width'>", encoding="utf-8")
        for filename, content in (
            ("session.key", b"s" * 32),
            ("audit.key", (b"1" if name == "dc01" else b"2") * 32),
            ("node.key", b"e2e-private-placeholder"),
        ):
            path = secrets / filename
            path.write_bytes(content)
            path.chmod(0o600)
        credential = secrets / "local-admin.json"
        credential.write_text(json.dumps(_credential(), sort_keys=True), encoding="utf-8")
        credential.chmod(0o640)
        (secrets / "node.crt").write_text("e2e-peer-certificate", encoding="utf-8")
        (secrets / "cluster-ca.crt").write_text("e2e-peer-ca", encoding="utf-8")
        (secrets / "web-ca.crt").write_text("e2e-web-ca", encoding="utf-8")
        profile = node_root / "profile.json"
        profile.write_text((ROOT / "deploy/profiles/hm-dm-two-node.v1.json").read_text(encoding="utf-8"), encoding="utf-8")
        self.config = Config(
            cluster_id="hm-dm-production",
            node_id=node_id,
            node_name=name,
            role="leader" if name == "dc01" else "standby",
            management_address="127.0.0.1",
            web_port=8443,
            peer_port=9443,
            state_db=node_root / "state.sqlite3",
            backup_dir=node_root / "backups",
            web_root=web,
            local_admin_credentials_file=credential,
            session_key_file=secrets / "session.key",
            audit_key_file=secrets / "audit.key",
            tls_certificate=secrets / "node.crt",
            tls_private_key=secrets / "node.key",
            cluster_ca=secrets / "cluster-ca.crt",
            web_ca=secrets / "web-ca.crt",
            deployment_profile=profile,
            peer=Peer(
                node_id=peer_id,
                name=peer_name,
                address="127.0.0.2",
                url="https://127.0.0.2:9443",
                certificate_name=f"home-center-{peer_name}",
            ),
            reconcile_interval_seconds=15,
            peer_timeout_seconds=1,
            external_access=ExternalAccessConfig(
                enabled=True,
                public_hostname="home.example.net",
                trusted_proxy_addresses=("127.0.0.1",),
            ),
        )
        self.runtime = Runtime(self.config, local_admin_expected_uid=os.geteuid())
        capability = {
            "schema": "home-center.node-capability.v1",
            "observed_at": "2026-09-06T00:00:00Z",
            "node": {"id": node_id, "name": name, "role": self.config.role, "address": "127.0.0.1"},
        }
        with self.runtime.reconciler._lock:
            self.runtime.reconciler._latest_local = capability
        self.runtime.store.upsert_node(capability, "ready")
        self.runtime.store.audit(
            actor="system:e2e",
            action="candidate.observe",
            target=node_id,
            outcome="accepted",
            correlation_id=f"e2e-{name}",
            details={"version": __version__},
        )
        self.server = HomeCenterServer(("127.0.0.1", 0), RuntimeRequestHandlerV2, self.runtime)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    @staticmethod
    def forwarded(user_agent: str = "HomeCenter-E2E/Desktop") -> dict[str, str]:
        return {
            "X-Forwarded-For": "203.0.113.18",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "home.example.net",
            "User-Agent": user_agent,
        }

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict | None = None,
        headers: dict[str, str] | None = None,
    ):
        request_headers = {"Accept": "application/json", **(headers or {})}
        data = None
        if body is not None:
            request_headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        return urllib.request.urlopen(
            urllib.request.Request(self.base + path, method=method, headers=request_headers, data=data), timeout=3
        )

    def exercise_external_path(self) -> None:
        with self.request("/external/healthz", headers=self.forwarded()) as response:
            assert json.load(response) == {"schema": "home-center.external-health.v1", "status": "ok"}
        for agent in ("HomeCenter-E2E/Desktop", "HomeCenter-E2E/Android-Mobile"):
            with self.request("/", headers=self.forwarded(agent)) as response:
                assert response.status == 200
                assert b"viewport" in response.read()
        browser = {
            **self.forwarded(),
            "Origin": "https://home.example.net",
            "Sec-Fetch-Site": "same-origin",
        }
        with self.request(
            "/api/v1/session",
            method="POST",
            body={"provider": "local", "username": USERNAME, "password": PASSWORD},
            headers=browser,
        ) as response:
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        with self.request("/api/v1/external-access", headers={**self.forwarded(), "Cookie": cookie}) as response:
            status = json.load(response)
            assert status["effective_enabled"] is True
        try:
            self.request("/readyz", headers=self.forwarded())
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("external request reached internal readiness")

    def backup_evidence(self) -> tuple[str, str]:
        with patch("home_center.backup.load_config", return_value=self.config):
            archive, sidecar = create_backup(retain=2)
        manifest = json.loads(sidecar.read_text(encoding="utf-8"))
        restored = verify_backup(
            archive,
            expected_archive_hash=manifest["archive_sha256"],
            audit_key=self.config.audit_key_file.read_bytes(),
        )
        assert restored["verification"] == {"sqlite_integrity": "ok", "audit_chain": "ok"}
        return manifest["archive_sha256"], sha256_file(sidecar)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.runtime.store.close()


class ReleaseCandidateE2E(unittest.TestCase):
    def test_artifact_two_node_external_backup_restore_and_acceptance_chain(self) -> None:
        self.assertEqual(__version__, "0.9.1")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts: list[bytes] = []
            artifact_digest = ""
            for attempt in ("first", "second"):
                output = root / f"artifact-{attempt}"
                environment = {
                    **os.environ,
                    "HOME_CENTER_VERSION": "0.9.1",
                    "HOME_CENTER_REVISION": CANDIDATE_REVISION,
                    "HOME_CENTER_RELEASE_BUILD": "0",
                    "SOURCE_DATE_EPOCH": "1767225600",
                }
                result = subprocess.run(
                    ["bash", str(ROOT / "deploy/scripts/build-artifact.sh"), str(output)],
                    cwd=ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                archive = output / "home-center-0.9.1-linux-amd64.tar.gz"
                artifacts.append(archive.read_bytes())
                artifact_digest = sha256_file(archive)
                if attempt == "first":
                    with tarfile.open(archive, "r:gz") as bundle:
                        self.assertEqual(bundle.extractfile("./VERSION").read(), b"0.9.1\n")
                        self.assertEqual(bundle.extractfile("./REVISION").read(), (CANDIDATE_REVISION + "\n").encode())
                        self.assertIsNotNone(bundle.getmember("./release-candidate-verify.py"))
                        bootstrap = bundle.extractfile("./deploy/bootstrap-hm-dm.sh").read().decode()
                        self.assertIn("ADMITTED_SOURCE_V090_VERSION=0.9.0", bootstrap)
                        self.assertIn(f"ADMITTED_SOURCE_V090_REVISION={PREDECESSOR_REVISION}", bootstrap)
            self.assertEqual(artifacts[0], artifacts[1])

            rollout: list[dict] = []
            nodes: list[_Node] = []
            try:
                for sequence, name in enumerate(("dc02", "dc01"), start=1):
                    node = _Node(root, name)
                    nodes.append(node)
                    node.exercise_external_path()
                    archive_sha, manifest_sha = node.backup_evidence()
                    web = _pki(f"{name}:web")
                    peer = _pki(f"{name}:peer")
                    rollout.append(
                        {
                            "sequence": sequence,
                            "node": name,
                            "status": "passed",
                            "observed_candidate": {
                                "version": "0.9.1",
                                "revision": CANDIDATE_REVISION,
                                "artifact_sha256": artifact_digest,
                            },
                            "backup": {
                                "archive_sha256": archive_sha,
                                "manifest_sha256": manifest_sha,
                                "verified": True,
                                "restore_verified": True,
                            },
                            "health": {
                                "readiness": "passed",
                                "peer_mtls": "passed",
                                "drs_replication": "passed",
                            },
                            "web_identity_before": web,
                            "web_identity_after": web.copy(),
                            "peer_identity_before": peer,
                            "peer_identity_after": peer.copy(),
                        }
                    )
            finally:
                for node in reversed(nodes):
                    node.close()

            predecessor = {
                "version": "0.9.0",
                "revision": PREDECESSOR_REVISION,
                "artifact_sha256": PREDECESSOR_ARTIFACT,
            }
            document = {
                "schema": "home-center.release-candidate-acceptance.v1",
                "acceptance_id": "ci-0.9.1-two-node-e2e",
                "candidate": {
                    "version": "0.9.1",
                    "revision": CANDIDATE_REVISION,
                    "artifact_sha256": artifact_digest,
                },
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
            result = verify_release_candidate(
                document,
                expected_candidate_revision=CANDIDATE_REVISION,
                expected_candidate_artifact_sha256=artifact_digest,
                expected_predecessor_revision=PREDECESSOR_REVISION,
                expected_predecessor_artifact_sha256=PREDECESSOR_ARTIFACT,
            ).result()
            self.assertEqual(result["status"], "accepted")
            self.assertEqual(result["rollout_order"], ["dc02", "dc01"])

            document["candidate"]["artifact_sha256"] = "0" * 64
            with self.assertRaisesRegex(ReleaseCandidateAcceptanceError, "acceptance_candidate_identity_rejected"):
                verify_release_candidate(
                    document,
                    expected_candidate_revision=CANDIDATE_REVISION,
                    expected_candidate_artifact_sha256=artifact_digest,
                    expected_predecessor_revision=PREDECESSOR_REVISION,
                    expected_predecessor_artifact_sha256=PREDECESSOR_ARTIFACT,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
