# Trust model Home Center P2.3

## Identities

- Human: `bootstrap-admin` only for P1; secret stored root/group-readable in `/etc/home-center/secrets/admin.token` and never printed to GitHub evidence.
- Service: local `home-center` Unix account with no login shell and no Linux capabilities.
- Node/peer: stable `node_id` + hashed machine identity + cluster-CA-signed Ed25519 certificate used only for TLS 1.3 mTLS.
- Cluster: `hm-dm-production`, locally generated Ed25519 peer CA; its private key remains only on `dc01` root storage.
- Web: separate node certificate for `dc01.hm.dm` or `dc02.hm.dm`, exact management IP and reserved `home-center.hm.dm` SAN; key and signature profile is ECDSA P-256/SHA-256.
- Web trust anchor: independent P-256 Web CA. Its private key is root-only on `dc01`; `dc02`, clients and `/api/v1/tls/ca.crt` receive only the public certificate.

## Authorization

- anonymous: `/healthz`, `/readyz`, `/api/v1/meta` and static login UI;
- authenticated bootstrap admin: versioned read API;
- mutating operations: none in P1, fail-closed;
- peer API: only CA-valid client certificate whose CN equals the configured peer identity.
- TLS Web activation/recovery: only fixed `tls.web.activate.v1` and `tls.web.reconcile.v1` through the bounded helper; no caller-controlled executable, argv, path or network target. Reconcile clears a mutation latch only after exact durable-current and live-listener proof.

## Secret handling

Secrets are generated on `dc01`, delivered to `dc02` only through the existing authenticated SSH bootstrap path, stored outside application release directories, mode `0600/0640`, and excluded from API/audit/backup artifacts. Node private keys are distinct. Neither the peer CA private key nor the Web CA private key is copied to `dc02`.

Web leaf keys exist only in root-controlled candidate storage and a node-local fingerprint-addressed Web release. Every staged candidate/release is bound to a durable random operation marker and exact file digests; cleanup is compare-and-delete, never path-only. The helper may write only `/etc/home-center/pki/web`; peer CA, Web CA and peer node private keys are explicitly inaccessible. Public fingerprints and certificate metadata are evidence-safe; private key material is never evidence.

`0.4.0` is not trusted for deployment because it coupled Web TLS to Ed25519. `0.4.1` is also quarantined because its shell lock admission rejected an empty regular flock file. `0.4.2` becomes trusted only after exact-head CI, immutable digest verification, staged `dc02 → dc01` rollout, browser handshake/trust, peer-mTLS and DRS acceptance.
