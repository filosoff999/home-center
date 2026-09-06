# Home Center — техническое задание

**Версия документа:** 2.4
**Дата:** 2026-09-06
**Статус продукта:** `0.5.0 PRODUCTION ACCEPTED / 0.8.0 RELEASED_PRODUCTION_PENDING / 0.9.0 DEVELOPMENT ACTIVE`
**Execution epic:** `#1` — независимая разработка и двухузловой HM.DM deployment.
**Область действия:** весь отдельный repository `ControlCenterSoft/home-center`.

## 1. Назначение документа

Настоящее техническое задание является консолидированной нормативной спецификацией Home Center. Оно объединяет ранее принятые требования, текущий реестр `HC-*`, архитектуру, exact production baseline Home Center `0.4.3` и дальнейший план P2.4–P7.

Документ определяет требования к архитектуре, функциям, безопасности, интерфейсам, данным, кластерным сценариям, Market, резервному копированию, обновлению, тестированию, поставке и приемке. Для 0.9 нормативными дополнениями являются ADR-0011/0012, config schema v4, release-candidate schemas и exact upgrade runbook 0.8.0→0.9.0.

### 1.1. Иерархия источников

При конфликте применяется следующий приоритет:

1. явное решение владельца продукта;
2. принятый ADR;
3. versioned canonical contract/schema;
4. настоящее ТЗ;
5. `REQUIREMENTS.md` / roadmap / runbooks;
6. реализационные детали, если они не закреплены выше.

Изменение security/safety-инварианта требует ADR, обновления contracts/tests и трассируемости.

## 2. Текущий принятый baseline

Home Center `0.4.3` (`64f798ceae0b669cbac01b452c3cf4fd96070136`, artifact `b2dde6a51ec9450ddd23e802db50be2605c8854e804803ba014422878a02d3d4`) принят как server-side production baseline HM.DM:

- `dc01` — leader, `192.168.10.254`, HTTPS `:8443`, peer channel `:9443`;
- `dc02` — standby, `192.168.10.253`, HTTPS `:8443`, peer channel `:9443`;
- strict RFC 5280 Ed25519 peer PKI и TLS 1.3/mTLS между узлами;
- exact immutable release revision на обеих нодах;
- local SQLite state с integrity verification;
- HMAC-связанный tamper-evident audit chain;
- verified local backup на обеих нодах;
- GitHub-hosted CI/build;
- canary rollout `dc02 → dc01`;
- fail-closed mutation boundary;
- generic shell product API отсутствует;
- automatic failover/VIP/active-active отключены до появления witness/fencing и P6 certification;
- существующий HM.DM Domain SID сохраняется, Home Center deployment не должен неявно изменять Samba AD/DNS/Kerberos.

Этот cumulative baseline считается **принятым P1–P2.2 и server-side P2.3**. P2.4–P7 должны быть совместимы с ним либо содержать отдельный migration ADR и проверяемый upgrade path. Managed-client установка Web CA и реальная Windows/Android browser acceptance остаются открытой частью P2.3; Home Center не получает права неявно менять AD/GPO.

Версия `0.4.3` ввела отдельную browser-compatible Web PKI ECDSA P-256/SHA-256 для `:8443`, не меняя peer PKI и `:9443`; software rollout, Web rotation, restricted TLS, peer mTLS, Domain SID и DRS доказаны на обеих нодах. Версии `0.4.0`, `0.4.1` и `0.4.2` остаются quarantined по зафиксированным Web-profile/lock/marker причинам. Candidate `0.5.0` добавляет только инертный signed-channel verifier и не включает autonomous update.

## 3. Цель продукта

Home Center — local-first, cloud-independent центр управления домашней и малой серверной инфраструктурой с единым защищенным Web UI/API.

Система должна обеспечивать:

1. обзор узлов, сервисов, ресурсов, topology и health;
2. Desired State / Actual State и drift detection;
3. безопасные typed изменения через Change/Job workflows;
4. управление ролями, конфигурациями и поддерживаемыми инфраструктурными службами;
5. безопасное добавление новых узлов, включая автоматический ввод второго участника кластера;
6. безопасный Maintenance / Drain / Remove любой ноды;
7. module/Market lifecycle с compatibility/dependency/permission gates;
8. backup/restore/update/rollback;
9. audit/evidence для каждой критичной операции;
10. отказоустойчивые сценарии только там, где они доказаны acceptance tests.

