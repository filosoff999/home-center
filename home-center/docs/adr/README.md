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
4. [ADR-0004 — typed action registry](0004-typed-action-registry.md).

## Следующие решения

- node trust/enrollment protocol;
- persistence/database architecture beyond local single-writer state;
- module isolation/privilege model;
- cluster membership/quorum and witness/fencing model;
- update/signing/supply-chain evolution.

README не принимает будущие решения автоматически; он задаёт процесс их фиксации.
