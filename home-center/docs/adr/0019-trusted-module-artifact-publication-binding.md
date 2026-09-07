# ADR-0019 — Trusted module artifact publication binding

- **Status:** Accepted
- **Date:** 2026-09-07
- **Related requirements:** HC-MOD-002, HC-MOD-003, HC-CORE-002, HC-TEST-002
- **Tracking:** #102

## Context

Home Center 0.11 can already verify a module manifest, canonical provenance statement, publisher signature threshold, archive structure and exact artifact bytes before atomically staging them under a deterministic content-addressed object key. The lifecycle preview introduced in increment 6 intentionally could not prove that its declared artifacts had crossed that boundary, so `artifact_publication_unverified` remained a mandatory blocker.

The lifecycle request is caller-controlled and must not be allowed to assert its own publication state. Presence of a digest in a manifest is also insufficient: it does not prove that matching bytes were verified, staged and bound to the exact publisher provenance.

## Decision

Home Center 0.11.0 adds immutable artifact-publication evidence. The canonical internal publication operation performs the existing full verification and content-addressed staging first, then records metadata in SQLite. It exposes no HTTP route.

The immutable identity covers:

- module ID, version and publisher;
- normalized-manifest binding digest;
- canonical provenance-statement digest;
- exact artifact digest, byte size and deterministic object key;
- the sorted active signing-key set that satisfied the publisher threshold.

The publication ID is a deterministic SHA-256 digest of that identity. The stored record additionally contains server-controlled publication time and correlation ID. A domain-separated HMAC covers the entire stored record, and readiness verifies every record.

The lifecycle preview still accepts exactly the increment-6 request. The server recomputes the admitted install set, loads publication records by exact module/version/artifact digest and passes only store-verified records into the pure planner. Caller-supplied publication fields are rejected by the closed request contract.

For each install step, the planner binds the publication to the manifest's exact publisher, normalized-manifest digest, provenance-statement digest, artifact size, digest, content address and signer threshold. A malformed, ambiguous or mismatched record fails closed. Missing records remain visible as `artifact_publication_unverified`.

When every install step is bound, only the artifact-publication preflight changes to `pass` and that one blocker is removed. The plan remains `blocked` by authorization, executor and placement.

## Public evidence

The public publication contract exposes a logical `sha256:<digest>` content address, not the internal object key or any host filesystem path. It fixes these fields to false:

- `installation_authority`;
- `lifecycle_execution_enabled`;
- `production_activation_enabled`.

The lifecycle itself continues to fix acknowledgement consumption, authorization decisions, artifact mutation, lifecycle persistence, lifecycle execution and production activation to false.

## Explicit exclusions

This increment provides no:

- artifact acquisition, upload, staging or publication HTTP API;
- acceptance of publication evidence from a lifecycle caller;
- archive extraction or module installation;
- permission acknowledgement consumption or authorization decision;
- topology placement, node orchestration or rollout;
- lifecycle job persistence, executor, service control, privileged helper or command surface;
- release or production deployment.

The independent `main`, `release/0.12.0` and 0.13 development lines are unchanged.

## Alternatives considered

### Trust the manifest digest alone

Rejected because a declaration does not prove that verified bytes exist in the content-addressed store.

### Accept publication records in the lifecycle request

Rejected because callers could forge or replace evidence. Publication lookup is server-side only.

### Check only object-file presence during preview

Rejected because presence alone loses the exact publisher, provenance, manifest and signing-threshold binding. It would also add filesystem I/O to the pure lifecycle planner.

### Enable installation once publication passes

Rejected because authorization, acknowledgement consumption, placement and a certified executor remain separate unresolved gates.

## Security and failure behavior

Publication metadata is written only after exact-byte staging succeeds. Replays are idempotent for the same immutable identity. Conflicting metadata fails closed. Database encoding or HMAC failures make readiness fail and cause lifecycle preview to return an internal error rather than downgrade corrupted evidence to a missing publication.

The planner remains deterministic and performs no filesystem, database, network or process I/O. Its public plan contains no command, argv, executable path, destination path, credentials or artifact bytes.

## Compatibility and migration

SQLite migration 4 adds the append-only publication table and indexes. Existing installations start with an empty publication inventory, so their lifecycle output remains equivalent to increment 6 until a trusted internal publication has completed. Existing manifest, admission, review, acknowledgement and lifecycle request contracts are unchanged. The lifecycle plan adds a nullable publication object per step and permits either three or four blockers.

## Validation and evidence

- verified stage-before-record and idempotent replay tests;
- publication contract and deterministic identity tests;
- record-tamper and readiness failure tests;
- exact full-publication and partial-publication lifecycle tests;
- publisher, manifest, provenance, signer, size and content-address mismatch tests;
- authenticated API tests proving server-side lookup and caller-field rejection;
- safe Web rendering and no-publication-route security checks;
- Python 3.12/3.14, E2E, public export and reproducible artifact CI.

## Consequences

The lifecycle plan can now distinguish an unverified artifact declaration from an exact artifact that was verified and published by the trusted internal pipeline. Passing this gate grants no authority. The next independent lifecycle increment may address topology-aware placement while authorization and execution remain blocked.
