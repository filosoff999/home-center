# ADR-0007: Signed stable release channel

Status: **Accepted for P2.4 verifier implementation**  
Date: **2026-09-06**  
Related: `#17`, HC-REL-001..005, HC-TEST-002..003, ADR-0001, ADR-0003, ADR-0005, ADR-0006

## Context

The accepted `0.4.3` deployment proves exact artifact integrity, canary order and recovery, but its SHA-256 and archive currently arrive through the same audited operator channel. The GitHub Actions candidate expires after fourteen days. A matching archive and caller-supplied checksum therefore prove integrity, not durable release authenticity or current promotion state.

Home Center needs a stable channel in which a later valid signature cannot silently replay a release after it has been quarantined. The channel must remain independent from Control Center credentials and must not turn CI success or a larger version number into deployment authority.

## Decision

### P2.4 boundary

P2.4 produces only a `VerifiedStableRelease` decision. The verifier has no network client, service-control operation, generic command surface or deployment primitive. P2.5 will separately own persisted discovery, fetch, two-node rollout and rollback. An expired or unavailable channel blocks a new mutation but never stops the already-running Home Center release.

### Signed representation

The channel is a DSSE v1 envelope with payload type `application/vnd.home-center.release-ledger.v1+json`. DSSE PAE binds the exact payload type and exact payload bytes. The payload is canonical ASCII JSON: sorted keys, compact separators, no BOM or trailing newline, no floating-point values, no duplicate keys and no unknown fields.

The signing algorithm is fixed by policy as ECDSA P-256 with SHA-256; untrusted metadata cannot select another algorithm. `keyid` is `sha256:` plus the SHA-256 of the DER SubjectPublicKeyInfo. Signatures are strict base64 DER values and only unique active keys count toward the configured threshold.

Private release-signing keys are never placed in this repository, a deployment artifact, GitHub PR job, `dc01`, `dc02`, logs or evidence. They are distinct from Web CA, peer CA, SSH and Control Center credentials. P2.4 supports a public multi-key threshold policy, but initial production key provisioning, fingerprint approval and trust bootstrap require a separate controlled ceremony.

### Immutable release record

Every release record binds:

- product, semantic version, full source revision and `linux/amd64` platform;
- exact archive name, SHA-256, byte count, internal manifest SHA-256, media type and content-addressed object key;
- exact Home Center repository, `main` source ref, workflow path and workflow digest;
- GitHub Actions run, attempt, artifact identity, outer ZIP digest and reproducible-build epoch;
- HM.DM acceptance transaction, evidence-bundle digest, evidence references and mandatory external postconditions.

The signed record contains a restricted relative object key, never a URL. A future fetcher obtains its HTTPS origin only from root-owned local configuration, rejects redirects across origins and writes to a content-addressed spool. CI artifact identity is provenance, not durable storage.

### Append-only ledger and state machine

The signed payload is a complete ledger snapshot. Each event has a monotonic sequence, exact previous-event digest and one or two atomic changes. A release record appears in full only on first listing and is referenced by its canonical SHA-256 afterward.

Allowed transitions are:

- `unlisted → stable`;
- `unlisted → quarantined`;
- `stable → superseded`;
- `stable → quarantined`;
- `superseded → quarantined`.

`quarantined` is terminal. `superseded` cannot become stable again in v1. Replacing an existing stable release is one event with exactly two ordered changes: old `stable → superseded`, then new `unlisted → stable`. At most one stable record may result. Stable versions increase strictly; the same version cannot identify another revision, record or artifact.

Emergency rollback is a P2.5 local transaction to the exact immediately preceding accepted artifact. It is not a channel re-promotion and it is forbidden when that artifact is quarantined or cannot be reverified.

### Freshness and anti-replay

Every snapshot has an increasing generation and a maximum seven-day validity window. A local checkpoint binds the highest accepted generation, payload digest, ledger sequence, event-list digest, last-event digest, stable artifact and verification time.

- lower generation or sequence is rejected;
- the same generation with different payload is equivocation;
- a higher generation must contain the previously accepted event head at the same sequence;
- a freshness-only republish may change generation and timestamps but not ledger state;
- clock rollback, rewritten history, stale metadata and a fork fail closed.

`--bootstrap-no-checkpoint` is an explicit one-time verification mode. Once a checkpoint exists, omitting it is not an accepted production operation. Atomic root-owned checkpoint persistence is part of P2.5, not the read-only P2.4 CLI.

### Artifact verification

The verifier opens the content-addressed object without following symlinks, checks exact bytes and SHA-256, rejects non-regular or multiply linked objects, and then validates the gzip/tar structure. Absolute/traversal/duplicate paths, symlinks, hardlinks, devices, sockets, FIFOs, wrong ownership, excessive members or expansion and incomplete manifests are rejected. Every regular file must be covered by `MANIFEST.sha256`; `VERSION` and `REVISION` must match the signed record.

The returned stable identity contains immutable scalar fields plus the exact signed canonical record bytes. Artifact verification accepts the `VerifiedLedger`, rebinds the record digest and does not accept an arbitrary caller-created record.

## Trust bootstrap and `0.5.0`

Shipping a verifier or public key only inside the target artifact is circular. Therefore the first `0.5.0` rollout remains the existing explicitly audited exact-artifact transaction from accepted `0.4.3`, in order `dc02 → dc01`, with rollback and no-regression gates. This transitional rollout installs an inert verifier; it does not publish a production `stable` record or enable polling.

Production activation remains blocked until all of the following exist:

1. dedicated Home Center signing keys and approved public fingerprints;
2. a reviewed root-owned trust-policy bootstrap on both nodes;
3. a durable Home Center-owned content-addressed release store;
4. immutable acceptance evidence satisfying the signed record;
5. P2.5 root-owned anti-replay checkpoint and two-node update state machine.

GDrive CURRENT and GitHub issues may mirror status and evidence but are never deployment authority.

## Alternatives considered

### Sign only the archive checksum

Rejected. It does not bind provenance, acceptance, current channel state or a later quarantine and remains replayable.

### Treat a successful `main` CI run as stable

Rejected. CI proves tests for a revision, not explicit promotion, durable availability or production acceptance.

### Put a production private key in GitHub Actions or on `dc01`

Rejected. PR/CI compromise or node compromise would become release-promotion authority. Signing is a separate protected Home Center process.

### Let P2.4 deploy automatically

Rejected. Authenticity and state verification are separable from interruption-safe two-node mutation. P2.5 requires its own checkpoints, probation/soak and rollback certification.

## Security, compatibility and recovery impact

P2.4 adds no endpoint and changes no Samba AD/DNS/DHCP, peer PKI, Web PKI, automatic failover or Control Center service. Existing `0.4.3` transaction formats stay readable and unchanged. The verifier is standard-library Python plus fixed `/usr/bin/openssl` argv with a bounded environment and timeout.

Any inability to prove exact DSSE signatures, canonical payload, state history, freshness, accepted release identity or artifact bytes returns a stable non-secret rejection code. No failure path falls back to a newer CI artifact or performs a mutation.

## Validation / evidence

- contract and strict-parser tests for DSSE, record, ledger, trust policy, checkpoint and result;
- signature, key, canonicalization, freshness, replay, equivocation, history-rewrite and illegal-transition negative tests;
- exact artifact byte/manifest/identity verification and archive safety limits;
- immutable returned identity regression test;
- build-twice byte comparison and full-SHA GitHub Action pins;
- existing TLS/helper/backup/rollback/peer-mTLS/DRS gates remain mandatory for deployment.

Supersedes: none.  
Superseded by: none.
