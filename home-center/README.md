# Home Center

> **Статус: 0.5.0 PRODUCTION ACCEPTED · MANAGED-CLIENT TRUST PENDING · 0.6.0 RELEASE CANDIDATE**

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

Текущий принятый production baseline на `dc01` и `dc02` — exact `0.5.0`, revision `1d1ff0be759667c40361bbd04b9da273a778b9c8`, artifact SHA-256 `3898daba8711dc1b24266c2677b29fc2302877724f49bf0d5cab9a5e4bcda58a`, release `/opt/home-center/releases/0.5.0-1d1ff0be7596-3898daba8711`.

Rollout `dc02 → dc01`, Web TLS rotation, restricted TLS 1.2/1.3, readiness, exact parity, peer mTLS, DRS, Domain SID и protected-service sentinels прошли. Отдельные Web certificates ECDSA P-256/SHA-256 обслуживаются на обеих нодах; peer PKI не изменена. Реальная установка Web CA в managed Windows/Android trust store и browser acceptance остаётся внешним явным gate: Home Center не изменяет AD/GPO неявно.

P2.4 `0.5.0` добавил offline/read-only DSSE verifier signed stable channel: immutable release record, append-only `stable/superseded/quarantined` ledger, freshness и anti-replay checkpoint, exact provenance/acceptance binding и полную проверку content-addressed artifact. Verifier ничего не скачивает и не устанавливает.

P2.5 / `0.6.0` добавляет installable, но production-inert persisted two-node reconcile core: exact `VerifiedRelease` binding, durable checkpoint, dc02-first state machine, anti-replay/equivocation/clock-rollback gates, single-writer locking, Web/peer PKI continuity checks и bounded status `current/drifted/blocked/quarantined/recovery_required`. `PRODUCTION_ACTIVATION_ENABLED = False`; production signing key, poller/timer и автоматический deploy не входят в этот release candidate.

В Web UI отображаются версия и build/revision установленного immutable artifact; source-tree execution не выдумывает revision. На мобильной версии вкладка **Узлы** закреплена как один узел на строку.

Проверяемые записи:

- [P1 production acceptance](ops/PRODUCTION-ACCEPTANCE-2026-09-06.md);
- [P2.1 production acceptance](ops/P2.1-PRODUCTION-ACCEPTANCE-2026-09-06.md);
- [P2.3 server-side production acceptance](ops/P2.3-PRODUCTION-ACCEPTANCE-2026-09-06.md);
- [upgrade exact 0.3.0/0.4.2 → 0.4.3](docs/UPGRADE-TO-0.4.3.md);
- [upgrade exact 0.4.3 → 0.5.0](docs/UPGRADE-TO-0.5.0.md);
- [upgrade exact 0.5.0 → 0.6.0](docs/UPGRADE-TO-0.6.0.md);
- [ADR-0007: signed stable release channel](docs/adr/0007-signed-stable-release-channel.md).

## Разработка и CI

Engineering compute выполняется только на GitHub-hosted runners. Домашняя AI Development Infrastructure не используется.

Эквивалентная проверка:

```bash
make ci
```

Release PR обязан пройти Python 3.12/3.14 deterministic gates и reproducible build-twice byte comparison до merge.
