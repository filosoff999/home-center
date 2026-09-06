# Текущий статус Home Center

**Дата:** 06.09.2026  
**Repository:** `ControlCenterSoft/home-center`  
**Статус:** `PRODUCTION_ACCEPTED / INDEPENDENT_PRODUCT`

## Принятые границы

- самостоятельный продукт;
- отдельные repository, issues, CI/CD, releases, artifacts и runtime;
- отсутствие code/runtime/build/deploy зависимостей от других продуктов;
- GitHub-hosted engineering compute only;
- HM.DM rollout: `dc02 → dc01`;
- automatic failover disabled до witness/fencing certification;
- domain services не изменяются неявно.

## Production

- version: `0.1.0`;
- revision: `0b9c4c1d0c1d0da85461e324ca780b56652634ec`;
- release на `dc01` и `dc02`: `/opt/home-center/releases/0.1.0-0b9c4c1d0c1d-6c4ca0fbe196`;
- GitHub-hosted CI: PASS;
- canary deployment: PASS;
- post-deployment acceptance: PASS;
- rollback points созданы на обоих узлах;
- Samba domain SID сохранён;
- DRS replication: PASS;
- automatic failover: disabled.

Полные проверяемые доказательства: [`ops/PRODUCTION-ACCEPTANCE-2026-09-06.md`](ops/PRODUCTION-ACCEPTANCE-2026-09-06.md).

## Следующий этап

P2 ведётся только в этом репозитории: расширение управляемых capabilities, контрактов, reconciliation и эксплуатационных guardrails без ослабления продуктовой границы.
