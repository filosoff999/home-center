# Operations / Runbooks

`ops/` хранит будущие эксплуатационные runbooks и acceptance procedures Home Center.

## Обязательные категории

- installation/bootstrap;
- node enrollment;
- node maintenance/drain/remove;
- upgrade/rollback;
- backup/restore;
- cluster failover/failback;
- loss of node;
- loss of Control Plane;
- networking/DNS/service recovery;
- secrets/certificate rotation;
- support bundle/evidence collection.

## Runbook standard

Каждый runbook должен содержать:

1. scope и prerequisites;
2. risk/impact;
3. preflight;
4. deterministic steps или ссылку на typed product workflow;
5. expected evidence;
6. stop conditions;
7. recovery/rollback;
8. post-validation;
9. audit references.

## Automation-first rule

Повторяемые production операции должны переходить в typed workflows продукта. Runbook не должен становиться постоянной заменой отсутствующей product automation.

## Current acceptance records

- `PRODUCTION-ACCEPTANCE-2026-09-06.md` — P1 baseline;
- `P2.1-PRODUCTION-ACCEPTANCE-2026-09-06.md` — typed Action Registry;
- `P2.3-PRODUCTION-ACCEPTANCE-2026-09-06.md` — exact `0.4.3` server-side/Web TLS acceptance with managed-client trust explicitly pending.

Mutable GitHub/GDrive status may point to these records, but the future signed release channel uses only its canonical DSSE ledger and root-owned anti-replay state as deployment authority.
