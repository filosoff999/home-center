# Home Center — план реализации

**Версия:** 2.4
**Дата:** 2026-09-06
**Статус:** `0.5.0 PRODUCTION ACCEPTED / 0.9.0 RELEASED_PRODUCTION_ACCEPTANCE_PENDING`
**Execution epic:** `#1`.
**Основание:** `TECHNICAL-SPECIFICATION.md`, `REQUIREMENTS.md`, `ARCHITECTURE.md`, production acceptance exact `0.4.3`.

## 1. Исходная точка

P1, P2.1 и P2.2 не проектируются заново: они уже приняты в production HM.DM. P2.3 server-side принят на exact `0.4.3`; managed-client Web CA/browser trust остаётся отдельным открытым evidence gate. P2.4 добавляет в `0.5.0` только инертный verifier signed release channel и не включает production signing либо autonomous update.

Принятые свойства 0.1.0:

- двухузловой leader/standby deployment `dc01/dc02`;
- GitHub-hosted deterministic CI/build;
- immutable artifact + SHA-256;
- HTTPS/mTLS 1.3 cluster identity;
- node identity/capability inventory;
- read-only fail-closed product API;
- SQLite local state;
- HMAC-linked tamper-evident audit;
- verified local backup/restore integrity;
- systemd least-privilege hardening;
- canary rollout `dc02 → dc01`;
- no generic shell API;
- automatic failover disabled without witness/fencing.

Опубликованный слой 0.9 добавляет внешнюю границу, exact 0.8→0.9 policy, deterministic E2E и closed acceptance verifier. Exact-main artifact опубликован; следующий необратимо последовательный gate — live `dc02 → dc01` acceptance. Signed promotion и production activation выполняются только после evidence PASS.

## 2. Исполнительная стратегия

Цикл каждой capability:

`HC Requirement → ADR/Contract → Implementation → Unit/Contract → Integration/Security/Failure → Immutable Artifact → Synthetic Acceptance → Canary dc02 → Postconditions → dc01 → Cluster Acceptance → Evidence`.

Правила:

1. build/test выполняются на GitHub-hosted runners; `dc01/dc02` не build workers;
2. production получает только immutable versioned artifacts;
3. rollback material создается до material mutation;
4. mutation разрешается только typed action/job;
5. post-condition gate определяет success;
6. failure path тестируется до production promotion;
7. AI Development Infrastructure не используется для Home Center build/runtime;
8. production secrets не доступны untrusted PR jobs.

## 3. Технологическая стратегия

### 3.1. Сохраняем accepted P1

- Python `>=3.12` Core;
- SQLite для leader-owned/local control state;
- OpenAPI 3.1 / JSON Schemas;
- systemd;
- mTLS cluster channel;
- GitHub Actions.

### 3.2. Не переписываем P1 без доказанной причины

Предыдущая Rust/PostgreSQL рекомендация рассматривается как историческая preferred direction. Переход на Rust или PostgreSQL возможен только через ADR с measurable benefit, migration/rollback plan и compatibility tests.

### 3.3. Где Rust допустим приоритетно

- privileged helper;
- security-sensitive Node Agent component;
- parsers/validators, где memory-safety/TCB reduction дает измеримую пользу.

### 3.4. Когда потребуется новый datastore decision

До P4/P6 необходимо принять отдельный state/consensus ADR:

- остается ли control state single-writer leader-owned;
- как state переносится/реплицируется;
- нужен ли PostgreSQL/другой transactional backend;
- как исключается split-brain;
- какое место занимает independent witness/fencing.

Автоматический failover не включается, пока этот ADR и P6 evidence не закрыты.

## 4. Workstreams

### A — Contracts / Architecture

ADR, schemas, OpenAPI, compatibility, traceability.

### B — Core / State / RBAC

Desired/Actual State, jobs, policy, audit evolution, persistence abstractions.

### C — Agent / Privileged Boundary

Typed Action Registry, helper IPC, adapters, recovery.

### D — Web / UX

Mutation UX, job progress, node/config/module/cluster pages.

