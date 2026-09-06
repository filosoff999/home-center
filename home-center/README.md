# Home Center

> **Статус: RUNTIME 0.4.2 QUARANTINED / P2.2 ACCEPTED · P2.3 0.4.3 RELEASE CANDIDATE**

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

Принятый baseline — версия `0.3.0`, revision `6b0c0db144bfd2a7b7a7db1a868d649f20825721`.

Принятый P2.2 добавляет bounded privileged helper с deny-by-construction policy и сохраняет immutable typed Action Registry. Универсальный shell отсутствует; Samba AD/DNS/DHCP не изменяются.

Software `0.4.2` (`9f376e3d39eb29b2c8e402d085cba8b9fee4258d`) развёрнуто на `dc01` и `dc02`, но quarantined после безопасного preflight-отказа первой Web TLS rotation: helper создавал release marker с ожидаемой primary group `home-center`, тогда как validator ошибочно требовал gid `0`. Web listener остался на legacy identity, peer mTLS и domain services не изменились.

P2.3 продолжается как immutable `0.4.3`: отдельный ECDSA P-256/SHA-256 Web CA и Web leaf устраняют TLS alert 40 на Android/Chrome, а hotfix согласует root-only marker `0600` с capability-free helper `root:home-center`. Артефакты `0.4.0`, `0.4.1` и `0.4.2` заблокированы для новых rollout. До exact-head CI, canary `dc02`, promotion `dc01`, Web rotation и browser/DRS acceptance версия `0.4.3` не считается production-accepted.

Проверяемые записи:

- [P1 production acceptance](ops/PRODUCTION-ACCEPTANCE-2026-09-06.md);
- [P2.1 production acceptance](ops/P2.1-PRODUCTION-ACCEPTANCE-2026-09-06.md).
- [upgrade exact 0.3.0/0.4.2 → 0.4.3](docs/UPGRADE-TO-0.4.3.md).

## Разработка и CI

Engineering compute выполняется только на GitHub-hosted runners. Домашняя AI Development Infrastructure не используется.

Локальная эквивалентная проверка:

```bash
make ci
```
