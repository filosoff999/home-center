# ADR-0015 — Deterministic module admission planner boundary

- **Status:** Accepted for 0.11 development
- **Date:** 2026-09-07
- **Related requirements:** HC-124
- **Tracking:** #85

## Context

ModuleManifest v2 and offline artifact admission establish trusted declarations and immutable package bytes, but they do not decide whether a set of modules can be admitted together on a local Home Center environment. Dependencies, semantic-version intervals, conflicts, platform compatibility and required capabilities need one deterministic decision boundary before any future permissions review or lifecycle work.

The decision must not silently expand into installation or multi-node orchestration. Home Center 0.12 development is proceeding independently and cannot become a dependency of the 0.11 planner.

## Decision

Home Center 0.11.0 adds a pure, install-only module admission planner with closed request and result contracts.

The request contains:

- an exact Home Center version, operating system, architecture and bounded local capability snapshot;
- exact installed module versions and their declared module conflicts;
- bounded validated ModuleManifest v2 candidates;
- exact requested module IDs and versions.

The planner validates every candidate manifest again and then computes a deterministic dependency-first order. Required dependencies must have exactly one compatible candidate unless an already installed exact version satisfies the interval. An optional dependency is included only when it is separately requested or already installed; mere presence in the candidate inventory never triggers an implicit install. A selected or installed optional dependency that is incompatible fails closed. Multiple compatible versions are ambiguous and rejected rather than resolved by an implicit preference.

Every selected candidate must support the exact Home Center version, operating system and architecture, and all of its declared capabilities must exist in the supplied local snapshot. Dependency cycles, incompatible shared constraints and conflicts declared by either a selected candidate or an installed module are rejected.

An installed module can satisfy a dependency but is never selected for change. Requesting an already installed module is rejected because update semantics belong to a future separately reviewed lifecycle state machine.

The admitted result contains only exact requested identities, dependency-first artifact identities, installed dependencies used, required capabilities, requested permissions and a constant `production_activation_enabled=false`. Requested permissions are review input; they are not grants.

## Explicitly excluded authority

The planner performs no:

- network acquisition or repository lookup;
- filesystem read/write or artifact extraction;
- install, update, rollback, disable, remove or lifecycle action;
- permission grant or privileged-helper call;
- node placement, topology selection, multi-node orchestration, rollout or health/quorum coordination;
- production deployment or mutation.

The 0.12 local administrator password-management requirement remains a separate release track and is neither imported into nor blocked by this decision.

## Alternatives considered

### Select the highest compatible dependency automatically

Rejected because candidate preference and publisher/channel policy are not yet defined. Ambiguity must be visible to a future policy layer.

### Upgrade an installed dependency when its version is incompatible

Rejected because admission planning must not infer update authority or rollback behavior.

### Include topology placement in the same planner

Rejected because local compatibility and graph admission can be proven independently. Placement and multi-node coordination require separate topology, failure and recovery decisions.

### Trust request manifests without revalidation

Rejected because callers must not bypass the closed ModuleManifest v2 semantic boundary.

## Security and data impact

The implementation accepts bounded in-memory JSON data and returns immutable dataclasses. It imports no filesystem, process, network or service-control module. Errors expose fixed reason codes and never echo untrusted manifest values. The result contains no command, path, credential, node target or granted authority.

## Compatibility and migration

The request and result use new v1 contract identities. No existing runtime contract changes. ModuleManifest v2 remains the only accepted candidate manifest. Existing installed modules are represented by exact ID/version/conflict state and are read-only inputs.

## Failure and recovery

Any malformed request, invalid manifest, missing or incompatible dependency, ambiguous candidate choice, cycle, platform/capability mismatch or conflict produces no partial plan. The planner has no side effects, so failure requires no rollback.

## Consequences

Permissions review and lifecycle implementation can consume a stable, deterministic admission result without reimplementing dependency graph logic. A future policy layer must explicitly resolve version/channel preference before submitting candidates when more than one version would satisfy a dependency.

## Validation and evidence

- deterministic multi-level DAG and installed-dependency tests;
- required/optional dependency and version-bound tests;
- ambiguity, shared-constraint and cycle rejection tests;
- Home Center, OS, architecture and capability compatibility tests;
- candidate/installed conflict tests in both declaration directions;
- closed-input and bounded JSON tests;
- static security gate for I/O, execution, topology and activation authority;
- Python 3.12/3.14 and reproducible artifact CI.
