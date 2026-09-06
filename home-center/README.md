# Home Center

> **Статус: 0.4.3 SERVER-SIDE PRODUCTION ACCEPTED · MANAGED-CLIENT TRUST PENDING · P2.4 0.5.0 CANDIDATE**

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

Текущий production baseline на `dc01` и `dc02` — exact `0.4.3`, revision `64f798ceae0b669cbac01b452c3cf4fd96070136`, artifact SHA-256 `b2dde6a51ec9450ddd23e802db50be2605c8854e804803ba014422878a02d3d4`, release `/opt/home-center/releases/0.4.3-64f798ceae0b-b2dde6a51ec9`.

Принятый P2.2 добавляет bounded privileged helper с deny-by-construction policy и сохраняет immutable typed Action Registry. Универсальный shell отсутствует; Samba AD/DNS/DHCP не изменяются.

Rollout `dc02 → dc01`, Web TLS rotation, restricted TLS 1.2/1.3, readiness, exact parity, peer mTLS, DRS, Domain SID и protected-service sentinels прошли. Отдельные Web certificates ECDSA P-256/SHA-256 обслуживаются на обеих нодах; peer PKI не изменена. Реальная установка Web CA в managed Windows/Android trust store и browser acceptance остаётся внешним явным gate: Home Center не изменяет AD/GPO неявно.

P2.4 `0.5.0` добавляет offline/read-only DSSE verifier signed stable channel: immutable release record, append-only `stable/superseded/quarantined` ledger, freshness и anti-replay checkpoint, exact provenance/acceptance binding и полную проверку content-addressed artifact. Verifier ничего не скачивает и не устанавливает. Production signing key, public trust bootstrap, durable artifact store и persisted auto-update относятся к отдельным последующим gates; private signing material в repository/artifact отсутствует.

Проверяемые записи:

- [P1 production acceptance](ops/PRODUCTION-ACCEPTANCE-2026-09-06.md);
- [P2.1 production acceptance](ops/P2.1-PRODUCTION-ACCEPTANCE-2026-09-06.md).
- [P2.3 server-side production acceptance](ops/P2.3-PRODUCTION-ACCEPTANCE-2026-09-06.md).
- [upgrade exact 0.3.0/0.4.2 → 0.4.3](docs/UPGRADE-TO-0.4.3.md).
- [upgrade exact 0.4.3 → 0.5.0](docs/UPGRADE-TO-0.5.0.md).
- [ADR-0007: signed stable release channel](docs/adr/0007-signed-stable-release-channel.md).

## Разработка и CI

Engineering compute выполняется только на GitHub-hosted runners. Домашняя AI Development Infrastructure не используется.

Локальная эквивалентная проверка:

```bash
make ci
```
