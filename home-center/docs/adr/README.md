# ADR — Home Center

Architecture Decision Record обязателен для решений, которые меняют системные границы, security invariants, data ownership, compatibility policy, deployment/HA semantics или canonical contracts.

## Naming

`ADR-NNNN-short-name.md`

## Минимальная структура ADR

- Status: Proposed / Accepted / Superseded / Rejected;
- Date;
- Related requirements (`HC-*`);
- Context;
- Decision;
- Alternatives considered;
- Security/data impact;
- Compatibility/migration impact;
- Failure/recovery impact;
- Consequences;
- Validation/evidence;
- Supersedes / Superseded by.

## Accepted decisions

1. [ADR-0001 — repository and execution boundary](0001-repository-and-execution-boundary.md);
2. [ADR-0002 — P1 runtime foundation](0002-p1-runtime-foundation.md);
3. [ADR-0003 — two-node single-writer topology](0003-two-node-single-writer.md);
4. [ADR-0004 — typed action registry](0004-typed-action-registry.md);
5. [ADR-0005 — bounded privileged helper](0005-bounded-privileged-helper.md);
6. [ADR-0006 — Web TLS certificate lifecycle and trust model](0006-web-tls-certificate-lifecycle.md);
7. [ADR-0007 — signed stable release channel](0007-signed-stable-release-channel.md);
8. [ADR-0008 — local administrator authentication for Home Center 0.8](0008-local-administrator-authentication.md);
9. [ADR-0009 — authentication-free deployment probes for Home Center 0.8](0009-auth-free-deployment-v2.md);
10. [ADR-0010 — optional bounded Active Directory authentication](0010-optional-ad-authentication.md);
11. [ADR-0013 — 0.10 Core Planning Boundary](0013-core-planning-boundary.md);
12. [ADR-0014 — ModuleManifest v2 and artifact trust boundary](0014-module-manifest-v2-trust-boundary.md).

## Proposed decisions

13. [ADR-0015 — Deterministic module admission planner boundary](0015-module-admission-planner-boundary.md).

## Следующие решения

- node trust/enrollment protocol beyond the accepted two-node bootstrap;
- persistence/database architecture beyond local single-writer state;
- module isolation/privilege model;
- cluster membership/quorum and witness/fencing model;
- protected production signing-key provisioning and P2.5 persisted update reconcile;

README не принимает будущие решения автоматически; он задаёт процесс их фиксации.

ADR-0014 is accepted for the 0.11 development line only. It does not activate module acquisition, extraction, installation, lifecycle execution, production rollout or a privileged Market runtime.

ADR-0015 is proposed for the 0.11 development line only. It adds a side-effect-free local admission decision and no topology, lifecycle, permission-grant or production authority.