### E — Market

Manifest, immutable staging, permissions, dependency planner, lifecycle.

### F — Cluster / HA

Second-node automation, role placement, replication gates, drain/remove, witness/fencing.

### G — Backup / Update / Operations

Restore verification, updater, rollback, support bundle, runbooks.

### H — Verification / Release

Security-negative, failure injection, canary, production evidence.

Параллелизация допускается только при стабильных contracts между workstreams.

## 5. P0/P1 — зафиксировать как completed baseline

### Что уже считается принятым

- namespace/governance;
- core/API/Web baseline;
- contracts baseline;
- read-only node inventory/health;
- production two-node leader/standby topology;
- mTLS PKI;
- audit-chain verification;
- backup verification;
- immutable artifact and canary deployment;
- fail-closed mutation boundary;
- production failure/recovery test Home Center process outage on dc02.

### Remaining architecture debt before advanced HA

- independent reviewer/security ownership;
- authoritative state/witness/fencing ADR;
- final compatibility/versioning matrix;
- complete threat model traceability;
- release evidence matrix formalization.

Эти пункты могут закрываться параллельно с ранним P2, если не ослабляют mutation boundary.

## 6. P2 — Managed Node / Typed Actions / Reconcile

### Цель P2

Перейти от read-only P1 к безопасным управляемым изменениям одной ноды через typed actions, сохранив fail-closed модель.

### P2.1 — Action Contract Registry (accepted foundation)

**Status:** production accepted as `0.2.0`, tracked in `#6`.

Создать machine-readable registry, где каждый action содержит:

- stable action ID/version;
- input/output schema;
- required `resource.action` permission;
- target capability requirements;
- destructive/risk classification;
- preconditions;
- timeout;
- idempotency semantics;
- postconditions;
- rollback/forward-recovery;
- audit/evidence schema.

#### First action set

1. read service state;
2. controlled service restart;
3. controlled service start/stop only for allowlisted Home Center-owned/test services;
4. atomic configuration replace in owned test path;
5. package/inventory facts;
6. network/storage read adapters;
7. diagnostic bundle action.

Samba/DNS/DHCP production mutations не входят в первый action set.

### P2.future — Change/Job Engine

Реализовать persisted workflow:

`queued → preflight → running → validating → succeeded | failed | recovery_required | cancelled`.

Обязательно:

- idempotency key;
- checkpoint persistence;
- exact initiator/auth context;
- reason/intent;
- step results;
- timeout/cancel policy;
- post-condition validation;
- retry/rollback metadata;
- audit link.

### P2.future — RBAC Policy Completion

- Owner/Admin/Operator/User/Observer/Service Account;
- atomic `resource.action` permissions;
- per-node/per-group scope;
- no self-elevation;
- service-token scoping;
- policy-denial audit.

### P2.2 — Privileged Helper (accepted foundation)

Минимальный helper:

- separate system identity;
- fixed local IPC;
- typed message schema;
- caller authentication;
- allowlist action IDs;
- no shell string;
- sanitized environment;
- validated paths;
- resource/time limits;
- systemd hardening;
- structured secret-safe result.

Решение Python vs Rust для helper оформляется ADR; default recommendation — Rust only if it demonstrably reduces TCB without delaying contract correctness.

### P2.future — Desired/Actual/Reconcile

- desired object schema;
- actual observation schema;
- drift classification;
- reconcile planner;
- no-op detection;
- retry/backoff;
- stale/offline handling;
- per-resource concurrency lock;
- post-reconcile evidence.

### P2.future — Web UX

- action preview;
- diff/preflight;
- required permission;
- progress timeline;
- explicit `failed/recovery_required` state;
- result/evidence link;
- retry only where idempotency contract allows.

### P2 tests

- direct API RBAC bypass;
- malformed action payload;
- action injection attempts;
- timeout halfway;
- helper unavailable;
- node disconnect during job;
- duplicate request/idempotency;
- restart Core during job;
- postcondition failure;
- rollback failure;
- stale Actual State.

### Gate P2

