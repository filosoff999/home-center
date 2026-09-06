# Текущий статус Home Center

**Дата:** 06.09.2026  
**Repository:** `ControlCenterSoft/home-center`  
**Статус:** `0.5.0 PRODUCTION_ACCEPTED / MANAGED_CLIENT_TRUST_PENDING / 0.6.0 RELEASE_CANDIDATE`

## Принятые границы

- Home Center — самостоятельный продукт;
- отдельные repository, issues, CI/CD, releases, artifacts, secrets и runtime;
- отсутствие code/runtime/build/deploy зависимостей от Control Center и AI Development Fabric;
- GitHub-hosted engineering compute only;
- HM.DM rollout: `dc02 → canary/soak → dc01`;
- automatic failover disabled до witness/fencing certification;
- Samba AD, DNS, DHCP, Domain SID и replication topology не изменяются неявно.

## Production `0.5.0`

Обе production-ноды приняты на одной exact identity:

- `dc01=0.5.0`, `dc02=0.5.0`;
- revision: `1d1ff0be759667c40361bbd04b9da273a778b9c8`;
- artifact SHA-256: `3898daba8711dc1b24266c2677b29fc2302877724f49bf0d5cab9a5e4bcda58a`;
- release: `/opt/home-center/releases/0.5.0-1d1ff0be7596-3898daba8711`;
- cluster transaction: `20260906T180027Z-3bd073793891`;
- production acceptance evidence: `serverops-control#1624 / 5561312312`;
- Web TLS ECDSA P-256/SHA-256 server-side acceptance: PASS;
- peer mTLS identities preserved: PASS;
- readiness/parity/DRS/Domain SID/protected-service gates: PASS;
- Samba AD/DNS/DHCP mutation evidence: none;
- automatic failover: disabled.

Managed-client Web CA enrollment и browser acceptance остаются отдельным явным gate. Home Center не получает права неявно менять AD/GPO ради установки trust anchor.

## P2.4 signed stable release channel

`0.5.0` содержит инертный offline/read-only verifier:

- DSSE PAE + ECDSA P-256/SHA-256 threshold verification;
- canonical release record и append-only ledger;
- `stable/superseded/quarantined` lifecycle;
- provenance/acceptance/artifact exact binding;
- freshness, anti-replay, equivocation/history rewrite rejection;
- immutable `VerifiedRelease`;
- no network/download/install authority.

Production signing private key, root-owned trust bootstrap, first production signed ledger и durable content-addressed artifact store остаются отдельными activation prerequisites.

## P2.5 / `0.6.0` Release Candidate

Реализован и CI-проверен installable persisted two-node reconcile core:

- exact P2.4 `VerifiedRelease` binding;
- durable closed checkpoint contract;
- state machine `discover → acquire → verify → admit → backup-dc02 → update-dc02 → canary-soak → backup-dc01 → update-dc01 → cluster-accept → checkpoint`;
- любое ambiguous outcome → `recovery_required`;
- dc01 completion невозможен до dc02 completion;
- sequence rollback/replay/equivocation rejection;
- clock rollback rejection;
- atomic fsync checkpoint publication;
- no-symlink and bounded checkpoint handling;
- single-writer non-blocking flock;
- exact Web CA/leaf/public-key и peer CA/certificate/public-key continuity checks;
- bounded monitoring states `current`, `drifted`, `blocked`, `quarantined`, `recovery_required`;
- production activation hard-disabled: `PRODUCTION_ACTIVATION_ENABLED = False`.

0.6.0 Operations Foundation также добавляет:

- точную installed release identity `VERSION + REVISION`;
- read-only `/api/v1/meta` с version/revision/build/source;
- видимую версию/build в Web UI;
- build-generated release identity, привязанную к exact artifact revision;
- regression gate: mobile **Узлы** всегда `grid-template-columns: 1fr`;
- exact release-cut policy: target только `0.6.0`, predecessor только accepted `0.5.0` baseline;
- fail-closed deterministic bootstrap renderer: изменение reviewed source shape блокирует build.

## 0.6.0 acceptance state

На release PR пройдены:

- Python 3.12 deterministic contracts/tests/security: PASS;
- Python 3.14 deterministic contracts/tests/security: PASS;
- 154 tests после release-cut fix: PASS;
- build-twice byte-identical artifact: PASS;
- checksum verification: PASS.

До production promotion ещё обязательны:

1. merge release PR в `main`;
2. exact merged-main CI PASS;
3. получить immutable merged-main artifact + SHA-256;
4. независимая проверка artifact identity/manifest;
5. controlled `dc02 → canary/soak → dc01` rollout;
6. exact parity, Web/peer identity, readiness, mTLS, DRS, Domain SID, backup и protected-service acceptance;
7. production acceptance evidence.

## Upgrade

Допускается только exact переход:

`0.5.0 / 1d1ff0be759667c40361bbd04b9da273a778b9c8 → 0.6.0 / <exact merged-main revision>`.

Runbook: [`docs/UPGRADE-TO-0.6.0.md`](docs/UPGRADE-TO-0.6.0.md).

Прямой переход с 0.4.x в 0.6.0 запрещён.

## Roadmap к 1.0.0

1. **0.6.x — Operations Foundation:** persisted reconcile primitives, release identity, Version Guard/cluster state foundation.
2. **0.7.x — Release Manager:** reviewed signed-channel trust/bootstrap, durable artifact store, persisted activation workflow и bounded rollback/recovery orchestration.
3. **0.8.x — Security/Auth:** local administrator model, AD authentication integration, client trust workflow и TLS/auth hardening.
4. **0.9.x — Release Candidate:** full E2E, upgrade/rollback/restore, two-node parity, external publication hardening.
5. **1.0.0 — Stable:** production acceptance всех обязательных gates и документации.

Automatic failover остаётся запрещён до отдельной witness/fencing certification.
