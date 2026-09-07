# ADR-0014 — ModuleManifest v2 and artifact trust boundary

- **Status:** Accepted for development
- **Date:** 2026-09-07
- **Related requirements:** HC-112, HC-114, HC-119, HC-124
- **Tracking:** #75

## Context

The existing `ModuleManifest v1` is a structural placeholder. It leaves nested objects open and does not define the semantic relationship between permissions, actions, persistent data, backup, lifecycle rollback and immutable package provenance. Treating it as installation authority would be unsafe.

## Decision

Home Center 0.11.0 introduces a closed `ModuleManifest v2`, a deterministic semantic validator and an offline package-admission boundary.

The manifest describes requirements and authority requested by a module. It never grants that authority and contains no executable command, arbitrary argument vector, filesystem path, service name or download URL. A future planner must independently compare the declaration with node capabilities, policy and topology. The package-admission boundary may verify caller-supplied bytes and stage the exact archive under a digest-derived object key, but it cannot acquire, extract, install, activate or execute the package.

Security-sensitive invariants include:

- bounded UTF-8 JSON input and duplicate-key rejection;
- closed root and nested objects;
- exact module/version and artifact digest/size identity;
- ordered semantic-version compatibility intervals;
- unique dependencies, conflicts, permissions, actions, storage and health identities;
- every action permission must be declared;
- mutation/destructive action metadata must be idempotent;
- install and upgrade declare distinct rollback actions;
- remove preserves data in v2; wipe requires a future separate destructive contract;
- every backup-required persistent resource is covered by required restore validation;
- provenance threshold cannot exceed the declared unique signer set.

Artifact-admission invariants include:

- canonical DSSE payload and ECDSA P-256/SHA-256 threshold signatures from manifest-declared publisher keys;
- public-key IDs equal the SHA-256 fingerprint of SubjectPublicKeyInfo DER;
- publisher scope and active/retired/revoked key state are enforced fail closed;
- a cycle-free manifest binding hashes canonical JSON after zeroing only `artifact.provenance.statement_sha256`;
- the signed statement binds exact module, normalized manifest, artifact digest/size/media type and immutable build source identity;
- archive members are bounded and traversal, aliases, links, devices, duplicate names and set-id modes are rejected;
- exact bytes are published without overwrite under `sha256/<prefix>/<digest>/artifact.tar.gz` using no-follow directory traversal, exclusive temporary creation, atomic hard-link publication and fsync;
- an existing object is accepted only when its exact digest and byte size match.

## Alternatives considered

### Extend v1 in place

Rejected because published contract identities are immutable and a breaking closure of previously open nested objects requires a new version.

### Put commands and paths in the manifest

Rejected because catalog data must not become a generic privileged execution surface.

### Activate installation together with the contract

Rejected because schema/semantic acceptance must precede package acquisition, cryptographic verification, policy review, sandboxing and lifecycle execution.

### Put the final statement digest and final manifest digest inside each other

Rejected because it creates a cryptographic hash cycle. The versioned manifest-binding algorithm zeroes only the statement digest before canonical hashing; the final manifest still pins the signed statement digest.

## Security and data impact

The increments are production-inert. Validation processes bounded caller-supplied bytes. Staging writes only verified archive bytes beneath an explicit pre-provisioned object-store root; the source path is never caller-controlled and archives are never extracted. The verifier uses a fixed OpenSSL binary and public keys only. It does not access the network, services, private credentials or production nodes.

## Compatibility and migration

`ModuleManifest v1` remains readable only by any explicitly retained v1 consumer. There is no automatic v1-to-v2 promotion. Publishers must create a v2 manifest and satisfy all new declarations. A future migration tool must remain offline and must not infer permissions or lifecycle actions.

## Failure and recovery

Any unknown field, malformed type/value, duplicate key/identity, inconsistent relationship, invalid signature, unsafe archive or object collision fails closed with a bounded reason code. All cryptographic and archive checks complete before staging. Interrupted temporary writes are unpublished; replay is idempotent only for exact existing bytes.

## Consequences

This creates a stable input for dependency planning and a verified immutable artifact store while keeping package acquisition, extraction and execution disabled. The contract is intentionally stricter than the current placeholder and requires explicit publisher signing work.

## Validation and evidence

- schema contract tests;
- positive reference manifest;
- duplicate/unknown/invalid identity negative tests;
- dependency/conflict/permission/action semantic tests;
- backup/lifecycle/provenance negative tests;
- DSSE tamper, threshold, key-policy and normalized-manifest binding tests;
- archive traversal/link/type and object-store collision tests;
- atomic/idempotent staging tests;
- Python 3.12/3.14 and reproducible artifact CI.