## 4. Границы системы

### 4.1. В состав Home Center входят

- Control Plane/API;
- Web UI;
- Identity, authentication, sessions, API tokens;
- RBAC/policy;
- inventory/capability discovery;
- Desired/Actual State;
- Change/Job engine;
- Node Agent;
- bounded privileged helper/action adapters;
- deployment profiles;
- topology/cluster membership;
- role placement и failover policies;
- monitoring/health/events/incidents;
- audit;
- notifications;
- configuration/secrets boundary;
- backup/restore/update/rollback;
- Market/module runtime;
- network/storage/service adapters;
- diagnostics/support bundle;
- canonical contracts/migrations/release metadata/evidence.

### 4.2. Не входят в доверенный product API

- произвольный remote shell;
- скрытый permanent root/SSH vendor channel;
- обязательное внешнее облако;
- generic root-equivalent proxy;
- unrestricted Docker socket/module access;
- destructive wipe как часть обычного remove;
- автоматическое продолжение опасной операции при неизвестном health/quorum/compatibility/policy state.
- code/runtime/build/deploy dependency от Control Center и управление `dc01-control-agent.service`.

Production bootstrap/operator transport может существовать отдельно от продукта, но не должен становиться обходным product capability.

## 5. Архитектурные инварианты

1. **Local-first.** Core data, audit, control и UI работают без обязательного Internet.
2. **Fail-closed.** Неизвестное состояние блокирует mutation.
3. **Least privilege.** Core, agents, modules и users получают минимальные права.
4. **Single privileged path.** `Identity → RBAC → Desired State/Change → Job → Execution → Actual State → Health/Audit`.
5. **Contracts-first.** API/actions/manifests/topology/jobs определены schemas до privileged implementation.
6. **Idempotency.** Retry не создает duplicate membership/roles/credentials/side effects.
7. **Evidence-based success.** Отправленная команда ≠ успешная операция; success определяется postconditions.
8. **Reversibility.** Где возможно — checkpoint, rollback либо forward-recovery.
9. **Deterministic delivery.** Immutable revision/package/checksum/provenance.
10. **No false HA.** Две ноды сами по себе не означают automatic failover или split-brain safety.
11. **Failure/recovery by design.** Critical workflow без interruption/recovery tests не готов.
12. **Secrets never evidence.** Логи/issues/audit/support bundles не содержат plaintext secrets.

## 6. Технологический baseline и evolution policy

### 6.1. Текущая accepted implementation (`0.4.3`, server-side P2.3)

- Control Plane: Python `>=3.12`;
- local state: SQLite;
- Web: встроенный Web UI через versioned HTTPS API;
- node-to-node: TLS 1.3/mTLS;
- service manager: systemd;
- release CI/build: GitHub-hosted runners;
- contracts: OpenAPI 3.1 + JSON Schemas;
- release: immutable archive + SHA-256 manifest, exact `dc02 → dc01` acceptance.

P2.3 `0.4.3` расширяет baseline отдельной Web PKI, status/renewal API, fixed TLS activation/reconciliation helper actions и staged rotation. Server-side свойства production accepted и не меняют peer mTLS identity. P2.4 `0.5.0` добавляет read-only verifier по ADR-0007; production signing/trust/store и P2.5 updater пока отсутствуют.

### 6.2. Предыдущая Rust/PostgreSQL рекомендация

Ранее Rust и PostgreSQL были указаны как предпочтительная целевая реализация. После принятия production P1 это **не является основанием для переписывания работающего Core**. Любая миграция языка или primary datastore допускается только отдельным ADR с доказанной ценностью, compatibility plan, data migration, rollback и production acceptance.

Rust может применяться точечно для privileged helper/agent-компонентов, если это уменьшает trusted computing base и оправдано ADR.

PostgreSQL либо другой replicated/transactional backend может вводиться при необходимости для P4–P6, но SQLite допустим в leader-owned single-writer модели, пока система не заявляет автоматический multi-writer/HA state failover.

## 7. Control Plane

Control Plane отвечает за:

