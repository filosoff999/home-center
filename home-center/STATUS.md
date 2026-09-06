# Текущий статус Home Center

**Дата:** 06.09.2026  
**Repository:** `ControlCenterSoft/home-center`  
**Статус:** `RUNTIME 0.4.2 QUARANTINED / P2.2 ACCEPTED / P2.3 0.4.3 RELEASE_CANDIDATE`

## Принятые границы

- самостоятельный продукт;
- отдельные repository, issues, CI/CD, releases, artifacts, secrets и runtime;
- отсутствие code/runtime/build/deploy зависимостей от других продуктов;
- GitHub-hosted engineering compute only;
- HM.DM rollout: `dc02 → dc01`;
- automatic failover disabled до witness/fencing certification;
- domain services не изменяются неявно.

## Production

- accepted baseline version: `0.3.0`;
- accepted baseline revision: `6b0c0db144bfd2a7b7a7db1a868d649f20825721`;
- observed runtime after software rollout: `dc01=0.4.2`, `dc02=0.4.2`, exact revision `9f376e3d39eb29b2c8e402d085cba8b9fee4258d`;
- exact release path и artifact SHA подтверждаются production evidence в `#1624`;
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

## Активный P2.3 release candidate

- target version: `0.4.3`;
- `0.4.0`: **QUARANTINED / DO_NOT_DEPLOY** — Web leaf и общий CA оставались Ed25519 и воспроизводили Android/Chrome TLS alert 40;
- `0.4.1`: **QUARANTINED / DO_NOT_DEPLOY** — empty regular flock-файл ошибочно отклонялся из-за строкового сравнения GNU `stat %F`;
- `0.4.2`: **QUARANTINED / DO_NOT_DEPLOY** — software rollout прошёл, но Web activation preflight отклонил безопасный marker `root:home-center:0600`, ошибочно требуя gid `0`; Web identity не переключалась;
- Web PKI: отдельный Web CA и leaf, ECDSA P-256, ECDSA-with-SHA-256;
- peer PKI: существующие Ed25519 `ca.crt`, `node.crt`, `node.key`, TLS 1.3 mTLS — без изменения;
- rollout: только immutable artifact, `dc02 → dc01`, с readiness, restricted-sigalgs, peer-mTLS, DRS и rollback gates;
- статус acceptance: `0.4.3` ожидает exact-head PR/main CI, software rollout и Web rotation; принятым baseline остаётся `0.3.0`, а текущий `0.4.2` runtime quarantined.
- незакрытые gates: immutable digest, `dc02` canary/soak, `dc01` promotion, Android/Chrome + managed Windows trust, peer-mTLS обе стороны, unchanged peer fingerprints, SID/DRS/no-mutation и synthetic rollback.

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
