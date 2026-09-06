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
