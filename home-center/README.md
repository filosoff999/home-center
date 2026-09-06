# Home Center

> **Статус: PRODUCTION_ACCEPTED / P2.1 / INDEPENDENT PRODUCT**

Home Center — самостоятельный local-first продукт для управления домашней и малой серверной инфраструктурой через единый Web UI и API.

## Жёсткая продуктовая граница

Home Center является полностью независимым продуктом:

- отдельный репозиторий, исходный код и документация;
- отдельные issues, roadmap, CI/CD, релизы и артефакты;
- отдельные deployment profiles, secrets и runtime;
- отсутствие code/runtime/build/deploy зависимостей от других продуктов;
- независимое версионирование и брендинг.

Одноразовый перенос ранее реализованного Home Center в этот репозиторий не создаёт постоянной зависимости от источника миграции.

## Целевая инфраструктура HM.DM

- `dc01.hm.dm` — `192.168.10.254`, основной Home Center control-plane;
- `dc02.hm.dm` — `192.168.10.253`, второй управляемый узел;
- развёртывание: canary `dc02 → dc01`;
- автоматический failover запрещён до независимого witness/fencing proof;
- обновление Home Center не выполняет неявных Samba AD, DNS или DHCP mutations.

## Production

Версия `0.2.0`, revision `853215871f0840b8cc05fc900d572bef87a7dc58`, развёрнута и принята на `dc01` и `dc02`.

P2.1 добавляет immutable typed Action Registry и единственное разрешённое действие `service.state.read.v1`: локальное, read-only, с permission/risk metadata, строгим allowlist, persisted idempotency и audit evidence. Универсальный shell и mutation actions отсутствуют.

Проверяемые записи:

- [P1 production acceptance](ops/PRODUCTION-ACCEPTANCE-2026-09-06.md);
- [P2.1 production acceptance](ops/P2.1-PRODUCTION-ACCEPTANCE-2026-09-06.md).

## Разработка и CI

Engineering compute выполняется только на GitHub-hosted runners. Домашняя AI Development Infrastructure не используется.

Локальная эквивалентная проверка:

```bash
make ci
```