Одна managed node безопасно изменяется Desired State через typed action. На каждом injection point system state либо converges, либо явно переходит в `recovery_required`; silent success невозможен.

## 7. P3 — Market / Module Platform

### Цель

Добавить расширяемость без превращения module install в root execution channel.

### P3.1 — ModuleManifest v1

Поля:

- id/name/version;
- schema version;
- Core compatibility range;
- platform/arch;
- required capabilities;
- dependencies/conflicts;
- permissions;
- privileged action dependencies;
- ports/network scopes;
- persistent paths;
- health contract;
- migrations;
- backup/restore;
- update/rollback;
- uninstall data policy;
- checksums/signature/publisher.

### P3.2 — Content-addressed Registry

- artifact hash = identity;
- immutable package storage;
- no overwrite existing digest;
- staged version before activation;
- package size/type policy;
- path-safe extraction.

### P3.3 — Signature / Integrity

- SHA-256 mandatory;
- signature policy and trust roots;
- invalid/missing signature behavior per policy;
- package metadata tied to exact Core release.

### P3.4 — Dependency Planner

- semantic version constraints;
- conflicts;
- capability requirements;
- Core compatibility;
- permission delta;
- deterministic plan;
- cycle detection;
- failure before mutation.

### P3.5 — Lifecycle

`Acquire → Stage → Verify → Plan → Permission Review → Backup → Install Inactive → Migrate → Health → Activate → Commit/Audit`.

Update/remove use the same planning/recovery semantics.

### P3.6 — Module Isolation

- dedicated identity;
- systemd sandbox;
- writable path allowlist;
- network permission mapping;
- no Docker socket/root-equivalent default;
- no master secrets;
- disable without data deletion.

### Gate P3

Reference stateful module passes install → upgrade → backup → restore → disable → remove plus interrupted install/update and malicious package tests.

## 8. P4 — Automatic Second Node / Cluster Automation

### Цель

Превратить текущую manually deployed second node в product-managed repeatable enrollment workflow.

### P4.1 — Topology Contracts

- ClusterMembership;
- NodeRole;
- DeploymentProfile;
- RolePlacementPolicy;
- ReplicationLink;
- HealthGate;
- FailoverPolicy metadata.

### P4.2 — Compatibility Preflight

Проверить до mutation:

- OS/platform/arch;
- Core/agent/module versions;
- trust/PKI;
- DNS/time;
- network reachability;
- storage/capacity;
- dependencies;
- current topology;
- replication prerequisite;
- quorum constraints.

### P4.3 — Second-node State Machine

`Discovered → Preflighted → Trusted → Enrolled → CapabilitiesKnown → DependenciesInstalled → RolesPlanned → Replicating → HealthValidated → Ready`.

Persisted checkpoints обязательны.

### P4.4 — HM.DM Role Adapters

Read-only/health first:

- Samba AD DC identity/replication;
- DNS resolver/service state;
- DHCP inventory/role state;
- file storage health;
- Home Center peer role.

Mutation adapters добавляются по одному после dedicated safety tests.

### P4.5 — Deployment Automation

- install exact artifact;
- create identities/config/PKI;
- enroll;
- validate capabilities;
- attach monitoring/backup;
- start replication only via role-specific adapter;
- postconditions;
- mark `READY` only after health gate.

### P4 acceptance

1. clean compatible node automatically joins;
2. incompatible node is rejected before mutation;
3. interrupted join resumes safely;
4. duplicate join does not create duplicate membership/credentials;
5. production HM.DM Domain SID and AD health remain unchanged unless explicit AD adapter action requested.

### Gate P4

Second-node bootstrap is a product workflow, not operator hand assembly.

## 9. P5 — Maintenance / Drain / Remove

### Цель

Безопасно вывести любую ноду, включая current role owner.

### P5.1 — DrainPlan

Plan должен показать:

- dependent services/modules;
- redundancy impact;
- replication state;
- target relocation nodes;
- storage capacity;
- backup readiness;
- quorum/witness impact;
- destructive operations = none by default.

### P5.2 — State Machine

