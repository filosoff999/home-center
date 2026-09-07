# ADR-0016 — Read-only module permission review boundary

- **Status:** Accepted for 0.11 development
- **Date:** 2026-09-07
- **Related requirements:** HC-124
- **Tracking:** #88

## Context

ADR-0015 produces a deterministic admitted module plan and aggregates requested permissions, but an administrator still needs a clear explanation of which selected module and typed action requests each permission. Presenting those declarations must precede any future decision persistence, permission grant or lifecycle execution.

The current 0.11 development line has no Market selection queue or module lifecycle state machine. A review surface must therefore represent the empty state honestly and support a side-effect-free preview without inventing a pending installation.

## Decision

Home Center 0.11.0 adds closed `module-permission-review.v1` and `module-permission-review-status.v1` contracts, a pure review builder, an authenticated preview API and a Russian Web UI module-permissions view.

The preview accepts the complete `module-admission-request.v1` document and recomputes admission. It does not trust a caller-supplied plan or permission summary. Every selected candidate is bound by exact module ID and version to the dependency-first admitted plan.

For each requested permission, the review records:

- every selected module that declares it;
- every typed action that references it;
- the highest declared action risk: `read-only`, `mutation` or `destructive`;
- `unclassified` when any declaring module has no typed action for that permission.

`unclassified` is fail-visible and is never downgraded to read-only. The aggregate admitted and reviewed permission set is bounded to the published 128-item admission-result limit, and the review permits at most 2048 typed-action references. Excess complexity fails closed before a response is produced.

The review ID is the SHA-256 digest of canonical review contents excluding the ID itself. It detects changed review contents but is not a signature, authorization token, approval or release identity.

`GET /api/v1/modules/permission-review` returns an authenticated closed `no-pending-review` status until a separately reviewed Market selection state exists. `POST /api/v1/modules/permission-review/preview` requires an authenticated same-origin request and returns only the deterministic preview. It stores no state.

The Web UI:

- adds a `Модули` navigation view;
- renders no-pending, unavailable, rejected and review-required states;
- uses DOM `textContent` only and never injects returned HTML;
- refuses to render a review unless decision persistence, grants and production activation are all explicitly false;
- preserves the one-node-per-row mobile Nodes layout and makes the expanded mobile navigation horizontally scrollable.

## Explicitly excluded authority

This increment provides no:

- approve, grant, install or activation endpoint;
- persisted acknowledgement or decision;
- permission grant, credential or secret handling;
- artifact acquisition, extraction or filesystem mutation;
- install, update, rollback, disable, remove or lifecycle execution;
- privileged-helper, service-control or generic command call;
- node placement, topology orchestration, rollout or production deployment.

The parallel `release/0.12.0` line and its local administrator password-management requirement remain independent and unchanged.

## Alternatives considered

### Add an Approve button now

Rejected because no persisted decision contract, authorization policy or lifecycle consumer exists. A button with no complete state machine would create misleading authority.

### Classify unreferenced permissions from their names

Rejected because namespace text is not an enforceable risk declaration. Missing typed-action attribution is displayed as unclassified.

### Accept a caller-provided admitted result

Rejected because a caller could omit dependencies or permissions. Preview recomputes admission from the complete bounded request.

### Persist the last preview for the UI

Rejected because preview identity, actor binding, expiry, concurrency and recovery require a separate decision-state contract.

## Security and data impact

The review builder imports no filesystem, process, network or service-control module. The POST body is bounded, read under the existing timeout and parsed with duplicate-key, float, non-finite and BOM rejection inherited from the admission boundary. Errors are fixed codes and do not echo module input. The browser renders server values only through text nodes.

## Compatibility and migration

The increment adds new v1 contracts and endpoints without changing existing response shapes. The empty GET status remains valid until a future persisted review contract is admitted. A future decision workflow must use a new explicit contract and cannot reinterpret `review_id` as authorization.

## Failure and recovery

Malformed input, failed admission, candidate/permission binding mismatch or excessive review complexity returns no partial review and creates no job or stored decision. UI contract or safety-flag mismatch renders a rejected state. Since no state is written, failure requires no rollback.

## Consequences

Administrators gain a stable, safe explanation layer for module authority requests. The next lifecycle increment can refer to an exact reviewed document but must separately define actor binding, acknowledgement persistence, expiry, idempotency and recovery.

## Validation and evidence

- deterministic review-identity tests;
- permission/module/action attribution and highest-risk tests;
- unclassified and shared-permission fail-visible tests;
- installed-dependency omission and complexity-bound tests;
- authenticated API and generic-error tests;
- DOM-injection and forbidden-authority static gates;
- mobile layout regression checks;
- Python 3.12/3.14, E2E, public export and reproducible artifact CI.
