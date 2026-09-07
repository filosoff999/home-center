# ADR-0013: 0.10 Core Planning Boundary

- Status: Accepted for development
- Date: 2026-09-07
- Release: 0.10.0

## Context

Home Center needs a stable Core seam before it can safely expose broader node,
upgrade, configuration, service, and policy capabilities. Connecting new
names directly to existing privileged helpers would make the module boundary
cosmetic and could accidentally create production authority.

The published immediate predecessor is exact `0.9.2`. Production remains on
its separately accepted baseline; a development branch, PR, merge, or artifact
does not authorize a production rollout.

## Decision

The first 0.10.0 Core increment is a side-effect-free planning layer.

The Core package contains five bounded modules:

- Node Manager;
- Upgrade Engine;
- Configuration Engine;
- Service Manager;
- Policy Engine.

Callers cross the boundary through closed v1 command/result/error contracts.
Commands are limited to `mode=plan`. The policy engine is default-deny and
uses exact module/action/mode rules. Upgrade plans bind exact version,
40-character source revision, and 64-character artifact SHA-256 identities.
Each admitted action has a separate closed input schema and each planner
returns a closed module-specific output schema. Dispatch uses the exact
module/action pair; unknown actions and fields fail closed.

No Core module may invoke a shell or subprocess, download content, control a
service, write production state, or activate a deployment. Production
mutation and activation constants are false and checked by CI.

## Consequences

- Architecture and contracts can evolve under versioned tests without
  exposing a second privileged path.
- Planning output is deterministic and suitable for later job/audit
  integration.
- The initial increment is deliberately not a user-visible execution feature.
- Persistence, HTTP routing, reconciliation, privileged adapters, automatic
  updates, and automatic HA require separate ADRs and safety evidence.
- Existing 0.9.x gates and the `dc02 → canary/soak → dc01` acceptance order
  remain mandatory.

## Rejected alternatives

- Reusing generic shell or argv actions: this expands caller-controlled
  authority and bypasses typed-action safety.
- Enabling execution behind an undocumented flag: build-time or ambient
  activation is not an acceptable release gate.
- Coupling 0.10.0 development to a production rollout: development identity
  and operational acceptance are independent states.
