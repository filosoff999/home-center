# Текущий статус Home Center

**Дата:** 06.09.2026  
**Repository:** `ControlCenterSoft/home-center`  
**Статус:** `PRODUCTION_ACCEPTED / P2.1 / INDEPENDENT_PRODUCT`

## Принятые границы

- самостоятельный продукт;
- отдельные repository, issues, CI/CD, releases, artifacts, secrets и runtime;
- отсутствие code/runtime/build/deploy зависимостей от других продуктов;
- GitHub-hosted engineering compute only;
- HM.DM rollout: `dc02 → dc01`;
- automatic failover disabled до witness/fencing certification;
- domain services не изменяются неявно.

## Production

- version: `0.2.0`;
- revision: `853215871f0840b8cc05fc900d572bef87a7dc58`;
- release на `dc01` и `dc02`: `/opt/home-center/releases/0.2.0-853215871f08-a356c0784aee`;
- immutable typed Action Registry: accepted;
- единственное executable action: `service.state.read.v1`, local/read-only/allowlisted;
- persisted idempotent replay, включая replay после рестарта `dc02`: PASS;
- injection, remote target, unknown action и conflicting idempotency: fail-closed;
- GitHub-hosted CI: PASS;
- canary deployment: PASS;
- post-deployment acceptance: PASS;
- rollback points созданы на обоих узлах;
- backup после action: PASS;
- Samba domain SID сохранён;
- DRS replication: PASS;
- Samba AD/DNS/DHCP mutations: none;
- automatic failover: disabled.

Проверяемые доказательства:

- [`ops/PRODUCTION-ACCEPTANCE-2026-09-06.md`](ops/PRODUCTION-ACCEPTANCE-2026-09-06.md);
- [`ops/P2.1-PRODUCTION-ACCEPTANCE-2026-09-06.md`](ops/P2.1-PRODUCTION-ACCEPTANCE-2026-09-06.md).

## Следующий этап

P2.2: отдельный bounded privileged helper, полный policy scope и failure/recovery доказательства. Ни одна mutation action не допускается в registry до отдельного ADR, тестов и canary acceptance.
