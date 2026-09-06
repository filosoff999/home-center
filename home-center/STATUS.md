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

## Roadmap

1. **P2.2 — bounded privileged helper и policy gate** ([#9](https://github.com/ControlCenterSoft/home-center/issues/9)).  
   Отдельный минимальный privileged execution boundary, deny-by-default policy scope и failure/recovery доказательства. Ни одна mutation action не допускается в registry до отдельного ADR, тестов и canary acceptance.

2. **P2.3 — HTTPS/TLS certificate lifecycle и доверенный Web UI** ([#10](https://github.com/ControlCenterSoft/home-center/issues/10)).  
   Исправление текущих проблем HTTPS-сертификата Web UI и перевод управления сертификатами в штатную функцию Home Center: корректные SAN/hostname/chain checks, доверие доменных клиентов, issuance/import, renewal/rotation, expiry monitoring, `dc02 → dc01` rollout, atomic rollback, certificate health в Web UI и отсутствие регрессии peer mTLS. Production mutation path зависит от acceptance P2.2.

3. **После P2.3 — bounded infrastructure mutations и cluster/node management.**  
   Расширение Action Registry только сертифицированными типизированными действиями с отдельными admission gates, rollback и production evidence.

Automatic failover остаётся запрещён до отдельной witness/fencing certification.
