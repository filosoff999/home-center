# ADR-0006: Web TLS certificate lifecycle and trust model

Status: **Accepted for P2.3 implementation**  
Date: **2026-09-06**  
Related: `#10`, ADR-0003, ADR-0005

## Context

Home Center currently has a strong peer mTLS identity for the `dc01`/`dc02` control-plane channel, but the Web listener must also present a certificate that managed Windows clients can trust without browser warnings. Web identity and peer mTLS identity have different trust and key-usage requirements and must not be silently coupled.

P2.3 must introduce installation, rotation, expiry monitoring and rollback without creating a generic root shell, without putting private keys in GitHub artifacts/logs/issues, without mutating Samba AD/DNS/DHCP, and without enabling automatic failover.

## Decision

### Trust anchor

The existing Home Center HM.DM internal CA is the P2.3 Web TLS trust anchor. Its public certificate may be distributed to managed clients and is exposed by Home Center as a public trust-anchor download. The CA private key remains root-controlled on `dc01` and is never copied to `dc02`, GitHub, an artifact, API response or audit record.

Trusting the CA on managed Windows clients is an explicit administrative/domain-policy operation outside the Home Center P2.3 mutation path. P2.3 does not silently edit AD/GPO. Production acceptance may use the existing HM.DM administrative channel to establish that trust and then prove the browser/OS trust result.

### Identities

- current canonical production Web endpoint: `dc01.hm.dm`;
- node Web identities: `dc01.hm.dm` and `dc02.hm.dm`;
- reserved future failover/VIP name: `home-center.hm.dm`;
- Web certificates carry the node FQDN, the reserved future VIP name and the exact node management IP in SAN;
- the reserved VIP SAN is compatibility preparation only and does **not** enable or advertise automatic failover;
- Web certificates require `CA:FALSE` and server authentication only; client authentication EKU is rejected;
- peer mTLS continues to use the existing node identity certificate with TLS 1.3 and `CERT_REQUIRED` and is never replaced implicitly by a Web certificate.

### Listener policy

- Web listener minimum: TLS 1.2;
- peer listener minimum: TLS 1.3;
- when a validated separate Web identity exists under `/etc/home-center/pki/web/current`, the Web listener uses it;
- before first P2.3 activation, the listener may use the accepted legacy peer certificate as an explicit migration fallback;
- status reports whether the Web listener is using `separate-web-identity` or `legacy-peer-fallback`.

### Candidate and release model

A new Web identity is admitted only through a root-owned staged candidate inside a dedicated Web PKI subtree:

- Web lifecycle root: `/etc/home-center/pki/web`;
- candidate directory: `/etc/home-center/pki/web/candidate`;
- release directory: `/etc/home-center/pki/web/releases`;
- active symlink: `/etc/home-center/pki/web/current`;
- certificate and key are bounded regular files; symlinks, wrong owner, unsafe modes and oversized files are rejected;
- candidate must pass strict chain validation, minimum remaining validity, node hostname SAN, reserved VIP SAN, node IP SAN, server-only EKU profile and exact public-key match;
- validated material is copied into a fingerprint-addressed release below the Web lifecycle root;
- the private candidate is removed after a validated immutable release exists;
- `current` is an atomic symlink switch and may only resolve inside the Web release root.

The Web lifecycle subtree is intentionally separate from `/etc/home-center/pki/ca.key`, `/etc/home-center/pki/node.key` and the peer certificate. The privileged helper has no writable mount for CA or peer identity material.

### Privilege boundary

ADR-0005 remains the base helper authority. P2.3 adds exactly one compile-time action, `tls.web.activate.v1`, through the P2.3 helper extension. Policy can enable or disable it but cannot provide executable, argv, paths, network targets or environment.

The action may:

1. validate the fixed staged candidate;
2. create a fixed Web certificate release;
3. atomically switch `current`;
4. restart only `home-center.service`;
5. connect only to the local HM.DM node address to prove the exact certificate fingerprint is actually being served;
6. restore the previous release and re-prove the previous presented fingerprint on failure.