- API/orchestration;
- auth/RBAC/policy;
- inventory/capabilities;
- Desired/Actual State;
- Change/Job lifecycle;
- dependency graph;
- deployment profiles;
- topology/membership;
- role placement;
- module lifecycle;
- health/events/audit aggregation;
- backup/update coordination.

Core не должен требовать root для обычной работы.

## 8. Web UI

Web UI является клиентом versioned API и не выполняет privileged actions напрямую.

Обязательные свойства:

- русский язык по умолчанию;
- i18n-ready архитектура;
- responsive desktop/tablet/mobile;
- состояния `loading/empty/error/stale/unknown` различаются;
- опасная операция показывает impact, preflight, план, прогресс, result/recovery;
- ошибки имеют human-readable текст + correlation ID;
- фильтры/поиск/pagination для больших коллекций;
- скрытие UI элемента не заменяет server-side authorization.
- TLS health считается исправным только при валидной цепочке/hostname/сроке и точном Web profile `ecdsa-p256-sha256`; UI показывает profile и Web CA fingerprint без private-key data.

Основные разделы:

- Overview;
- Nodes;
- Services;
- Network;
- Storage;
- Automation/Jobs;
- Cluster/HA;
- Market;
- Events/Incidents;
- Audit;
- Users/Roles/Tokens/Sessions;
- Backup/Recovery;
- Updates;
- Settings/Policies;
- Diagnostics/Support.

## 9. Identity, authentication и sessions

- initial setup создает Owner, universal default password запрещен;
- passwords хранятся только adaptive hash с salt;
- local weak/compromised password blocklist;
- login rate limit/backoff;
- absolute + idle session timeout;
- re-auth для критичных действий;
- Secure/HttpOnly/SameSite cookies при HTTPS;
- CSRF protection browser mutations;
- active sessions list/revoke;
- API tokens: scoped, expiring, revocable, secret показывается полностью только при создании;
- service accounts не наследуют Owner privileges.

## 10. RBAC / policy

Минимальные роли:

- Owner;
- Administrator;
- Operator;
- User;
- Observer;
- Service Account.

Поддерживаются custom roles из атомарных `resource.action` permissions.

Scope минимум:

- instance;
- node group;
- node;
- resource type;
- concrete object.

Запрещены self-elevation и permission bypass через direct API.

## 11. API и canonical contracts

### 11.1. API

- version baseline `/api/v1`;
- OpenAPI 3.1 canonical contract;
- JSON UTF-8;
- strict Content-Type/body size;
- machine-readable reason code + correlation ID;
- no stack trace/SQL/secrets/internal path leakage;
- pagination;
- filter/sort allowlists;
- rate limits sensitive endpoints;
- idempotency keys mutations;
- ETag/object version/optimistic concurrency.

Mandatory endpoints include:

- `/health/live`;
- `/health/ready`;
- `/api/v1/info`;
- `/api/v1/session`;
- `/api/v1/audit`.

### 11.2. Canonical schema set

- NodeIdentity/Enrollment;
- NodeCapability;
- DeploymentProfile;
- RolePlacementPolicy;
- DesiredState/ActualState;
- Change/Job/StepResult;
- HealthStatus;
- ModuleManifest;
- ClusterMembership;
- DrainPlan/DrainResult;
- Event/Audit envelope;
- Backup/Restore metadata;
- Compatibility metadata.

Breaking contract change требует migration/compatibility policy и automated compatibility tests.

## 12. Desired State / Actual State

- Desired отдельно от Actual;
- drift explicitly visible;
- reconcile idempotent;
- stale/unknown не отображается healthy;
- concurrent changes versioned;
- remediation объясним пользователю;
- retry после interruption не создает duplicate side effects.

## 13. Change / Job lifecycle

Каждая mutation представлена typed Change/Job:

- immutable ID;
- type/schema version;
- initiator/auth context;
- reason/intent;
- target;
- idempotency key;
- preconditions/preflight;
- dependency/impact plan;
- checkpoints/steps;
- progress;
- normalized status/reason codes;
- audit/evidence links;
- timeout/cancellation;
- rollback/forward-recovery;
- postconditions.

States минимум:

`queued → running → waiting | succeeded | failed | cancelled | expired | recovery_required`.

Generic shell строка не является разрешенным action contract.

## 14. Node Agent и privileged actions — P2

Node Agent должен обеспечить:

