from __future__ import annotations

import base64
import copy
import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "product/control-plane/src"))

from home_center.module_artifact import (  # noqa: E402
    MANIFEST_BINDING_ALGORITHM,
    PAYLOAD_TYPE,
    ModuleArtifactError,
    canonical_json,
    manifest_binding_sha256,
    stage_module_artifact,
    verify_module_artifact,
)
try:  # unittest discovery imports tests as top-level modules.
    from test_module_manifest import valid_manifest  # type: ignore[import-not-found] # noqa: E402
except ModuleNotFoundError:  # Direct module selection imports through the namespace package.
    from tests.test_module_manifest import valid_manifest  # noqa: E402


class ModuleArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._keys = tempfile.TemporaryDirectory()
        cls.key, cls.public, cls.keyid = cls.generate_key("primary", "prime256v1")
        cls.second_key, cls.second_public, cls.second_keyid = cls.generate_key("secondary", "prime256v1")
        cls.p384_key, cls.p384_public, cls.p384_keyid = cls.generate_key("p384", "secp384r1")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._keys.cleanup()

    @classmethod
    def generate_key(cls, name: str, curve: str) -> tuple[Path, Path, str]:
        key = Path(cls._keys.name) / f"fixture-only-{name}-private-key.pem"
        public = Path(cls._keys.name) / f"fixture-only-{name}-public-key.pem"
        subprocess.run(
            ["/usr/bin/openssl", "ecparam", "-name", curve, "-genkey", "-noout", "-out", str(key)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        subprocess.run(
            ["/usr/bin/openssl", "pkey", "-in", str(key), "-pubout", "-out", str(public)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        der = subprocess.run(
            ["/usr/bin/openssl", "pkey", "-pubin", "-in", str(public), "-outform", "DER"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
        return key, public, "sha256:" + hashlib.sha256(der).hexdigest()

    @staticmethod
    def policy_key(public: Path, keyid: str, *, state: str = "active") -> dict:
        return {
            "keyid": keyid,
            "algorithm": "ecdsa-p256-sha256",
            "state": state,
            "public_key_pem": public.read_text(encoding="ascii"),
        }

    @staticmethod
    def archive(*, unsafe_name: str | None = None, symlink: bool = False) -> bytes:
        output = io.BytesIO()
        with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as bundle:
                name = unsafe_name or "payload/module.bin"
                info = tarfile.TarInfo(name)
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                info.mtime = 0
                info.mode = 0o644
                if symlink:
                    info.type = tarfile.SYMTYPE
                    info.linkname = "../target"
                    bundle.addfile(info)
                else:
                    content = b"bounded module payload\n"
                    info.size = len(content)
                    bundle.addfile(info, io.BytesIO(content))
        return output.getvalue()

    @staticmethod
    def pae(payload: bytes) -> bytes:
        payload_type = PAYLOAD_TYPE.encode("ascii")
        return (
            b"DSSEv1 "
            + str(len(payload_type)).encode()
            + b" "
            + payload_type
            + b" "
            + str(len(payload)).encode()
            + b" "
            + payload
        )

    @classmethod
    def signature(cls, payload: bytes, key: Path) -> str:
        signature = subprocess.run(
            ["/usr/bin/openssl", "dgst", "-sha256", "-sign", str(key)],
            input=cls.pae(payload),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
        return base64.b64encode(signature).decode("ascii")

    def material(
        self,
        *,
        artifact: bytes | None = None,
        signer_keys: list[tuple[Path, str]] | None = None,
        threshold: int = 1,
        policy_keys: list[dict] | None = None,
        policy_threshold: int = 1,
    ) -> tuple[bytes, bytes, bytes, bytes, dict, dict]:
        artifact = artifact if artifact is not None else self.archive()
        signer_keys = signer_keys or [(self.key, self.keyid)]
        manifest = valid_manifest()
        manifest["artifact"]["sha256"] = hashlib.sha256(artifact).hexdigest()
        manifest["artifact"]["size_bytes"] = len(artifact)
        manifest["artifact"]["provenance"]["signer_key_ids"] = [keyid for _, keyid in signer_keys]
        manifest["artifact"]["provenance"]["threshold"] = threshold
        statement = {
            "schema": "home-center.module-provenance.v1",
            "module": {
                "id": manifest["module"]["id"],
                "version": manifest["module"]["version"],
                "publisher": manifest["module"]["publisher"],
            },
            "manifest": {
                "schema": manifest["schema"],
                "binding_algorithm": MANIFEST_BINDING_ALGORITHM,
                "sha256": manifest_binding_sha256(manifest),
            },
            "artifact": {
                "sha256": manifest["artifact"]["sha256"],
                "size_bytes": len(artifact),
                "media_type": manifest["artifact"]["media_type"],
            },
            "source": {
                "repository": "ControlCenterSoft/reference-module",
                "revision": "a" * 40,
                "ref": "refs/tags/v1.2.3",
                "workflow": ".github/workflows/release.yml",
                "run_id": 12345,
                "run_attempt": 1,
            },
        }
        payload = canonical_json(statement)
        manifest["artifact"]["provenance"]["statement_sha256"] = hashlib.sha256(payload).hexdigest()
        envelope = {
            "payloadType": PAYLOAD_TYPE,
            "payload": base64.b64encode(payload).decode("ascii"),
            "signatures": [
                {"keyid": keyid, "sig": self.signature(payload, key)} for key, keyid in signer_keys
            ],
        }
        policy = {
            "schema": "home-center.module-trust-policy.v1",
            "publisher": manifest["module"]["publisher"],
            "payload_type": PAYLOAD_TYPE,
            "threshold": policy_threshold,
            "keys": policy_keys or [self.policy_key(self.public, self.keyid)],
        }
        return (
            canonical_json(manifest),
            canonical_json(envelope),
            canonical_json(policy),
            artifact,
            manifest,
            statement,
        )

    def assert_rejected(self, code: str, material: tuple[bytes, bytes, bytes, bytes, dict, dict]) -> None:
        with self.assertRaises(ModuleArtifactError) as raised:
            verify_module_artifact(*material[:4])
        self.assertEqual(raised.exception.code, code)

    def test_valid_material_verifies_and_stages_idempotently(self) -> None:
        material = self.material()
        verified = verify_module_artifact(*material[:4])
        self.assertEqual(verified.module_id, "org.home-center.reference-stateful")
        self.assertEqual(verified.signing_key_ids, (self.keyid,))
        self.assertEqual(
            verified.object_key,
            f"sha256/{verified.artifact_sha256[:2]}/{verified.artifact_sha256}/artifact.tar.gz",
        )
        with self.assertRaises(FrozenInstanceError):
            verified.version = "9.9.9"  # type: ignore[misc]

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            first = stage_module_artifact(*material[:4], object_store_root=root)
            second = stage_module_artifact(*material[:4], object_store_root=root)
            self.assertTrue(first.created)
            self.assertFalse(second.created)
            self.assertEqual((root / verified.object_key).read_bytes(), material[3])
            self.assertEqual(stat_mode(root / verified.object_key), 0o600)

    def test_digest_and_size_mismatch_fail_before_staging(self) -> None:
        material = list(self.material())
        material[3] += b"x"
        self.assert_rejected("artifact_size_mismatch", tuple(material))

        material = list(self.material())
        changed = bytearray(material[3])
        changed[-1] ^= 1
        material[3] = bytes(changed)
        self.assert_rejected("artifact_sha256_mismatch", tuple(material))

    def test_manifest_and_statement_tampering_fail_closed(self) -> None:
        material = list(self.material())
        manifest = copy.deepcopy(material[4])
        manifest["permissions"].append("system.shell.execute")
        material[0] = canonical_json(manifest)
        self.assert_rejected("statement_manifest_mismatch", tuple(material))

        material = list(self.material())
        statement = copy.deepcopy(material[5])
        statement["source"]["revision"] = "b" * 40
        payload = canonical_json(statement)
        envelope = json.loads(material[1])
        envelope["payload"] = base64.b64encode(payload).decode("ascii")
        material[1] = canonical_json(envelope)
        self.assert_rejected("provenance_statement_sha256_mismatch", tuple(material))

    def test_signature_threshold_and_key_policy_are_enforced(self) -> None:
        material = list(self.material())
        envelope = json.loads(material[1])
        raw = bytearray(base64.b64decode(envelope["signatures"][0]["sig"]))
        raw[-1] ^= 1
        envelope["signatures"][0]["sig"] = base64.b64encode(raw).decode("ascii")
        material[1] = canonical_json(envelope)
        self.assert_rejected("signature_verification_failed", tuple(material))

        two_key_material = self.material(
            signer_keys=[(self.key, self.keyid), (self.second_key, self.second_keyid)],
            threshold=2,
            policy_keys=[
                self.policy_key(self.public, self.keyid),
                self.policy_key(self.second_public, self.second_keyid),
            ],
        )
        material = list(two_key_material)
        envelope = json.loads(material[1])
        envelope["signatures"] = envelope["signatures"][:1]
        material[1] = canonical_json(envelope)
        self.assert_rejected("signature_threshold_not_met", tuple(material))

        wrong_curve = list(self.material(policy_keys=[self.policy_key(self.p384_public, self.p384_keyid)]))
        self.assert_rejected("trust_key_algorithm_rejected", tuple(wrong_curve))

    def test_non_archive_traversal_and_link_are_rejected(self) -> None:
        self.assert_rejected("archive_format_rejected", self.material(artifact=b"not-an-archive"))
        self.assert_rejected("archive_member_name_rejected", self.material(artifact=self.archive(unsafe_name="../escape")))
        self.assert_rejected("archive_member_type_rejected", self.material(artifact=self.archive(symlink=True)))

    def test_bad_signature_has_no_store_effect_and_collision_is_rejected(self) -> None:
        material = list(self.material())
        envelope = json.loads(material[1])
        envelope["signatures"][0]["sig"] = base64.b64encode(b"invalid-signature").decode("ascii")
        material[1] = canonical_json(envelope)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with self.assertRaises(ModuleArtifactError):
                stage_module_artifact(*material[:4], object_store_root=root)
            self.assertEqual(list(root.iterdir()), [])

        material = self.material()
        verified = verify_module_artifact(*material[:4])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            stage_module_artifact(*material[:4], object_store_root=root)
            (root / verified.object_key).write_bytes(b"corrupt")
            with self.assertRaises(ModuleArtifactError) as raised:
                stage_module_artifact(*material[:4], object_store_root=root)
            self.assertEqual(raised.exception.code, "object_store_collision_rejected")

    def test_relative_and_symlink_store_roots_are_rejected(self) -> None:
        material = self.material()
        with self.assertRaises(ModuleArtifactError) as relative:
            stage_module_artifact(*material[:4], object_store_root=Path("relative"))
        self.assertEqual(relative.exception.code, "object_store_root_rejected")

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            target = base / "target"
            target.mkdir()
            link = base / "link"
            os.symlink(target, link)
            with self.assertRaises(ModuleArtifactError) as symlink:
                stage_module_artifact(*material[:4], object_store_root=link)
            self.assertEqual(symlink.exception.code, "object_store_root_rejected")


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


if __name__ == "__main__":
    unittest.main()
