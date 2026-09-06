# Home Center

> **Статус: 0.5.0 PRODUCTION ACCEPTED · 0.9.0 RELEASED / PRODUCTION ACCEPTANCE PENDING**

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
- rollout: `dc02 → canary/soak → dc01`;
- automatic failover запрещён до независимого witness/fencing proof;
- Home Center не выполняет неявных Samba AD, DNS или DHCP mutations.

## Production

Текущий принятый production baseline на `dc01` и `dc02` — exact `0.5.0`, revision `1d1ff0be759667c40361bbd04b9da273a778b9c8`, artifact SHA-256 `3898daba8711dc1b24266c2677b29fc2302877724f49bf0d5cab9a5e4bcda58a`, release `/opt/home-center/releases/0.5.0-1d1ff0be7596-3898daba8711`.

Server-side Web TLS ECDSA P-256/SHA-256, readiness, exact parity, peer mTLS, DRS, Domain SID и protected-service sentinels были приняты. Managed Windows/Android Web CA enrollment и browser acceptance остаются отдельным явным gate; Home Center не меняет AD/GPO неявно.

## Release candidates

### 0.6.0

Exact merged-main candidate:

- revision `2235670d77bccc1c777223eb4f50a546313b4d2d`;
- artifact SHA-256 `bf68e870339f18351f4401895f7bff18c093fa9809eb4eefda2633391fe9a811`;
- artifact `home-center-0.6.0-linux-amd64.tar.gz`;
- GitHub-hosted exact-main CI PASS.

`0.6.0` добавил installable, но production-inert persisted two-node reconcile core: exact `VerifiedRelease` binding, durable checkpoint, dc02-first state machine, anti-replay/equivocation/clock-rollback gates, single-writer locking, Web/peer PKI continuity checks и bounded state. `PRODUCTION_ACTIVATION_ENABLED = False`.

В Web UI отображаются версия и build/revision immutable artifact. На мобильной вкладке **Узлы** закреплён один узел на строку.

### 0.7.0

`0.7.0` добавляет production-inert offline Release Manager foundation:

- fixed root-owned public trust policy и signed DSSE snapshot;
- durable anti-replay checkpoint;
- fixed-inbox content-addressed artifact store;
- no caller-controlled source/destination path;
- no ambient network/download authority;
- full P2.4 artifact/manifest/version/revision verification;
- atomic single-writer checkpoint advancement;
- fail-closed staged release policy: target exact 0.7.0, predecessor exact published 0.6.0 identity.

`PRODUCTION_RELEASE_MANAGER_ENABLED = False`; production private signing key, automatic poller/timer и automatic installation не активированы.

### 0.8.0 released

Published [Home Center v0.8.0](https://github.com/ControlCenterSoft/home-center/releases/tag/v0.8.0): revision `bbb2b1e952b2072c8ce30ad6b3220c7c14280949`, artifact SHA-256 `25fb72fdffab972703d9be2b7e457b1f4ed053bc1c013a6237558fd693e73ca8`.

0.8.0 включает:

- локального администратора Home Center вместо bootstrap-token в интерактивном Web login;
- root-only атомарное создание scrypt verifier без plaintext/reversible password storage;
- Secure, HttpOnly, SameSite=Strict короткие сессии и fail-closed same-origin/Fetch Metadata защита POST-запросов;
- token-free installer/bootstrap/recovery probes с сохранением `dc02 → canary/soak → dc01`, durable rollback и peer mTLS gates;
- exact 0.8 artifact policy с predecessor `0.7.0 / f28fc1c820b065616758ca3c220794555c30a25a / 6ab15ef38b5d4b064ddb45c47009c73de3f5425553a059d75c9fb3c77bc82b82`;
- опциональный AD provider, отключённый по умолчанию: фиксированные Kerberos/NSS executables, явные KDC и разрешённые administrator groups, bounded timeout и удаляемый per-attempt credential cache;
- публичный secret-free provider catalog: Web UI показывает AD-вход только при явном включении provider;
- локальный вход остаётся доступным независимо от состояния AD.

Release PR, exact-main CI `34064080930` и pinned publication run `34064461198` прошли deterministic Python 3.12/3.14 и reproducible build-twice. В 0.8 не активируются production deployment, AD/GPO mutation, automatic update или automatic failover. Публикация релиза не означает его deployment на dc01/dc02; live HM.DM rollout и optional AD validation отслеживаются в issue #44.

### 0.9.0 released

Published [Home Center v0.9.0](https://github.com/ControlCenterSoft/home-center/releases/tag/v0.9.0): revision `29b2f61071067028c14febbbaf0103c5600380e9`, artifact SHA-256 `66531867f806c6665f41d2bb82dccfb5670403acd0c9988271714da09172f668`.

0.9.0 добавляет:

- отключённую по умолчанию external publication boundary через exact trusted reverse proxy;
- fail-closed forwarding/Origin/HTTPS/public-host validation, client+proxy rate limits и скрытие внутренних endpoints;
- минимальный внешний health и authenticated non-secret status;
- exact upgrade policy только с опубликованной 0.8.0 identity;
- deterministic build-twice/two-node/runtime/external-login/backup E2E;
- closed acceptance evidence для `dc02 → dc01`, reverse rollback `dc01 → dc02`, backup/restore, exact parity, PKI/Domain SID/DRS и zero forbidden mutations.

Exact-main Python 3.12/3.14, отдельный E2E, build-twice и pinned publisher прошли. Gateway, DNS, router/NAT и certificates остаются operator-owned. Acceptance evidence не заменяет threshold-signed stable release channel. Публикация кода не означает production activation.

## Проверяемые записи

- [P1 production acceptance](ops/PRODUCTION-ACCEPTANCE-2026-09-06.md);
- [P2.1 production acceptance](ops/P2.1-PRODUCTION-ACCEPTANCE-2026-09-06.md);
- [P2.3 server-side production acceptance](ops/P2.3-PRODUCTION-ACCEPTANCE-2026-09-06.md);
- [upgrade exact 0.4.3 → 0.5.0](docs/UPGRADE-TO-0.5.0.md);
- [upgrade exact 0.5.0 → 0.6.0](docs/UPGRADE-TO-0.6.0.md);
- [upgrade exact 0.6.0 → 0.7.0](docs/UPGRADE-TO-0.7.0.md);
- [upgrade exact 0.7.0 → 0.8.0](docs/UPGRADE-TO-0.8.0.md);
- [0.8.0 release evidence](ops/0.8.0-RELEASE-EVIDENCE-2026-09-06.md);
- [upgrade plan exact 0.8.0 → 0.9.0](docs/UPGRADE-TO-0.9.0.md);
- [external access 0.9 runbook](docs/EXTERNAL-ACCESS-0.9.0.md);
- [0.9.0 release evidence](ops/0.9.0-RELEASE-EVIDENCE-2026-09-06.md);
- [ADR-0007: signed stable release channel](docs/adr/0007-signed-stable-release-channel.md);
- [ADR-0010: optional bounded AD authentication](docs/adr/0010-optional-ad-authentication.md);
- [ADR-0011: external publication boundary](docs/adr/0011-external-publication-boundary.md);
- [ADR-0012: release-candidate acceptance](docs/adr/0012-release-candidate-acceptance.md).

## Разработка и CI

Engineering compute выполняется только на GitHub-hosted runners. Домашняя AI Development Infrastructure не используется.

```bash
make ci
```

PR обязан пройти Python 3.12/3.14 deterministic gates; 0.9 также запускает отдельный E2E. Release PR обязан пройти reproducible build-twice byte comparison до merge.
