# Upgrade plan Home Center 0.8.0 → 0.9.0

## Status

This is the exact upgrade and acceptance contract, not production authorization. The target revision and artifact digest remain unresolved until the 0.9 release PR passes exact-main CI and reproducible build-twice verification.

Rollout order is **dc02 → canary/soak → dc01 → cluster acceptance**. Reverse rollback order is **dc01 → dc02**. Automatic update and automatic failover remain disabled.

## Exact admitted predecessor

Both nodes must start from the published 0.8.0 identity:

- version: `0.8.0`;
- revision: `bbb2b1e952b2072c8ce30ad6b3220c7c14280949`;
- artifact SHA-256: `25fb72fdffab972703d9be2b7e457b1f4ed053bc1c013a6237558fd693e73ca8`;
- release: `/opt/home-center/releases/0.8.0-bbb2b1e952b2-25fb72fdffab`.

A direct upgrade from 0.7.x or older fails closed.

## Target artifact

Only an immutable artifact built from the exact merged 0.9 main revision is eligible:

```text
TARGET_VERSION=0.9.0
TARGET_REVISION=<40-hex merged-main revision>
TARGET_SHA256=<64-hex artifact SHA-256>
TARGET_ARTIFACT=home-center-0.9.0-linux-amd64.tar.gz
```

Before production mutation, verify the artifact checksum, VERSION, REVISION, MANIFEST, config schema v4, exact 0.8 predecessor admission and presence of the release-candidate verifier and contracts.

## Preflight

- both nodes prove the exact predecessor above;
- unresolved deployment/recovery transactions are absent;
- local administrator login works independently on both nodes;
- AD authentication remains optional and disabled unless separately accepted;
- Web and peer PKI fingerprints are captured separately;
- Domain SID, DRS replication and protected-service sentinels pass;
- a fresh backup on each node passes checksum, SQLite integrity and audit-chain verification;
- the reverse rollback points are present and bound to the transaction;
- external access remains disabled until the operator gateway is ready.

## Controlled rollout

1. Install only on `dc02` using the packaged bootstrap.
2. Prove exact candidate identity, readiness, peer mTLS, DRS, protected services, backup verification and unchanged Web/peer PKI.
3. Run the bounded canary/soak twice.
4. Only after dc02 PASS, install on `dc01`.
5. Prove the same postconditions and exact version/revision/artifact parity.
6. If external publication is intended, follow `EXTERNAL-ACCESS-0.9.0.md` and run all positive and negative desktop/mobile checks.
7. Produce the closed `home-center.release-candidate-acceptance.v1` evidence document.
8. Verify it with the packaged `release-candidate-verify.py`, passing the exact target and predecessor identities on the command line.

Acceptance evidence records observations only. It does not replace the separately signed stable-channel record and cannot authorize deployment.

## Rollback and restore drill

The release candidate must complete a controlled reverse drill `dc01 → dc02` and prove:

- both nodes returned to the exact 0.8.0 identity;
- the deployment transaction reached a known terminal state;
- each backup can be independently extracted and verified;
- SQLite integrity and HMAC audit chain pass after restore verification;
- Web and peer identities remain unchanged;
- Domain SID, DRS and protected services remain healthy.

Any ambiguous outcome is `recovery_required`; forward progress is forbidden until exact postconditions restore a known state.

## Safety invariants

The upgrade performs no implicit Samba AD, DNS, DHCP, GPO, user, group, router, NAT or DDNS mutation. It does not enable automatic update or automatic failover. Home Center remains independent from Control Center and AI Development Fabric.
