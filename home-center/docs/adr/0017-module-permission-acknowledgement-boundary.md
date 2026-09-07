# ADR-0017 — Expiring module permission acknowledgement boundary

- **Status:** Proposed
- **Date:** 2026-09-07
- **Related requirements:** HC-124
- **Tracking:** #93

## Context

ADR-0016 provides a deterministic read-only permission review but deliberately stores no user decision. A future lifecycle engine needs durable evidence that one authenticated actor saw one exact review, without treating that evidence as permission, authorization or execution authority.

The current 0.11 line still has no Market selection queue, lifecycle state machine, module runtime identity or production activation path. The acknowledgement layer must therefore persist only a short-lived precondition record and expose its limits explicitly.

## Decision

Home Center 0.11.0 adds a closed module-permission acknowledgement request, record, result and list contract.

The create request contains:

- the complete `module-admission-request.v1` document;
- the exact SHA-256 review ID previously shown to the actor;
- the explicit constant intent `permissions-reviewed`;
- a bounded idempotency key.

The server parses the envelope with duplicate-key, float, non-finite, BOM and size rejection. It recomputes admission and the complete permission review from the nested request. Persistence occurs only when the supplied review ID exactly matches the recomputed review.

Every record is bound to the authenticated actor and to a deterministic scope derived from exact requested module IDs and versions. It expires after 15 minutes according to the server clock. A new record from the same actor for the same exact module/version scope supersedes older recorded entries. Other actors and other scopes are unaffected.

Actor plus idempotency key is unique. An identical retry returns the original record; reuse with different request material fails before supersession. Creation and same-scope supersession occur in one `BEGIN IMMEDIATE` SQLite transaction.

The persisted record contains the exact reviewed document, timestamps, correlation ID and a domain-separated HMAC over every stored field. HMAC validation runs on startup, reads and readiness checks. A record with modified content or state is rejected.

Authenticated APIs provide:

- actor-scoped recent-history GET;
- actor-scoped exact-record GET that returns the same 404 for absent, invalid and foreign records;
- same-origin POST for acknowledgement creation.

Successful creation and idempotent replay write secret-free audit evidence before returning a response. If the record commits but audit persistence fails, the request fails; an identical retry safely replays the record and attempts audit again.

The Web UI shows recent acknowledgements for the current actor and labels recorded, expired and superseded states. It renders server data only as text and exposes no acknowledgement creation, approval, grant or installation control until a separately reviewed Market flow can supply the exact admission request.

## Lifecycle handoff boundary

Each acknowledgement exposes `module-acknowledgement:<uuid>` as a stable handoff reference. A recorded, unexpired item reports `precondition-recorded`; expired or superseded items report `blocked`.

The reference is always `consumable=false`. Every record also fixes:

- `authorization_decision_persisted=false`;
- `permission_grants_applied=false`;
- `lifecycle_execution_enabled=false`;
- `production_activation_enabled=false`.

A future lifecycle state machine must introduce a new reviewed authorization and consumption contract, revalidate the exact review and define recovery. Existing acknowledgement records cannot authorize execution by themselves.

## Explicitly excluded authority

This increment provides no:

- approval, deny, grant, revoke or role/policy mutation;
- lifecycle acknowledgement consumption;
- artifact acquisition, extraction or filesystem publication;
- install, update, rollback, disable, remove or health-gated activation;
- privileged-helper, service-control or generic command call;
- node placement, topology orchestration, rollout, release or production deployment.

The parallel `release/0.12.0` line remains independent and unchanged.

## Alternatives considered

### Treat acknowledgement as approval

Rejected because authorization policy, module isolation and lifecycle recovery are not yet defined. Human review evidence is not execution authority.

### Store only the caller-supplied review ID

Rejected because the server must independently recompute and preserve the exact review contents that were acknowledged.

### Use a permanent acknowledgement

Rejected because module inventory, artifacts, policy and actor sessions can change. A short fixed TTL limits stale handoff evidence.

### Allow a lifecycle engine to consume the reference now

Rejected because this would silently introduce installation authority without its state machine, preflight, postconditions or rollback contract.

## Security and data impact

The pure acknowledgement builder performs no I/O or execution. The API uses the existing authenticated session and same-origin POST gate. Inputs and failures are bounded and non-echoing. Stored content contains module metadata and actor identity but no password, credential, artifact bytes, command, path or secret.

The state database adds an acknowledgement table and indexes in migration 3. Records use a domain-separated HMAC keyed by the existing audit-integrity key. The UI refuses history whose authority flags or non-consumable boundary do not match the closed contract.

## Compatibility and migration

Migration 3 is additive. Older binaries ignore the new table. Existing permission-review v1 responses remain non-persisting previews; the new endpoint requires an explicit acknowledgement request and does not reinterpret `decision_persistence_enabled=false` as authorization.

## Failure and recovery

Malformed input, admission/review failure, review mismatch or idempotency conflict creates no new record and performs no supersession. Transaction failure rolls back both insertion and supersession. Expiry is computed fail-closed at response time. HMAC mismatch fails startup/readiness and record reads. No external rollback is required because no module or system state is changed.

## Consequences

Future lifecycle work gains a bounded actor/review reference while remaining unable to consume it. The next lifecycle ADR must define authorization, single-use consumption, topology, staging, health, rollback and audit semantics independently.

## Validation and evidence

- exact review recomputation and mismatch tests;
- duplicate-key, non-finite, BOM, size and intent rejection tests;
- actor-scoped idempotency, conflict and supersession tests;
- server-controlled expiry tests;
- stored-record HMAC tamper and readiness tests;
- authenticated API, cross-origin and non-echoing error tests;
- UI safety and forbidden-authority gates;
- Python 3.12/3.14, E2E, public export and reproducible artifact CI.