- enrollment/trust;
- inventory/capabilities;
- execution только registered typed actions;
- local authorization scope check;
- pre/post conditions;
- evidence;
- health;
- reconnect/reconcile;
- agent upgrade lifecycle.

Privileged helper:

- отдельная system identity;
- fixed action namespace;
- typed arguments;
- caller identity verification;
- local IPC/socket;
- time/resource limits;
- environment scrubbing;
- path validation;
- systemd sandbox;
- no generic shell/root proxy.

## 15. Nodes / enrollment

Node record минимум содержит:

- immutable node ID;
- display name/type;
- addresses;
- OS/architecture;
- tags/groups;
- last contact;
- state/reason;
- agent/version;
- trust identity/fingerprint;
- capabilities;
- roles;
- topology membership;
- maintenance state.

States минимум:

`DISCOVERED`, `PREFLIGHTED`, `TRUSTED`, `ENROLLED`, `CAPABILITIES_KNOWN`, `CONFIGURING`, `REPLICATING`, `READY`, `DEGRADED`, `OFFLINE`, `UNKNOWN`, `MAINTENANCE`, `DRAINING`, `DECOMMISSION_READY`.

## 16. Automatic second-node bootstrap — HC-NODE-004 / P4

State machine:

`Discovered → Preflighted → Trusted → Enrolled → CapabilitiesKnown → DependenciesInstalled → RolesPlanned → Replicating → HealthValidated → Ready`.

Workflow обязан:

1. выполнить bounded trust/enrollment;
2. обнаружить capabilities;
3. проверить platform/network/storage/time/DNS/service/module compatibility;
4. построить dependency plan;
5. установить required components/modules;
6. применить network/cluster roles;
7. настроить replication;
8. включить health/monitoring;
9. применить backup policy;
10. настроить role-placement/failover metadata;
11. выполнить post-join production gate;
12. перевести в `READY` только при подтвержденном Actual State.

Повторный запуск после partial failure должен быть безопасным.

## 17. Maintenance / Drain / Remove — P5

Один workflow работает для любой ноды, включая active role/VIP owner.

State machine:

`MaintenanceRequested → Preflight → Draining → RolesRelocated → ReplicationValidated → QuorumValidated → ServicesValidated → MembershipRemoved → DecommissionReady`.

Обязательно:

- dependency/quorum/redundancy preflight;
- block service/data/quorum loss;
- role/VIP relocation where supported;
- replication finalization;
- Desired State/topology update;
- obsolete membership/trust cleanup;
- audit/evidence;
- deterministic interrupted-drain recovery;
- destructive wipe — отдельный typed workflow и отдельный explicit intent.

## 18. Cluster / HA — P4–P6

Для каждой role определяются:

- eligible nodes;
- active/standby or active/active semantics;
- affinity/anti-affinity;
- redundancy target;
- replicated state;
- leader/quorum rules;
- fencing/split-brain protection;
- health signal;
- failover trigger;
- failback policy;
- degraded-mode rules;
- backup/restore ordering.

### 18.1. Current two-node restriction

В текущем `dc01/dc02` profile automatic failover запрещен без независимого witness/fencing. UI/API не должны заявлять automatic HA до P6 certification.

### 18.2. Witness/fencing

Для automatic promotion требуется архитектурно независимый механизм quorum/fencing. Он не может быть симулирован одним из двух конфликтующих узлов.

## 19. Monitoring / health / services

Node metrics:

- uptime/load;
- CPU;
- RAM/swap;
- filesystem/mount/capacity;
- network interfaces/reachability;
- selected services;
- temperatures/SMART через bounded adapters.

Service checks:

- TCP;
- HTTP(S);
- process/systemd;
- typed custom adapters.

States:

`healthy/degraded/failed/unknown/disabled`.

Stale telemetry не считается healthy.

Server-side checks защищаются от SSRF/DNS rebinding, unsafe redirects и ambient proxy inheritance.

## 20. Infrastructure module families

Home Center должен поддерживать module/adapters для:

- Samba AD/DNS health/replication;
- DNS;
- DHCP и DHCP redundancy/coordination;
- file shares/storage;
- print service;
- PXE/boot service;
- VPN/network integrations;
- monitoring/notifications;
- backup targets;
- virtualization/container integrations только через bounded contracts.