`MaintenanceRequested → Preflight → Draining → RolesRelocated → ReplicationValidated → QuorumValidated → ServicesValidated → MembershipRemoved → DecommissionReady`.

### P5.3 — Hard Blocks

Operation блокируется при:

- loss of mandatory service;
- data unavailability risk;
- insufficient target capacity;
- replication unhealthy;
- quorum loss;
- ambiguous ownership;
- unsupported role relocation.

### P5.4 — Wipe Separation

`Remove` не удаляет local persistent data автоматически. `Wipe` — separate permission/action/confirmation/evidence.

### P5 tests

- active owner drain;
- standby drain;
- replication lag;
- node disconnect mid-drain;
- relocation failure;
- Core restart mid-drain;
- insufficient quorum;
- second request/idempotency;
- attempt wipe via remove payload.

### Gate P5

Unsafe removal impossible through supported API; safe removal either completes with evidence or stops at recoverable checkpoint.

## 10. P6 — HA / Backup / Disaster Recovery Certification

### Цель

Добавить только доказанную автоматическую отказоустойчивость.

### P6.1 — Witness/Fencing Architecture

До automatic failover:

- independent witness/quorum source;
- deterministic leader election/promotion rule;
- fencing mechanism preventing old leader writes;
- network partition behavior;
- witness-loss behavior;
- failback policy.

### P6.2 — State Store Decision

ADR должен определить:

- authoritative state owner;
- replication method;
- SQLite continuation vs migration;
- PostgreSQL/other backend if required;
- consistency model;
- backup/restore semantics;
- schema migration and rollback.

### P6.3 — Failover Certification

Scenarios:

- standby loss;
- leader service loss;
- leader host loss;
- network partition;
- witness loss;
- delayed replication;
- simultaneous recovery;
- failback.

### P6.4 — Backup/Restore Certification

- scheduled backup;
- retention;
- checksum/tamper detection;
- automated restore verification;
- clean-host restore;
- node replacement;
- cluster-aware restore ordering;
- disaster recovery runbook rehearsal.

### Gate P6

Automatic failover button/claim remains disabled until split-brain prevention and restore evidence are both PASS.

## 11. P7 — Productization

### Scope

- polished responsive UX;
- first-run installer/bootstrap;
- signed update channel;
- module catalog governance;
- support bundle;
- privacy/telemetry defaults;
- compatibility matrix;
- operator/user documentation;
- release notes;
- upgrade from supported previous release;
- rollback rehearsal;
- release certification dashboard.

### Gate P7

A release can be installed cleanly, upgraded from previous supported version, recovered from backup, operated without source-code access, and independently validated by evidence bundle.

## 12. Infrastructure module implementation order

После P2 typed action foundation рекомендуется следующий порядок:

1. Home Center self-management adapter;
2. systemd generic bounded adapter;
3. storage/filesystem read + safe owned-path config adapter;
4. Samba AD/DNS **read-only health** adapter;
5. DHCP **read-only state** adapter;
6. DNS diagnostics adapter;
7. file-share health adapter;
8. backup targets;
9. notifications;
10. controlled DHCP mutation/HA workflows;
11. controlled DNS mutation workflows;
12. controlled Samba/domain operations only after dedicated AD safety certification;
13. print server module;
14. PXE module;
15. optional virtualization/network integrations.

## 13. CI gates

### Every PR

- syntax/import/build;
- formatter/linter/type checks;
- unit tests;
- JSON Schema/OpenAPI validation;
- contract negative tests;
- secret scan;
- dependency/supply-chain checks;
- affected security tests.

### Persistence-affecting PR

- clean state initialization;
- upgrade migration;
- rollback/restore compatibility;
- corrupted state failure mode.

### Privileged-action PR

- RBAC negative;
- malformed input;
- injection/path traversal;
- timeout;
- helper unavailable;
- recovery;
- audit completeness;
- secret leakage.

### Cluster PR

- partition/failure simulation;
- idempotent retry;
- stale membership;
- replication gate;
- quorum block.

### Release PR

