# Home Center

> **Статус: PRODUCTION_ACCEPTED / INDEPENDENT PRODUCT**

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

Версия `0.1.0`, revision `0b9c4c1d0c1d0da85461e324ca780b56652634ec`, развёрнуты и приняты на `dc01` и `dc02`.

Проверяемая запись CI, artifact, rollout, rollback points и post-deployment checks: [production acceptance 2026-09-06](ops/PRODUCTION-ACCEPTANCE-2026-09-06.md).

## Разработка и CI

Engineering compute выполняется только на GitHub-hosted runners. Домашняя AI Development Infrastructure не используется.

Локальная эквивалентная проверка:

```bash
make ci
```
