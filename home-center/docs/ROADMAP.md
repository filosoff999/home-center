# Roadmap Home Center

**Статус:** `0.5.0 PRODUCTION ACCEPTED / 0.8.0 RELEASED_PRODUCTION_PENDING / 0.9.0 DEVELOPMENT ACTIVE`
**Execution epic:** `#1`.

Roadmap определяет последовательность продуктовых gates. Home Center уже имеет accepted production P1, P2.1 и P2.2 на `dc01/dc02`; дальнейшая работа продолжает P2 и не должна регрессировать принятые safety/evidence свойства.

## Release train 0.9.0

Статус: **IMPLEMENTED IN DEVELOPMENT / LIVE ACCEPTANCE PENDING**.

- [x] source/package/Web version 0.9.0;
- [x] exact published 0.8.0 predecessor policy;
- [x] disabled-by-default external publication boundary;
- [x] strict trusted-proxy provenance, origin and rate-limit gates;
- [x] desktop/mobile logical-path E2E in GitHub-hosted CI;
- [x] build-twice artifact and two runtime node E2E;
- [x] backup/restore verification in E2E;
- [x] closed two-node rollout/rollback/parity acceptance contract and verifier;
- [x] external access and exact upgrade runbooks;
- [ ] exact merged-main 0.9 revision and artifact digest;
- [ ] live gateway TLS + desktop/mobile browser evidence;
- [ ] live `dc02 → dc01` rollout, reverse rollback/restore drill and cluster parity evidence;
- [ ] signed stable promotion and production activation.

Ни один synthetic/CI result не заменяет live HM.DM acceptance.

## P0 — Repository / Architecture Readiness

Статус: **baseline сформирован; часть architecture-debt остается cross-cutting**.

- [x] отдельный repository `ControlCenterSoft/home-center`;
- [x] компонентные границы;
- [x] `HC-*` requirements;
- [x] node enrollment и drain/remove state-machine baseline;
- [x] canonical OpenAPI/JSON Schema baseline;
- [x] production two-node deployment profile;
- [x] GitHub-hosted CI/release path;
- [x] production evidence baseline;
- [ ] independent reviewer/security-critical ownership;
- [ ] formal state/witness/fencing ADR before automatic failover;
- [ ] compatibility/versioning matrix formalization;
- [ ] complete threat-model traceability;
- [ ] release evidence matrix formalization.

Open P0 debt may proceed in parallel with early P2 only if it does not weaken mutation safety.

## P1 — Read-only Control Plane Foundation

Статус: **PRODUCTION ACCEPTED — Home Center 0.1.0**.

Accepted:

- [x] versioned HTTPS API/Web UI;
- [x] node identity/capability inventory;
- [x] leader/standby topology;
- [x] TLS 1.3/mTLS cluster identity;
- [x] local SQLite state and integrity checks;
- [x] HMAC-linked tamper-evident audit;
- [x] verified backup;
- [x] systemd hardening;
- [x] immutable artifact + SHA-256;
- [x] canary rollout `dc02 → dc01`;
- [x] fail-closed mutation boundary;
- [x] production failure/recovery scenario.

**Gate P1:** PASS. Automatic failover remains intentionally disabled.

## P2 — Managed Node / Typed Actions / Reconcile

Status: **IN PROGRESS** — P2.1/P2.2 remain accepted foundations; exact `0.4.3` is server-side production accepted after P2.3 rollout/rotation. Managed-client trust evidence remains open. `0.4.0`, `0.4.1` and `0.4.2` must not be used for new rollout. P2.4 targets inert verifier candidate `0.5.0`.

Accepted foundations:

- [x] typed Action Registry — `0.2.0`, accepted in `#6`;
- [x] first non-domain safe read action — `service.state.read.v1`;
- [x] bounded privileged helper / deny-by-construction policy gate — `0.3.0`, production accepted in `#9`;
- [x] deterministic helper replay/conflict/interruption evidence;
- [x] P2.2 canary `dc02 → dc01` with DRS preservation and no AD/DNS/DHCP mutation.

### P2.3 — HTTPS/TLS certificate lifecycle — `#10`

Status: **SERVER-SIDE ACCEPTED / MANAGED-CLIENT TRUST OPEN**.