The helper retains deny-by-default authorization, deterministic replay/evidence, bounded output/time/environment and no shell evaluation. Its explicit filesystem write surface is limited to `/etc/home-center/pki/web`; its IP egress is limited to the two fixed HM.DM node addresses required for bounded postflight verification.

### Renewal and monitoring

The unprivileged Home Center maintenance path monitors certificate state twice per day with randomized delay. A separate Web identity is considered renewal-required when it is absent, approaching the renewal threshold, has an invalid trust chain or no longer matches the expected hostname. The maintenance path may request the fixed helper action only when a complete staged candidate is present. It does not read or return private-key contents.

A controlled HM.DM certificate-issuance/deployment script may create server-only node candidates using the root-controlled CA, stage `dc02` first, run activation and exact presented-certificate acceptance, and only then proceed to `dc01`. This script has fixed node identities/addresses and is part of the audited deployment channel, not a generic remote-execution API.

## Failure and recovery model

- invalid/expired/short-lived/wrong-host/wrong-VIP/wrong-IP/wrong-chain/wrong-key/clientAuth candidate: reject before activation;
- partial candidate: do not invoke activation; report blocked maintenance state;
- activation restart/postflight failure: atomically restore the previous release, restart Home Center, and verify the previous presented fingerprint;
- rollback verification failure: return explicit failed/unknown recovery state; never report success;
- interruption is governed by ADR-0005 durable helper in-flight recovery semantics;
- `dc02` failure blocks `dc01` promotion;
- peer mTLS regression blocks production acceptance;
- no AD/DNS/DHCP mutation is part of certificate rotation.

## Alternatives considered

### Reuse the peer mTLS certificate permanently for Web TLS

Rejected. It couples browser and machine identities, carries client-auth semantics into the Web surface and makes independent rotation unsafe.

### Public ACME certificate as the initial trust model

Deferred. The current Home Center endpoint is internal HM.DM infrastructure; depending on public DNS/challenges would add external availability and credential dependencies. The certificate lifecycle remains CA-source-neutral enough to support a future external issuer/import path.

### Put CA signing into the network API or generic privileged helper

Rejected. The CA private key must not become API-accessible and the helper must not become a remote signing or shell surface.

## Security and data impact

Certificate metadata and SHA-256 fingerprints are non-secret audit/status data. Private keys are never serialized into API status, helper evidence, logs, GitHub artifacts or issues. The CA private key remains root-only on `dc01`; node Web private keys exist only in bounded staging and the node-local Web release store.

## Compatibility and migration impact

P2.3 preserves the existing peer mTLS certificate paths and accepts a temporary Web fallback to the existing certificate until the first separate Web identity is activated. Existing clients may continue to connect during staged rollout. The future VIP SAN is included now so later witness/fencing-certified failover does not require an emergency certificate identity change.

## Consequences

Home Center gets a distinct, observable and rollback-safe Web identity lifecycle while preserving the previously accepted P2.2 privilege boundary and two-node peer mTLS behavior. Browser trust becomes deterministic once the Home Center CA is explicitly trusted on managed Windows clients. Automatic failover remains disabled.

## Validation / evidence

P2.3 requires all of the following before production acceptance:

- Python 3.12 and 3.14 deterministic CI PASS on GitHub-hosted runners;
- negative candidate/profile/key/rollback tests;
- reproducible immutable artifact;
- staged `dc02 → dc01` activation evidence with exact certificate fingerprints;
- OS/browser hostname and chain acceptance on a managed Windows client;
- synthetic expiry/renewal and failed-rotation rollback evidence;
- peer mTLS PASS in both directions after Web rotation;
- Samba domain SID/DRS health preserved and no AD/DNS/DHCP mutation evidence.

Supersedes: none.  
Superseded by: none.
