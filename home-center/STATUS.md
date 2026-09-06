# Текущий статус Home Center

**Дата:** 06.09.2026  
**Repository:** `ControlCenterSoft/home-center`  
**Статус:** `0.5.0 PRODUCTION_ACCEPTED / 0.8.0 RELEASED / 0.8.0 PRODUCTION_ROLLOUT_PENDING / MANAGED_CLIENT_TRUST_PENDING`

## Принятые границы

- Home Center — самостоятельный продукт;
- GitHub-hosted engineering compute only;
- HM.DM rollout: `dc02 → canary/soak → dc01`;
- automatic failover disabled до witness/fencing certification;
- Samba AD, DNS, DHCP, Domain SID и replication topology не изменяются неявно;
- Control Center и AI Development Fabric не входят в Home Center runtime/build/deploy.

## Production `0.5.0`

Обе production-ноды остаются на принятой exact identity:

- version: `0.5.0`;
- revision: `1d1ff0be759667c40361bbd04b9da273a778b9c8`;
- artifact SHA-256: `3898daba8711dc1b24266c2677b29fc2302877724f49bf0d5cab9a5e4bcda58a`;
- release: `/opt/home-center/releases/0.5.0-1d1ff0be7596-3898daba8711`;
- cluster transaction: `20260906T180027Z-3bd073793891`;
- production acceptance evidence: `serverops-control#1624 / 5561312312`.

Managed-client Web CA enrollment/browser acceptance остаётся отдельным gate. Фактического rollout 0.6/0.7 на dc01/dc02 в текущем GitHub-сеансе не выполнялось.

## Exact merged-main `0.6.0` candidate

- revision: `2235670d77bccc1c777223eb4f50a546313b4d2d`;
- artifact SHA-256: `bf68e870339f18351f4401895f7bff18c093fa9809eb4eefda2633391fe9a811`;
- artifact bytes: `119076`;
- GitHub Actions run: `34055837243`;
- Python 3.12/3.14 gates: PASS;
- build-twice byte-identical: PASS.

0.6.0 включает:

- persisted two-node reconcile core;
- exact `VerifiedRelease` binding;
- durable checkpoint;
- dc02-first state machine;
- anti-replay/equivocation/clock rollback rejection;
- single-writer lock;
- Web/peer PKI continuity checks;
- installed `VERSION+REVISION` identity;
- visible version/build in Web UI;
- mobile Nodes one-column regression gate.

`PRODUCTION_ACTIVATION_ENABLED = False`.

## `0.7.0` Release Candidate

0.7 foundation добавляет:

- bounded content-addressed artifact store;
- fixed root-owned inbox/object roots;
- no caller-controlled source/destination path API;
- no network/download client;
- no-overwrite CAS publication + fsync;
- full P2.4 artifact/manifest/version/revision re-verification;
- fixed root-owned public trust policy и DSSE snapshot locations;
- durable anti-replay release-channel checkpoint;
- offline `ReleaseManager.evaluate()`;
- local-only `admit_local()` с single-writer lock;
- no deployment/systemctl/SSH surface;
- `PRODUCTION_RELEASE_MANAGER_ENABLED = False`;
- unified fail-closed release-policy renderer;
- staged target exact `0.7.0`;
- admitted predecessor exact published `0.6.0 / 2235670d77bccc1c777223eb4f50a546313b4d2d / bf68e870339f18351f4401895f7bff18c093fa9809eb4eefda2633391fe9a811`.

Release PR tests/security и reproducible artifact проходят; после финального docs head требуется ещё один exact-head CI PASS, merge и exact-main artifact evidence.

## `0.8.0` released

Интегрированный code baseline `e9bb45415b105ea510d0774380b98108fa5f0fc1`:

- local administrator credential/session model;
- bootstrap Bearer/token authority удалена из 0.8 Web runtime;
- безопасное интерактивное создание local-admin verifier;
- auth-free deployment/recovery renderer без копирования локального verifier между нодами;
- exact staged predecessor: `0.7.0 / f28fc1c820b065616758ca3c220794555c30a25a / 6ab15ef38b5d4b064ddb45c47009c73de3f5425553a059d75c9fb3c77bc82b82`;
- optional AD provider: disabled by default, exact KDC list, explicit administrator groups, bounded Kerberos/NSS execution, no password persistence;
- local administrator fallback не зависит от доступности AD;
- config schema `home-center.config.v3`;
- provider-aware Web UI и versioned auth contracts;
- public secret-free provider discovery; AD login скрыт и отключён до явного `ad_auth.enabled=true`;
- fail-closed exact HTTPS Origin/Host и Fetch Metadata gate для browser POST до credential/action processing;
- authenticated audited logout и COOP/CORP response hardening.

Exact release evidence:

- release: `v0.8.0`;
- URL: https://github.com/ControlCenterSoft/home-center/releases/tag/v0.8.0;
- revision/tag target: `bbb2b1e952b2072c8ce30ad6b3220c7c14280949`;
- artifact: `home-center-0.8.0-linux-amd64.tar.gz`;
- artifact SHA-256: `25fb72fdffab972703d9be2b7e457b1f4ed053bc1c013a6237558fd693e73ca8`;
- exact-main CI `34064080930`: Python 3.12 PASS, Python 3.14 PASS, build-twice PASS;
- pinned publisher `34064461198`: exact source/digest PASS, tag and both assets published;
- issue #29: completed.

Не выполнены и не заявлены как выполненные:

- production rollout на dc01/dc02 — tracked in #44;
- optional live HM.DM AD activation/acceptance — tracked in #44;
- automatic update/failover.

## Upgrade

- `0.5.0 → 0.6.0`: [`docs/UPGRADE-TO-0.6.0.md`](docs/UPGRADE-TO-0.6.0.md)
- `0.6.0 → 0.7.0`: [`docs/UPGRADE-TO-0.7.0.md`](docs/UPGRADE-TO-0.7.0.md)
- `0.7.0 → 0.8.0`: [`docs/UPGRADE-TO-0.8.0.md`](docs/UPGRADE-TO-0.8.0.md) — exact release published

Прямой переход `0.5.x/0.4.x → 0.7.0` запрещён release policy.

## Roadmap к 1.0.0

1. **0.7.x — Release Manager foundation:** exact signed-channel/trust/store/checkpoint infrastructure, production-inert.
2. **0.8.x — Security/Auth:** local administrator credentials/session model, optional AD authentication, client trust workflow, TLS/auth hardening.
3. **0.9.x — Release Candidate:** external publication hardening, full E2E, upgrade/rollback/restore, two-node parity acceptance.
4. **1.0.0 — Stable:** production acceptance обязательных gates, documentation и controlled dc02→dc01 deployment evidence.

Production auto-update и automatic failover остаются запрещены до соответствующих независимых activation/witness gates.