- [x] ADR-0006 Web TLS trust/identity/rollback model;
- [x] distinct Web TLS identity path separated from peer mTLS;
- [x] minimum TLS policy: Web 1.2+, peer mTLS 1.3;
- [x] strict candidate validation: chain, validity, node hostname, future VIP, IP, server-only EKU and key match;
- [x] fingerprint-addressed Web certificate releases and atomic `web-current` switch;
- [x] bounded helper action `tls.web.activate.v1` with no generic shell/argv/path surface;
- [x] deterministic activation rollback and presented-certificate postflight;
- [x] renewal/expiry/candidate status API plus trust-anchor download;
- [x] Web UI certificate health/trust/renewal view;
- [x] unprivileged scheduled maintenance coordinator;
- [x] staged internal-CA issuance/rotation workflow `dc02 → dc01` with fixed node identities;
- [x] packaging/install/rollback integration for maintenance units;
- [x] RCA for Android/Chrome TLS alert 40: browser ClientHello omitted Ed25519;
- [x] dedicated ECDSA P-256/SHA-256 Web CA and leaf profile, independent of Ed25519 peer mTLS;
- [x] real TLS 1.2/TLS 1.3 restricted-signature handshake regression tests;
- [x] pre-switch rollback-chain classification for legacy `0.3.0` and quarantined `0.4.0` states;
- [x] durable owner-marker/digest CAS, crash reconciliation and helper recovery-latch proof;
- [x] fsync-stable node/cluster journals with pre-mutation peer identity snapshots and exact rollback overview gates;
- [x] exact-head branch/main CI final PASS after all P2.3 changes;
- [x] immutable release artifact and PR/main gates;
- [x] production canary `dc02` Web rotation acceptance;
- [x] production `dc01` promotion only after dc02 PASS;
- [ ] managed Windows OS/browser trust acceptance with no certificate warning;
- [ ] Android/Chrome acceptance with no TLS alert 40 or `ERR_SSL_VERSION_OR_CIPHER_MISMATCH`;
- [ ] synthetic expiry/renewal and failed-rotation rollback acceptance;
- [x] peer mTLS post-rotation PASS both directions;
- [x] peer CA, node certificate and node public-key fingerprints unchanged on both nodes;
- [x] final Samba domain SID/DRS no-regression evidence.

### P2.4 — Signed stable release channel

Status: **ACTIVE / `0.5.0` VERIFIER CANDIDATE**.

- [x] closed immutable release-record/DSSE/trust/ledger/checkpoint/result contracts;
- [x] ECDSA P-256/SHA-256 threshold verification with canonical bytes and fixed product/repository/workflow scope;
- [x] append-only `stable`, `quarantined`, `superseded` state machine and terminal quarantine;
- [x] freshness, generation/sequence anti-rollback, equivocation and history-rewrite rejection;
- [x] exact content-addressed artifact byte/manifest/identity/archive verification;
- [x] verifier has no network/deploy/service-control surface and returns immutable identity;
- [ ] dedicated Home Center production signing keys and approved public fingerprints, separate from Control Center;
- [ ] root-owned trust-policy bootstrap on both nodes;
- [ ] durable Home Center-owned content-addressed artifact storage independent of expiring CI;
- [ ] first signed production ledger linking source SHA, artifact SHA, CI run and immutable acceptance evidence.

### P2.5 — Persisted two-node update reconcile

Status: **PLANNED / outside the `0.5.0` verifier-only scope**.

- [ ] periodically compare promoted stable release with both nodes;
- [ ] persisted checkpointed state machine: discover → verify → dc02 → soak → dc01 → accept;
- [ ] bounded retry/backoff for transient failures and digest quarantine for terminal regressions;
- [ ] automatic rollback to the last accepted release when a health gate fails;
- [ ] exact version/revision/artifact parity, peer-mTLS and DRS postconditions;
- [ ] never mutate Control Center, `dc01-control-agent.service`, Samba AD/DNS/DHCP or automatic failover state.

Remaining P2 after P2.3:

- [ ] persisted asynchronous Change/Job state machine beyond current synchronous foundations;
- [ ] complete RBAC/policy for later infrastructure mutations;
- [ ] Desired State / Actual State;
- [ ] drift/reconcile;
- [ ] checkpoint/retry/recovery;
- [ ] Web action/preflight/progress/recovery UX;
- [ ] broader P2 synthetic/security/failure acceptance.

**Gate P2:** one managed node is reproducibly changed through Desired State and typed actions; interruption cannot create silent success or duplicate side effects.

## P3 — Modules / Market Foundation

- [ ] ModuleManifest v1;
- [ ] immutable/content-addressed package registry;
- [ ] signed/checksummed staging;
- [ ] path-safe strict package validation;
- [ ] dependency/conflict/capability planner;
- [ ] permissions review;
- [ ] install/update/remove lifecycle;
- [ ] module system identity/sandbox;
- [ ] module health;
- [ ] backup/restore contract;
- [ ] reference stateful module acceptance.

**Gate P3:** reference stateful module passes install → upgrade → backup → restore → disable → remove including failure/recovery tests.

## P4 — Productized Second-Node Enrollment / Cluster

- [ ] ClusterMembership schema;
- [ ] DeploymentProfile/RolePlacementPolicy;
- [ ] compatibility preflight;
- [ ] persisted second-node enrollment workflow;
- [ ] dependency/module bootstrap;
- [ ] role planning;
- [ ] replication health gates;
- [ ] monitoring/backup enrollment;
- [ ] HM.DM read-only Samba/DNS/DHCP/file-role adapters;
- [ ] role-specific mutation adapters only after separate safety certification;
- [ ] post-join production gate.

**Gate P4:** clean compatible second node reaches `READY` via product workflow; incompatible or interrupted joins are safe and idempotent.

## P5 — Maintenance / Drain / Remove

- [ ] DrainPlan/DrainResult schemas;
- [ ] dependency/quorum/redundancy preflight;
- [ ] maintenance state;
- [ ] role/VIP relocation where supported;
- [ ] replication finalization;
- [ ] membership/trust cleanup;
- [ ] interrupted-drain recovery;
- [ ] separate destructive wipe workflow;
- [ ] active-owner and standby acceptance scenarios.

**Gate P5:** any node is either safely removed with evidence or deterministically blocked before unsafe state.

## P6 — HA / Backup / Disaster Recovery Certification

- [ ] independent witness/fencing architecture;
- [ ] authoritative state/consensus ADR;
- [ ] automatic promotion rules;
- [ ] split-brain prevention tests;
- [ ] leader/standby/witness loss scenarios;
- [ ] partition/failback tests;
- [ ] scheduled backup/retention;
- [ ] automated restore verification;
- [ ] clean-host/node-replacement recovery;
- [ ] cluster-aware DR runbooks;
- [ ] automatic failover product enablement only after PASS.

**Gate P6:** automatic failover/HA claims exist only with witness/fencing and scenario evidence; backup is considered healthy only with verified restore.

## P7 — Productization

- [ ] polished responsive UX;
- [ ] first-run/bootstrap packaging;
- [ ] signed update channel;
- [ ] module catalog governance;
- [ ] support bundle;
- [ ] privacy/telemetry defaults;
- [ ] compatibility matrix;
- [ ] end-user/operator documentation;
- [ ] supported upgrade paths;
- [ ] rollback rehearsal;
- [ ] release certification/evidence dashboard.

**Gate P7:** release can be clean-installed, upgraded, recovered and independently accepted without source-code access.

## Приоритет инфраструктурных модулей после P2

1. Home Center self-management;
2. bounded systemd adapter;
3. storage/filesystem safe adapter;
4. Samba AD/DNS read-only health;
5. DHCP read-only state;
6. DNS diagnostics;
7. file-share health;
8. backup targets;
9. notifications;
10. DHCP mutation/HA workflows;
11. DNS mutation workflows;
12. Samba/domain mutations only after dedicated certification;
13. print server;
14. PXE;
15. optional virtualization/network integrations.

## Правило приоритета

Новый feature не может обходить safety foundation предыдущего уровня. Market не создает parallel privileged path; cluster automation не пропускает compatibility/quorum gates; automatic HA не включается без witness/fencing; backup не считается рабочим без restore evidence.