Каждое семейство получает отдельный versioned contract и acceptance suite. Наличие family в ТЗ не означает, что все действия должны быть реализованы в одном milestone.

## 21. Network management

- inventory interfaces/IP/routes/DNS;
- declarative changes only;
- operation, способная оборвать management session, имеет preflight/impact warning/watchdog rollback;
- atomic apply либо auto-return к valid state;
- no arbitrary command fragments;
- network capability scopes задаются policy.

## 22. Storage / file resources

- filesystems/mounts/capacity;
- threshold alerts;
- mount health;
- network shares adapters;
- free-space preflight before backup/module install;
- storage paths normalized and access-controlled;
- destructive filesystem operations выделены в отдельные privileged actions.

## 23. Configuration / secrets

Configuration:

- schema/version;
- unknown critical fields fail validation/startup;
- UI/API change → validation + diff + audit;
- atomic save/transaction;
- previous valid state;
- rollback;
- restart-required marker.

Secrets:

- separate storage boundary;
- no plaintext return after save;
- references вместо копирования credentials;
- rotation lifecycle;
- dependency-aware deletion;
- no secrets in logs/diff/audit/evidence/issues.

## 24. Market / modules — P3

### 24.1. Module Manifest

Минимум:

- module ID/name/version;
- manifest schema version;
- compatible Core range;
- supported platform/architecture;
- required capabilities;
- permissions/privileged actions;
- network requirements;
- dependencies/conflicts;
- UI/API integration;
- persistent data;
- migrations;
- health check;
- backup/restore;
- update/rollback;
- uninstall data policy;
- checksums;
- publisher/signature metadata.

### 24.2. Artifact model

- immutable/content-addressed packages;
- staged version installation;
- checksum/signature verification;
- strict schema;
- path-safe extraction;
- dependency/conflict planner;
- incompatible Core/module rejected before mutation;
- lifecycle bound to append/tamper-evident audit.

### 24.3. Install lifecycle

`Acquire → Stage → Verify → Compatibility/Dependency Plan → Permission Review → Backup Point → Install Inactive → Migrate → Health → Activate/Commit → Audit`.

Upgrade/remove имеют аналогичный recovery contract.

### 24.4. Module isolation

- separate service identity;
- minimal filesystem access;
- read-only system paths default;
- declared network permissions;
- no master secrets;
- no RBAC bypass;
- resource limits;
- no Docker socket/root-equivalent default;
- disable without data deletion.

## 25. Events / audit / notifications

Event envelope:

- occurred/received time;
- source/type/severity/resource;
- normalized message;
- correlation ID;
- dedup key;
- retention class.

Audit:

- actor;
- UI/API/system channel;
- action/object/result/time;
- safe session/token/source reference;
- safe before/after;
- policy denial reason;
- linked job/change;
- tamper-evident/append semantics;
- audit cleanup itself produces audit evidence.

Notifications:

- local feed mandatory;
- external channels optional adapters;
- filtering/dedup/suppression/maintenance suppression/escalation;
- channel secrets stored as secrets;
- channel test audited.

## 26. Backup / restore / DR

Backup scope:

- Control Plane state;
- configuration;
- protected secrets according to key model;
- module manifests/state;
- module data per backup contract;
- version/schema/compatibility metadata.

Requirements:

- manual + scheduled;
- backup ID/checksum/status;
- free-space preflight;
- atomic completed backup publication;
- retention;
- local target export;
- restore validation + compatibility gate;
- protective snapshot before restore where possible;
- post-restore migration/readiness;
- failure does not silently accept partial state;
- periodic automated restore verification;
- cluster-aware restore order.

Current P1 local backup verification remains mandatory regression coverage.

## 27. Update / rollback

- trusted immutable package;
- version/architecture/schema/module compatibility preflight;
- checksum/provenance;
- backup/rollback material before mutation;
- atomic or side-by-side switch;
- migration ledger;
- live/ready after switch;
- automatic rollback where safe;
- irreversible migration requires explicit intent + verified backup;
- incompatible modules detected before switch;
- release identity visible in UI/API.

Canary order for HM.DM remains `dc02 → dc01` unless deployment ADR changes it.

### 27.1. P2.3 Web TLS upgrade contract