- deterministic artifact;
- SHA-256 manifest;
- exact revision metadata;
- package integrity;
- synthetic E2E;
- canary deploy plan;
- rollback material.

## 14. Production rollout policy

Для HM.DM:

1. read-only preflight обоих узлов;
2. verify backup/rollback material;
3. deploy `dc02` canary;
4. validate service, readiness, peer channel, audit, backup, domain health;
5. deploy `dc01`;
6. validate 2/2 topology;
7. run capability-specific scenario test;
8. publish exact evidence to repository/issue;
9. rollback immediately if a mandatory gate fails and rollback remains safe.

Domain mutations are never implicit side effect of Home Center upgrade.

## 15. Issue decomposition

Каждая issue содержит:

- `HC-*` requirement IDs;
- linked ADR/contracts;
- goal/non-goals;
- dependencies/preconditions;
- security/data/privileged impact;
- failure/recovery semantics;
- acceptance criteria;
- evidence requirements;
- rollout/rollback.

Issue должна закрывать один проверяемый vertical slice.

## 16. Definition of Ready

Task готов к coding, если:

- requirement определен;
- contract/API sufficiently defined;
- permissions известны;
- state ownership известен;
- failure/recovery path известен;
- acceptance automation возможна;
- production secret/data не требуется в CI.

## 17. Definition of Done

Task закрыт только если:

- merged through PR;
- required CI PASS;
- contract/docs updated;
- security-negative tests PASS;
- failure/recovery tested according to risk;
- audit/health/evidence implemented;
- compatibility/migration assessed;
- rollback/recovery proven where applicable;
- production evidence tied to exact commit/artifact when deployed.

## 18. Приоритет ближайших задач

Текущая очередь после принятого exact `0.4.3`:

1. завершить PR/CI и transitional canary rollout verifier-only `0.5.0` из exact `0.4.3`;
2. получить отдельное authority для Home Center production signing keys и утвердить public-key fingerprints;
3. выполнить root-owned trust-policy bootstrap на `dc02`, затем `dc01` с rollback evidence;
4. опубликовать первый signed append-only ledger и durable content-addressed release assets;
5. реализовать P2.5 persisted updater: discover → verify → dc02 → soak → dc01 → accept/rollback;
6. завершить managed-client Web CA/browser trust evidence P2.3 без неявной AD/GPO mutation;
7. реализовать persisted asynchronous Change/Job и Desired/Actual State reconcile;
8. добавить P2 Web job/action UX и выполнить P2 production acceptance;
9. перейти к ModuleManifest, content-addressed registry и P3 lifecycle;
10. продолжить P4–P7 только после соответствующих architecture/security gates.

## 19. Key risks

### R1 — Generic privileged execution leaks into product

Mitigation: typed registry + helper allowlist + no shell strings.

### R2 — Current P1 is rewritten instead of evolved

Mitigation: accepted Python/SQLite baseline is preserved unless ADR proves migration value.

### R3 — Two-node topology is marketed as automatic HA prematurely

Mitigation: automatic failover remains disabled until witness/fencing P6 PASS.

### R4 — Home Center damages AD/DNS/DHCP

Mitigation: read-only adapters first; mutation modules require dedicated role-specific gates and rollback/evidence.

### R5 — Partial job produces unknown state

Mitigation: persisted checkpoints + idempotency + postcondition + `recovery_required`.

### R6 — Module supply-chain compromise

Mitigation: immutable content-addressed artifacts, checksum/signature, permission manifest, sandbox.

### R7 — Backup is not actually restorable

Mitigation: scheduled restore verification; unverified backup not shown as healthy.

### R8 — State-store limitations block P6

Mitigation: state/consensus ADR before automatic failover; no implicit datastore migration.

## 20. Итоговая sequence

`P1 accepted 0.1.0 → P2 Typed Actions/Reconcile → P3 Market → P4 Productized Second-Node Enrollment → P5 Drain/Remove → P6 Witness/Fencing + HA/DR Certification → P7 Productization`.

Ни один этап не имеет права обходить safety foundation предыдущего.
