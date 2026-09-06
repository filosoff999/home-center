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

## Первые ADR, требующие отдельного утверждения

1. repository/product naming boundary для Home Center;
2. canonical control-plane architecture и process boundaries;
3. node trust/enrollment protocol;
4. persistence/database architecture;
5. secrets boundary;
6. module isolation/privilege model;
7. cluster membership/quorum model;
8. update/signing/supply-chain model.

README не принимает эти решения автоматически; он задаёт процесс их фиксации.