- historical admitted source was exact accepted `0.3.0` or deployed `0.4.2`; exact target `0.4.3` is now server-side accepted, while `0.4.0`, `0.4.1` and `0.4.2` remain quarantined for new rollout;
- `:8443` uses TLS 1.2+ and, after rotation, an exact ECDSA P-256 leaf signed ECDSA-with-SHA-256 by the independent Web CA;
- `:9443` remains TLS 1.3 with `CERT_REQUIRED`, the existing Ed25519 peer CA and existing node identities;
- `/etc/home-center/pki/web-ca/ca.key` exists only on `dc01`; public `ca.crt` exists on both nodes; the Web CA is provisioned before installing a config that requires `web_ca`;
- Web candidates/releases/current live under `/etc/home-center/pki/web`; version rollback never deletes this persistent PKI state;
- candidate and release staging are bound to a durable root-owned operation marker and exact file digests; the release marker is `root:home-center:0600`, matching the capability-free helper primary group while granting no group access; same-directory publication and current/release/software symlink switches are directory-fsynced;
- activation classifies and verifies the previous certificate chain before the atomic switch, including legacy `0.3.0` and possible quarantined `0.4.0` rollback material;
- interrupted activation is reconciled only from marker-owned state; durable-current/listener divergence is restarted and the exact live fingerprint is proved before a helper recovery latch can clear;
- cluster deployment journals persist exact source releases and pre-mutation peer CA/node certificate/public-key fingerprints for both nodes; a rollback becomes terminal only after source, readiness, healthy overview/roles and bidirectional peer-mTLS proof;
- `dc01` promotion is blocked until `dc02` readiness, exact version/revision/digest, restricted-signature TLS 1.2 and 1.3, peer-mTLS and a 30-second soak gate pass;
- final acceptance additionally requires managed-browser hostname/chain trust, unchanged peer public fingerprints, Samba SID/DRS health and proof of no AD/DNS/DHCP or Control Center mutation.

Continuous update policy must consume only an explicitly promoted immutable stable release, verify signature/provenance/digest, persist two-node checkpoints and quarantine terminally bad digests. Discovery of a newer build alone is never authority to deploy it.

### 27.2. P2.4 signed stable release-channel contract

- exact DSSE v1 payload type: `application/vnd.home-center.release-ledger.v1+json`;
- canonical ASCII JSON bytes, duplicate/float/unknown-field rejection and bounded payload/signature/key/event sizes;
- fixed ECDSA P-256/SHA-256 verification; key ID is SHA-256 of DER SPKI; only unique active keys count toward policy threshold;
- private signing keys remain outside repository, artifacts, PR jobs, `dc01`, `dc02`, logs and evidence and are separate from Web/peer/SSH/Control Center credentials;
- immutable record binds version, full revision, archive SHA/bytes, internal manifest SHA, content-addressed object key, repository/ref/workflow/run provenance and exact HM.DM acceptance evidence;
- full signed ledger supports only `unlisted→stable`, `unlisted→quarantined`, `stable→superseded|quarantined` and `superseded→quarantined`; quarantine is terminal;
- replacing stable is one atomic event that supersedes the old record and promotes one strictly greater semantic version; version, revision and artifact identities cannot fork;
- signed generation/freshness plus local sequence/head checkpoint reject rollback, replay, equivocation, rewritten history, stale head and clock rollback;
- content-addressed artifact is opened without symlink following and rechecked for exact bytes, owner/type, bounded tar expansion, complete internal manifest and exact `VERSION`/`REVISION`;
- verifier returns immutable identity and has no network, deploy, service-control or checkpoint-write authority;
- `--bootstrap-no-checkpoint` is one-time explicit trust bootstrap only; later production decisions require the persisted floor owned by P2.5;
- stale/unavailable channel blocks a new mutation but never stops the currently accepted runtime.

The transitional `0.5.0` rollout from exact `/opt/home-center/releases/0.4.3-64f798ceae0b-b2dde6a51ec9` uses the existing audited artifact channel because target-contained trust is circular. It installs no production trust key or updater. Signed-channel activation requires a separate Home Center signing-key ceremony, root-owned trust bootstrap on both nodes and durable content-addressed storage.

## 28. Security requirements

Web/API:

