# Home Center

> **Статус: PRODUCTION 0.3.0 / P2.2 ACCEPTED · P2.3 0.4.1 RELEASE CANDIDATE**

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

Версия `0.3.0`, revision `6b0c0db144bfd2a7b7a7db1a868d649f20825721`, развёрнута и принята на `dc01` и `dc02`.

Принятый P2.2 добавляет bounded privileged helper с deny-by-construction policy и сохраняет immutable typed Action Registry. Универсальный shell отсутствует; Samba AD/DNS/DHCP не изменяются.

P2.3 готовится как immutable `0.4.1`: отдельный ECDSA P-256/SHA-256 Web CA и Web leaf устраняют TLS alert 40 на Android/Chrome, а существующая Ed25519 peer-mTLS PKI остаётся неизменной. Артефакт `0.4.0` заблокирован и не подлежит развёртыванию, поскольку сохранял несовместимую Ed25519 Web identity. До CI, canary `dc02`, promotion `dc01` и browser/DRS acceptance версия `0.4.1` не считается production-accepted.

Проверяемые записи:

- [P1 production acceptance](ops/PRODUCTION-ACCEPTANCE-2026-09-06.md);
- [P2.1 production acceptance](ops/P2.1-PRODUCTION-ACCEPTANCE-2026-09-06.md).
- [upgrade 0.3.0 → 0.4.1](docs/UPGRADE-0.3.0-TO-0.4.1.md).

## Разработка и CI

Engineering compute выполняется только на GitHub-hosted runners. Домашняя AI Development Infrastructure не используется.

Локальная эквивалентная проверка:

```bash
make ci
```
