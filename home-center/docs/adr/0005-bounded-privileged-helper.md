# ADR-0005: Bounded privileged helper and policy gate

Status: **Accepted for P2.2 implementation**

## Context

Home Center needs a future path for narrowly scoped privileged host changes, while the existing Action Registry is deliberately read-only. The control plane must never become a generic root-command endpoint. Home Center and Control Center remain separate products; engineering compute remains GitHub-hosted only; `dc01`/`dc02` are deployment and production-acceptance targets only.

## Decision

P2.2 introduces a separate local privileged process, `home-center-helper.service`, with these invariants:

- root helper is reachable only through a local Unix-domain socket;
- caller identity comes from Linux `SO_PEERCRED`, never from request data;
- authorization is deny-by-default and evaluated before execution;
- policy can only map callers to compile-time permissions and enable compile-time actions;
- policy cannot define executable paths, argv, environment variables, service names, filesystem paths, shell fragments or remote targets;
- the helper never invokes a shell and launches only an exact absolute executable with exact compile-time argv;
- request IDs and nonces are validated; exact replay returns the original terminal result; conflicting reuse is rejected deterministically;
- the root-controlled policy is hashed into result evidence;
- state is atomically persisted before execution and after completion;
- if the helper restarts with an in-flight request, that request becomes `unknown` and requires operator recovery; it is never converted to silent success;
- stdout/stderr, request size, execution time and environment are bounded;
- result evidence is chained by SHA-256;
- systemd restricts the helper to AF_UNIX, removes its capability bounding set, denies IP networking and applies filesystem/kernel hardening.

## P2.2 action admission

P2.2 admits **only** `helper.probe.v1`, implemented as exact `/usr/bin/true` with no arguments and permission `helper.probe`. It is a synthetic proof of the privilege boundary and performs no product, domain, DNS, DHCP, storage or service mutation.

No production mutation action is admitted by this ADR. Every future mutation requires a separate change with typed parameters, explicit permission, rollback/recovery semantics, negative tests, immutable GitHub-hosted artifact evidence and `dc02 → dc01` canary acceptance.

## Failure model

- malformed/oversized frames: fail closed before action lookup;
- unknown action/parameter shape: fail closed before subprocess creation;
- unknown caller/permission/action disabled: terminal `rejected` result;
- executable timeout/non-zero/exec failure: terminal `failed` result;
- process interruption after durable in-flight state: terminal `unknown` on recovery;
- duplicate exact request: same stored result, without re-execution;
- duplicate request id with different canonical request digest: deterministic conflict rejection.

## Consequences

The root boundary is explicit and independently testable without granting Home Center generic command execution. P2.1 read-only behavior remains unchanged. Automatic failover remains disabled until separate witness/fencing certification. Samba AD, DNS and DHCP are outside this helper's P2.2 action set.