- Host validation;
- DNS rebinding protection;
- CSRF;
- CSP/frame-ancestors/nosniff/Referrer-Policy;
- no wildcard CORS default;
- request/upload size limits;
- path traversal protection;
- SSRF protection;
- trusted reverse-proxy model;
- sanitize external messages;
- rate limit sensitive endpoints;
- no unconditional forwarded-header trust.
- separate Web and peer trust anchors; public Web CA endpoint never publishes the peer CA;
- reject Web certificates that are Ed25519, RSA, non-P-256, not SHA-256-signed, clientAuth-enabled or missing exact node/VIP/IP SANs;
- serialize only certificate metadata, profile validity and public fingerprints, never private keys.

Supply chain:

- locked dependencies;
- vulnerability/license checks;
- artifact integrity;
- immutable CI references where applicable;
- provenance/evidence;
- no production secrets in untrusted PR jobs;
- no use of AI Development Infrastructure as Home Center build/runtime dependency.

Threat boundaries минимум:

- browser↔server;
- server↔state store;
- server↔privileged helper;
- Control Plane↔Node Agent;
- node↔cluster;
- core↔module;
- module↔network/filesystem;
- backup↔restore;
- update package↔installer;
- CI↔release artifact;
- operator deployment channel↔production.

## 29. Performance / reliability

Targets:

- local read UI/API p95 ≤ 500 ms under normal reference load;
- liveness p95 ≤ 100 ms;
- readiness without long diagnostics;
- ≥10 concurrent interactive users;
- ≥100 000 local events without full in-memory load;
- job queue ≥1 000 records;
- one offline node does not block others;
- process restart preserves persistent state;
- crash leaves unfinished jobs in deterministic non-success state;
- stale data never shown healthy.

## 30. Logging / diagnostics

- structured logs: time/level/component/correlation ID;
- ERROR/WARN/INFO/DEBUG;
- DEBUG off default;
- secret field masking;
- admin diagnostic summary;
- support bundle only by explicit action;
- automatic secret scrubbing;
- normalized error classes/reason codes;
- DB/state/jobs/modules/cluster/migrations health without credentials.

## 31. System deployment

Reference Linux layout:

- `/usr/lib/home-center` — binaries/runtime;
- `/etc/home-center` — configuration;
- `/var/lib/home-center` — persistent state;
- `/var/cache/home-center` — cache;
- `/var/backups/home-center` — local backups.
- `/etc/home-center/pki/web-ca` — persistent Web CA (`ca.key` only on `dc01`);
- `/etc/home-center/pki/web` — persistent fingerprint-addressed Web leaf lifecycle.

Services run under dedicated non-interactive identities with systemd hardening: `NoNewPrivileges`, bounded writable paths, applicable `ProtectSystem/ProtectHome`, crash-loop controls and secret-safe logging.

Listeners bind only to explicitly configured management addresses/trusted LAN. Public Internet exposure is not default.

## 32. HM.DM authoritative deployment profile

Current pilot/production topology:

- `dc01` `192.168.10.254` — primary writable Samba AD DC, Home Center leader;
- `dc02` `192.168.10.253` — secondary writable Samba AD DC, Home Center standby;
- Domain SID must remain unchanged;
- Home Center modifications of Samba/DNS/DHCP/file roles are allowed only through later certified typed adapters;
- Home Center is independent from Control Center and its deployment never changes `dc01-control-agent.service`;
- build/test compute — GitHub-hosted runners;
- AI Development Infrastructure is not used;
- deployment uses audited external operator transport;
- rollout by immutable artifact;
- rollback material before material mutation.

## 33. Testing

Mandatory levels:

- unit;
- contract/schema compatibility;
- integration;
- migration clean+upgrade when persistent schema affected;
- HTTP/API;
- Web smoke/E2E;
- auth/RBAC negative;
- CSRF;
- IDOR;
- SSRF;
- DNS rebinding;
- path traversal/archive extraction;
- secret leakage;
- concurrency/race;
- agent/privileged boundary;
- installer/update/rollback;
- real TLS 1.2/TLS 1.3 handshakes with browser-representative signature algorithms that omit Ed25519;
- Web/peer trust-anchor separation, Web CA key absence on `dc02`, concurrency lock and previous-chain rollback classification;
- Market install/upgrade/backup/restore/remove;
- enrollment/re-enrollment;
- second-node bootstrap;
- failover/failback;
- drain/remove and blocked unsafe cases;
- interruption/recovery at checkpoints;
- backup/restore verification;
- package integrity/provenance;
- degraded-node isolation;
- split-brain/fencing tests before automatic failover.

