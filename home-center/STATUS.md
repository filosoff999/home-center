# Текущий статус Home Center

**Дата:** 06.09.2026  
**Repository:** `ControlCenterSoft/home-center`  
**Статус:** `0.4.3 SERVER-SIDE PRODUCTION_ACCEPTED / MANAGED_CLIENT_TRUST_PENDING / P2.4 0.5.0 VERIFIER_CANDIDATE`

## Принятые границы

- самостоятельный продукт;
- отдельные repository, issues, CI/CD, releases, artifacts, secrets и runtime;
- отсутствие code/runtime/build/deploy зависимостей от других продуктов;
- GitHub-hosted engineering compute only;
- HM.DM rollout: `dc02 → dc01`;
- automatic failover disabled до witness/fencing certification;
- domain services не изменяются неявно.

## Production `0.4.3`

- `dc01=0.4.3`, `dc02=0.4.3`;
- exact revision: `64f798ceae0b669cbac01b452c3cf4fd96070136`;
- artifact SHA-256: `b2dde6a51ec9450ddd23e802db50be2605c8854e804803ba014422878a02d3d4`;
- internal manifest SHA-256: `fea9edd8f61425dd685fb1f1db227685d3a7faaaca6c97f7ae641af2bccc0b75`;
- exact release: `/opt/home-center/releases/0.4.3-64f798ceae0b-b2dde6a51ec9`;
- deploy transaction: `20260906T160641Z-4618b29b86a5`, order `dc02 → dc01`;
- immutable typed Action Registry: accepted;
- единственное executable action: `service.state.read.v1`, local/read-only/allowlisted;
- persisted idempotent replay, включая replay после рестарта `dc02`: PASS;
- injection, remote target, unknown action и conflicting idempotency: fail-closed;
- GitHub-hosted CI: PASS;
- exact canary/deployment acceptance: PASS;
- separate Web CA distribution/key placement: PASS;
- `dc01` Web certificate SHA-256: `28594a1e6afb94dbe944c6fa874325a21c2beeceec3d87774320284c6cdaa443`;
- `dc02` Web certificate SHA-256: `a495d61150997d521c46fae9a06abf12626e6d1b5a10282f14a44f9f50d1921f`;
- restricted browser-compatible TLS 1.2/TLS 1.3 and future VIP identity: PASS;
- peer mTLS TLS 1.3 both directions and peer public fingerprints unchanged: PASS;
- rollback points созданы на обоих узлах;
- backup после action: PASS;
- Samba domain SID `S-1-5-21-483832520-828804035-215000592` сохранён;
- DRS replication: PASS;
- Samba AD/DNS/DHCP mutations: none;
- `dc01-control-agent.service` mutation: none;
- automatic failover: disabled.

P2.3 остаётся открытым только для реального managed-client evidence: явная установка Web CA административным процессом и browser trust на Windows/Android без warning. Это не разрешает Home Center изменять AD/GPO.

## Quarantined releases

- `0.4.0`: **QUARANTINED / DO_NOT_DEPLOY** — Web leaf и общий CA оставались Ed25519 и воспроизводили Android/Chrome TLS alert 40;
- `0.4.1`: **QUARANTINED / DO_NOT_DEPLOY** — empty regular flock-файл ошибочно отклонялся из-за строкового сравнения GNU `stat %F`;
- `0.4.2`: **QUARANTINED / DO_NOT_DEPLOY** — software rollout прошёл, но Web activation preflight отклонил безопасный marker `root:home-center:0600`, ошибочно требуя gid `0`; Web identity не переключалась;

## Активный P2.4 / `0.5.0`

- ADR-0007 и closed schemas для release record, DSSE envelope, ledger, trust policy, checkpoint и result;
- canonical ASCII JSON + DSSE PAE + ECDSA P-256/SHA-256 threshold verification;
- exact repository/ref/workflow/provenance/acceptance binding;
- append-only atomic transitions `stable`, `superseded`, `quarantined`; quarantine terminal;
- generation/sequence/checkpoint anti-replay, stale/future/equivocation/history-rewrite rejection;
- full content-addressed artifact byte, archive, manifest, version и revision verification;
- immutable `VerifiedStableRelease`; verifier offline/read-only, без network/deploy/systemctl;
- reproducible build-twice CI and immutable full-SHA action pins.

Production activation blockers:

- отдельные Home Center production signing keys и утверждённые public fingerprints отсутствуют;
- root-owned trust policy ещё не bootstrap-установлена на обе ноды;
- durable Home Center-owned content-addressed artifact store ещё не создан;
- signed production ledger не опубликован;
- P2.5 persisted two-node updater/checkpoint writer не реализован и не включён.

Проверяемые доказательства:

- [`ops/PRODUCTION-ACCEPTANCE-2026-09-06.md`](ops/PRODUCTION-ACCEPTANCE-2026-09-06.md);
- [`ops/P2.1-PRODUCTION-ACCEPTANCE-2026-09-06.md`](ops/P2.1-PRODUCTION-ACCEPTANCE-2026-09-06.md).
- [`ops/P2.3-PRODUCTION-ACCEPTANCE-2026-09-06.md`](ops/P2.3-PRODUCTION-ACCEPTANCE-2026-09-06.md).

## Roadmap

1. **P2.2 — bounded privileged helper и policy gate** ([#9](https://github.com/ControlCenterSoft/home-center/issues/9)).
   Отдельный минимальный privileged execution boundary, deny-by-default policy scope и failure/recovery доказательства. Ни одна mutation action не допускается в registry до отдельного ADR, тестов и canary acceptance.

2. **P2.3 — HTTPS/TLS certificate lifecycle и доверенный Web UI** ([#10](https://github.com/ControlCenterSoft/home-center/issues/10)).
   Server-side lifecycle принят; остаётся только явное managed-client CA/browser trust evidence.

3. **P2.4 — signed stable release channel** ([#17](https://github.com/ControlCenterSoft/home-center/issues/17)).
   `0.5.0` реализует инертный verifier и security contracts. Production signing/trust/store остаются отдельным gate.

4. **P2.5 — persisted two-node update reconcile.**
   Только после P2.4 trust bootstrap: discover → verify → dc02 → continuous soak → dc01 → accept/rollback с fsync checkpoint и bounded retry.

Automatic failover остаётся запрещён до отдельной witness/fencing certification.
