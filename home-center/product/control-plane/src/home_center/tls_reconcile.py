from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from home_center import tls_activate  # noqa: E402

SCHEMA = "home-center.tls-reconcile-result.v1"


def reconcile() -> dict[str, str]:
    with tls_activate._mutation_lock():
        spec = tls_activate._spec()
        tls_activate._ensure_release_root()
        tls_activate._recover_release_stages()
        tls_activate._recover_pending_candidate_owner()
        tls_activate._recover_current_pending_links()
        candidate_paths = (
            tls_activate.CANDIDATE_OWNER,
            tls_activate.CANDIDATE_CERT,
            tls_activate.CANDIDATE_KEY,
        )
        candidate_present = []
        for path in candidate_paths:
            try:
                path.lstat()
            except FileNotFoundError:
                candidate_present.append(False)
            else:
                candidate_present.append(True)
        if any(candidate_present):
            if not candidate_present[0]:
                raise tls_activate.ActivationError("unowned_candidate_recovery_rejected")
            tls_activate._recover_owned_candidate()
        current = tls_activate._current_release()
        fingerprint, anchor = tls_activate._rollback_identity(spec, current, verify_live=False)
        mode = "legacy-peer-fallback"
        release = "legacy"
        if current is not None:
            certificate = current / "tls.crt"
            private_key = current / "tls.key"
            if anchor == tls_activate.WEB_CA_CERT:
                candidate = tls_activate.validate_candidate(spec, certificate, private_key)
                mode = "separate-web-identity"
            else:
                tls_activate._regular_secure(certificate, private=False)
                tls_activate._regular_secure(private_key, private=True)
                tls_activate._run(
                    [
                        tls_activate.OPENSSL,
                        "verify",
                        "-x509_strict",
                        "-CAfile",
                        str(tls_activate.PEER_CA_CERT),
                        str(certificate),
                    ]
                )
                tls_activate._run(
                    [
                        tls_activate.OPENSSL,
                        "x509",
                        "-in",
                        str(certificate),
                        "-noout",
                        "-checkend",
                        str(tls_activate.MIN_VALIDITY_SECONDS),
                    ]
                )
                tls_activate._require_expected_identities(spec, certificate)
                if tls_activate._key_public(private_key) != tls_activate._cert_public(certificate):
                    raise tls_activate.ActivationError("reconcile_migration_key_mismatch")
                candidate = tls_activate._fingerprint(certificate)
                mode = "quarantined-peer-web-identity"
            if candidate != fingerprint:
                raise tls_activate.ActivationError("reconcile_fingerprint_mismatch")
            if current.name != fingerprint[:24]:
                raise tls_activate.ActivationError("reconcile_release_identity_mismatch")
            release = current.name
        try:
            tls_activate._presented_fingerprint(spec, fingerprint, anchor, timeout_seconds=3)
        except tls_activate.ActivationError:
            # The durable current link is authoritative after an interrupted
            # switch. Converge the listener to it, then prove the exact chain,
            # hostname and fingerprint before clearing the helper latch.
            tls_activate._restart()
            tls_activate._presented_fingerprint(spec, fingerprint, anchor, timeout_seconds=20)
        return {
            "schema": SCHEMA,
            "status": "reconciled",
            "node_id": spec["node_id"],
            "mode": mode,
            "release": release,
            "certificate_sha256": fingerprint,
        }


def main() -> int:
    try:
        result = reconcile()
    except Exception:
        result = {"schema": SCHEMA, "status": "failed", "reason": "reconcile_validation_failed"}
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
