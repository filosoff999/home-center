# ADR-0013 — ModuleManifest v2 trust boundary

- **Status:** Proposed
- **Date:** 2026-09-07
- **Related requirements:** HC-112, HC-114, HC-119, HC-124
- **Tracking:** #75

## Context

The existing `ModuleManifest v1` is a structural placeholder. It leaves nested objects open and does not define the semantic relationship between permissions, actions, persistent data, backup, lifecycle rollback and immutable package provenance. Treating it as installation authority would be unsafe.

## Decision

Home Center 0.11.0 introduces a closed `ModuleManifest v2` and a separate deterministic semantic validator.

The manifest describes requirements and authority requested by a module. It never grants that authority and contains no executable command, arbitrary argument vector, filesystem path, service name or download URL. A future planner must independently compare the declaration with node capabilities, policy, topology and a cryptographically verified content-addressed artifact.

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

## Alternatives considered

### Extend v1 in place

Rejected because published contract identities are immutable and a breaking closure of previously open nested objects requires a new version.

### Put commands and paths in the manifest

Rejected because catalog data must not become a generic privileged execution surface.

### Activate installation together with the contract

Rejected because schema/semantic acceptance must precede package acquisition, cryptographic verification, policy review, sandboxing and lifecycle execution.

## Security and data impact

The increment is read-only and production-inert. It processes only bounded manifest bytes and returns a non-secret immutable identity. It does not access the network, filesystem, services, credentials or production nodes.

## Compatibility and migration

`ModuleManifest v1` remains readable only by any explicitly retained v1 consumer. There is no automatic v1-to-v2 promotion. Publishers must create a v2 manifest and satisfy all new declarations. A future migration tool must remain offline and must not infer permissions or lifecycle actions.

## Failure and recovery

Any unknown field, malformed type/value, duplicate key/identity, inconsistent relationship or insufficient provenance declaration fails closed with a bounded reason code. Validation has no side effects, so recovery is correction and revalidation of a new immutable manifest.

## Consequences

This creates a stable input for later content-addressed staging and dependency planning while keeping execution disabled. The contract is intentionally stricter than the current placeholder and may require explicit publisher work.

## Validation and evidence

- schema contract tests;
- positive reference manifest;
- duplicate/unknown/invalid identity negative tests;
- dependency/conflict/permission/action semantic tests;
- backup/lifecycle/provenance negative tests;
- Python 3.12/3.14 and reproducible artifact CI.
