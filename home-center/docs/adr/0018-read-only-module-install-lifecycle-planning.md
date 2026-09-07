# ADR-0018 — Read-only module install lifecycle planning boundary

- **Status:** Proposed
- **Date:** 2026-09-07
- **Related requirements:** HC-MOD-002, HC-MOD-003, HC-CORE-002, HC-TEST-002
- **Tracking:** #96

## Context

Home Center 0.11 now validates module manifests and artifacts, computes an install-only admission plan, renders an exact permission review and stores short-lived evidence that an authenticated actor saw that review. None of those layers authorizes or executes module installation.

Before adding any lifecycle executor, the product needs a deterministic representation of preflight, dependency ordering, health postconditions and recovery. The representation must expose every missing authority and infrastructure dependency instead of treating a permission acknowledgement as approval.

The current line has no authorization contract, acknowledgement-consumption rule, topology placement result, durable published-artifact binding or certified module lifecycle executor. A lifecycle plan must therefore remain blocked.

## Decision

Home Center 0.11.0 adds closed module-install lifecycle preview request, blocked-plan and no-pending-status contracts.

The request supports only `operation=install` and contains an actor-owned acknowledgement ID plus the complete module admission request. The server:

1. parses the request with duplicate-key, float, non-finite, BOM and size rejection;
2. loads the acknowledgement only within the authenticated actor scope;
3. verifies its HMAC through the state store and re-renders its server-controlled TTL state;
4. requires an active `recorded` acknowledgement and the exact non-consumable handoff reference;
5. recomputes admission and the complete permission review;
6. binds the exact full admission-request hash, recomputed review and requested-module scope to the acknowledgement;
7. builds a deterministic blocked plan.

Install steps follow the dependency-first admission order. Each step contains only the exact module/version/artifact identity, the typed install action metadata declared by the validated manifest, declared health postconditions and the module backup contract. No command, argv, executable path or destination path is part of the plan.

Recovery steps follow the exact reverse install order and use only each manifest's declared install rollback action. Recovery is marked `planned`, uses `reverse-order-rollback` and fixes `data_policy=preserve`.

## Preflight boundary

Admission, permission review and active acknowledgement checks report `pass`. The plan always reports `blocked` with all of these fail-visible blockers:

- `artifact_publication_unverified`;
- `authorization_contract_unavailable`;
- `lifecycle_executor_unavailable`;
- `placement_unresolved`.

The following capabilities are fixed off:

- `acknowledgement_consumption_enabled=false`;
- `authorization_decisions_enabled=false`;
- `artifact_mutation_enabled=false`;
- `lifecycle_persistence_enabled=false`;
- `lifecycle_execution_enabled=false`;
- `production_activation_enabled=false`.

The acknowledgement reference is evidence input only and remains `consumable=false`.

## API and user interface

An authenticated GET returns the honest no-pending lifecycle status. An authenticated same-origin POST computes the preview. Preview acceptance and rejection produce secret-free audit evidence, but no lifecycle record or system mutation.

The Russian Web UI shows the lifecycle boundary and can safely render blocked preflight, future steps and reverse recovery using text nodes only. It exposes no start, resume, authorize, consume, install or activation control because no Market selection flow can yet supply the complete request.

## Explicitly excluded authority

This increment provides no:

- acknowledgement consumption, approval, deny, grant, revoke or RBAC/policy mutation;
- lifecycle job persistence or transition into queued/running/verifying/recovery states;
- package acquisition, extraction, publication or filesystem mutation;
- privileged-helper, service-control, generic command or process execution;
- topology placement, node orchestration, rollout, release or production deployment;
- update, rollback execution, disable, remove or destructive data workflow.

The parallel `release/0.12.0` line remains independent and unchanged.

## Alternatives considered

### Treat the active acknowledgement as authorization

Rejected because acknowledgement records only prove that one actor saw one exact review. They carry no policy decision and are explicitly non-consumable.

### Persist an executable job immediately

Rejected because artifact publication, topology placement, authorization, retry checkpoints and a certified executor are not defined. Persisting an apparently runnable job would create misleading authority.

### Hide unavailable preflight checks

Rejected because omitted gates could be mistaken for passed gates. Missing dependencies are explicit blockers in the canonical response.

### Accept arbitrary lifecycle commands from manifests

Rejected. Plans reference only validated symbolic action IDs and bounded metadata. Command, argv and path surfaces remain forbidden.

## Security and data impact

The planner is pure and performs no filesystem, database, network or process I/O. It accepts only a store-verified actor-scoped acknowledgement and recomputes all caller-supplied admission/review material. The HMAC-protected stored request hash binds lifecycle, health, backup, action metadata and list ordering that are intentionally outside the permission-review identity. Errors are bounded and never echo module input.

The API writes only the existing tamper-evident audit evidence. It does not store the lifecycle request or plan. The response contains module identities, artifact digests, symbolic action metadata, health and backup declarations, but no actor identity, secret, credential, artifact bytes, command or host path.

## Compatibility and migration

This increment adds new v1 contracts and routes without changing published manifest, admission, permission-review or acknowledgement shapes. No database migration is required because lifecycle persistence remains disabled.

## Failure and recovery

Malformed, foreign, expired, superseded or mismatched inputs fail before a plan is returned. An integrity failure remains a server error and readiness failure rather than a generic validation result. Since preview performs no module mutation, operational rollback is not required.

The recovery section is a deterministic future execution blueprint, not a runnable or persisted rollback operation.

## Consequences

Future increments can bind durable artifact publication, topology placement and a separately reviewed authorization/consumption contract to this exact plan identity. Only after those gates exist may a persisted lifecycle job and certified executor introduce controlled state transitions, checkpoints, health verification and recovery execution.

## Validation and evidence

- exact actor, acknowledgement state, TTL, review and scope binding tests;
- dependency-first install and exact reverse recovery tests;
- action/health/backup manifest binding tests;
- malformed, ambiguous, mismatched and non-install request tests;
- no-command/path/persistence/execution security gates;
- authenticated API, same-origin and non-echoing failure tests;
- safe Web rendering and forbidden-control regression tests;
- Python 3.12/3.14, E2E, public export and reproducible artifact CI.