## 34. CI / release gates

Every PR after DEV admission:

1. formatting/lint/static checks;
2. unit tests;
3. schemas/OpenAPI validation;
4. compatibility tests;
5. dependency vulnerability/license checks;
6. secret scan;
7. affected integration/security/failure tests;
8. Web smoke when affected;
9. package/build validation for release changes.

Release pipeline:

Transitional manual pipeline:

`Source commit → CI build-twice → exact artifact/checksum → canary dc02 → postconditions → dc01 → cluster acceptance → evidence`.

Target stable-channel pipeline after P2.4/P2.5 activation:

`Exact main CI artifact → durable content-addressed copy → production acceptance → immutable record → protected DSSE promotion → independent node verification → persisted dc02 canary/soak → dc01 → acceptance checkpoint`.

Production deploy credentials are unavailable to untrusted PR jobs.

## 35. Acceptance / Definition of Done

Capability готова только при наличии:

1. linked `HC-*` requirement;
2. ADR where needed;
3. versioned contract;
4. implementation;
5. unit/contract/integration tests;
6. security-negative tests according to risk;
7. failure/interruption/recovery tests;
8. health/observability evidence;
9. backup/restore impact assessment;
10. compatibility/migration evidence;
11. documentation/runbook;
12. release identity/checksum/provenance;
13. rollback/forward-recovery;
14. evidence without secrets/customer data.

## 36. System acceptance criteria

Home Center satisfies this specification when:

1. clean install/upgrade reproducible;
2. no universal default credentials;
3. server-side RBAC cannot be bypassed;
4. sessions/tokens can be revoked;
5. Desired/Actual/drift/reconcile work idempotently;
6. health distinguishes ready/degraded/offline/unknown/stale/maintenance;
7. typed jobs + audit/evidence exist for mutations;
8. generic shell product API does not exist;
9. configuration validates/diffs/rolls back;
10. secrets do not leak to UI/API/log/audit/support/evidence;
11. Market verifies manifest/integrity/compatibility/dependencies/permissions before activation;
12. broken module can be disabled without Core outage;
13. system update uses preflight/backup/readiness/rollback;
14. backup actually restores and validates;
15. audit captures actor/action/object/time/result;
16. security-negative suite does not bypass controls;
17. failure of one node does not block others;
18. restart preserves state/history;
19. damaged package rejected before mutation;
20. second-node workflow reaches `READY` only after replication/health gates;
21. unsafe drain/remove is deterministically blocked;
22. automatic failover is impossible before witness/fencing certification;
23. claimed HA/backup behavior is supported by scenario evidence.

## 37. Requirements to delivery

- immutable release package;
- SHA-256/checksum manifest;
- build/provenance metadata;
- signed release record, DSSE ledger decision and public trust policy where the stable channel is active;
- installer/updater;
- safe config example;
- systemd units/policies;
- state/database migrations when applicable;
- Web assets;
- canonical OpenAPI/schemas;
- Node Agent/helper package where applicable;
- ModuleManifest validator/tooling;
- backup/restore/rollback procedures;
- runbooks;
- compatibility matrix;
- release acceptance evidence bundle.

## 38. Traceability

- Core/Desired/Jobs: `HC-CORE-001..003`;
- RBAC/Audit/Secrets/destructive gates: `HC-SEC-001..004`;
- Nodes/enrollment: `HC-NODE-001..005`;
- Maintenance/decommission: `HC-NODE-010..014`;
- Cluster/HA: `HC-HA-001..004`;
- Market: `HC-MOD-001..003`;
- Observability: `HC-OBS-001..002`;
- Backup: `HC-BKP-001..003`;
- API/contracts: `HC-API-001`, `HC-CONTRACT-001`;
- Testing: `HC-TEST-001..003`.
- Release/supply chain: `HC-REL-001..005`.

## 39. Реализационный приоритет

Безопасность и сохранность данных → корректность/обратимость → наблюдаемость/evidence → надежность → удобство → расширяемость.

Любое решение, упрощающее UI или ускоряющее delivery ценой обхода RBAC, typed jobs, compatibility/quorum gates, audit или recovery, не соответствует настоящему ТЗ.
