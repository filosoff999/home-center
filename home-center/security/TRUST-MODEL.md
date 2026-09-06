# Trust model Home Center P2.4

## Identities

- Human: `bootstrap-admin` only for P1; secret stored root/group-readable in `/etc/home-center/secrets/admin.token` and never printed to GitHub evidence.
- Service: local `home-center` Unix account with no login shell and no Linux capabilities.
- Node/peer: stable `node_id` + hashed machine identity + cluster-CA-signed Ed25519 certificate used only for TLS 1.3 mTLS.
- Cluster: `hm-dm-production`, locally generated Ed25519 peer CA; its private key remains only on `dc01` root storage.
- Web: separate node certificate for `dc01.hm.dm` or `dc02.hm.dm`, exact management IP and reserved `home-center.hm.dm` SAN; key and signature profile is ECDSA P-256/SHA-256.
- Web trust anchor: independent P-256 Web CA. Its private key is root-only on `dc01`; `dc02`, clients and `/api/v1/tls/ca.crt` receive only the public certificate.
- Release signer: dedicated offline Home Center ECDSA P-256 key set. Only public SPKI pins belong in the node trust policy; production private signing keys are absent from nodes, artifacts, PR jobs and evidence.

## Authorization

- anonymous: `/healthz`, `/readyz`, `/api/v1/meta` and static login UI;
- authenticated bootstrap admin: versioned read API;
- mutating operations: none in P1, fail-closed;
- peer API: only CA-valid client certificate whose CN equals the configured peer identity.
- TLS Web activation/recovery: only fixed `tls.web.activate.v1` and `tls.web.reconcile.v1` through the bounded helper; no caller-controlled executable, argv, path or network target. Reconcile clears a mutation latch only after exact durable-current and live-listener proof.
- Release channel: DSSE signature threshold + canonical ledger + monotonic checkpoint may authorize only a `VerifiedStableRelease`; verifier has no deployment authority. GDrive, GitHub issue text and a newer CI build are not authority.

## Secret handling

Secrets are generated on `dc01`, delivered to `dc02` only through the existing authenticated SSH bootstrap path, stored outside application release directories, mode `0600/0640`, and excluded from API/audit/backup artifacts. Node private keys are distinct. Neither the peer CA private key nor the Web CA private key is copied to `dc02`.

Web leaf keys exist only in root-controlled candidate storage and a node-local fingerprint-addressed Web release. Every staged candidate/release is bound to a durable random operation marker and exact file digests; cleanup is compare-and-delete, never path-only. The helper may write only `/etc/home-center/pki/web`; peer CA, Web CA and peer node private keys are explicitly inaccessible. Public fingerprints and certificate metadata are evidence-safe; private key material is never evidence.

`0.4.0`, `0.4.1` and `0.4.2` remain quarantined for the recorded Web-profile/lock/marker failures. Exact `0.4.3` is the accepted server-side source baseline after CI, staged `dc02 → dc01`, Web rotation, restricted TLS, peer-mTLS and DRS evidence. Managed-client CA distribution/browser trust remains explicit external evidence and is never implemented as an implicit AD/GPO mutation.

`0.5.0` installs only the read-only channel verifier and contracts through the existing audited exact-artifact rollout. It does not ship a production public trust policy, private signing key, polling timer or auto-deployer. Those authorities remain blocked pending the ADR-0007 trust-bootstrap gates.
