# ADR-0004: Typed Action Registry and read-only execution boundary

**Status:** Accepted  
**Date:** 2026-09-06  
**Related requirements:** `HC-CORE-002`, `HC-SEC-001`, `HC-SEC-002`, `HC-NODE-005`, `HC-OBS-002`, `HC-CONTRACT-001`, `HC-TEST-001`

## Context

P1 intentionally rejects every mutation. P2 needs a controlled transition toward managed operations without adding a generic privileged or shell execution path. The first increment must prove request typing, authorization, idempotency, persistence, postconditions and audit evidence before any bounded-change action is admitted.

## Decision

1. Every executable action is declared in an immutable, versioned registry shipped in the exact release artifact.
2. Registry IDs and executors form a closed set. Startup fails when the registry contains an action without a matching certified executor.
3. Requests use a strict envelope with no additional fields, a local target, a reason and an idempotency key.
4. Idempotency is persisted in SQLite. Repeating identical request material replays the stored result; reusing the key with different material is rejected.
5. Authorization is an explicit atomic permission. P2.1 grants only `service.read` to the bootstrap administrator.
6. The first action, `service.state.read.v1`, can inspect only Home Center-owned allowlisted systemd units.
7. The executor uses an absolute binary path, an argument vector, a sanitized environment, a five-second timeout and bounded typed output. It never invokes a shell.
8. Each action persists preflight, state transitions, step postconditions, a terminal result and evidence linked to the HMAC audit chain.
9. Remote targets, arbitrary service names, foreign actions and mutation-shaped input fail before execution.
10. No Samba AD, DNS, DHCP, package, filesystem or failover mutation is admitted by this ADR.

## Alternatives considered

- Keep every action unavailable: safe, but does not establish the P2 job/idempotency/evidence path.
- Expose a generic command or shell runner: rejected because input validation cannot bound its effective privilege.
- Start with service restart or configuration mutation: rejected until the read-only state machine and recovery evidence are accepted.
- Dispatch directly to the peer node: deferred until authenticated agent routing and target-scoped authorization are independently specified.

## Security and data impact

The registry is part of the trusted computing base and is validated fail-closed. Only a fixed absolute executable and fixed argument shape are used. User input selects one value from a two-item service allowlist. Raw stderr, environment values and subprocess exceptions are not returned or written to evidence. Security-sensitive reads create an HMAC-linked audit event.

The SQLite migration adds a sidecar `action_job_metadata` table for idempotency, request hashes and steps. The P1 `jobs` table remains unchanged, so an emergency binary rollback does not expose new internal columns through the older API. Existing P1 job rows remain readable through compatibility defaults.

## Compatibility and migration impact

Schema migration 2 is forward-only at runtime and remains compatible with the 0.1.0 database. The deployment rollback point preserves the previous release and database backup. Rolling the binary back after migration leaves additive columns that 0.1.0 ignores.

The public API adds endpoints and does not change existing P1 response paths. Action contracts are versioned independently.

## Failure and recovery

Requests rejected by registry, permission, target or input gates create no job and invoke no executor. Identical retries return the stored job. Conflicting retries return a deterministic conflict. Executor timeout or invalid output produces an explicit terminal failed job with `none-read-only` recovery; silent success is not permitted.

## Consequences

- P2 gains a real managed-action path while remaining read-only and fail-closed.
- A bounded-change executor requires a later ADR, dedicated helper boundary and failure-recovery evidence.
- Registry changes are security-critical and must pass contract, negative, idempotency and artifact gates before deployment.

## Validation and evidence

Required gates are registry/schema validation, API authentication, permission denial, input-injection rejection, local-target enforcement, idempotent replay, conflict rejection, timeout persistence, HMAC audit linkage, immutable artifact verification, synthetic execution and canary production acceptance.

## Supersedes / Superseded by

None.
