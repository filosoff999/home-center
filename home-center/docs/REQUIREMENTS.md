# Реестр требований Home Center

Статус: действующий нормативный baseline. Requirement ID сохраняются при дальнейшей декомпозиции в issues, ADR, contracts и tests.

## Core / Control Plane

### HC-CORE-001 — Desired State
Home Center должен хранить и применять Desired State отдельно от Actual State, обнаруживать drift и выполнять reconcile идемпотентно.

**Acceptance:** повторное применение неизменённого Desired State не создаёт побочных изменений; drift видим и имеет объяснимый remediation path.

### HC-CORE-002 — Change/Job lifecycle
Любая изменяющая операция должна иметь typed Change/Job с инициатором, основанием, preflight, шагами, состоянием, результатом, evidence и recovery metadata.

### HC-CORE-003 — Dependency graph
Control Plane должен понимать зависимости между нодами, ролями, модулями, сетями, storage, secrets, backup и health checks до выполнения изменения.

## Identity / Security

### HC-SEC-001 — RBAC
Все privileged operations должны проходить централизованный RBAC/policy gate и локальную проверку допустимого action scope на Node Agent.

### HC-SEC-002 — Audit
Все изменения, security-sensitive read actions и lifecycle операций должны иметь трассируемый audit trail без утечки секретов.

### HC-SEC-003 — Secrets
Секреты должны храниться и передаваться через специализированный secrets boundary; обычные logs/events/issues/evidence не должны содержать plaintext secrets.

### HC-SEC-004 — Destructive action gate
Удаление данных, wipe, irreversible migration и эквивалентные операции требуют усиленного preflight и отдельного явного подтверждаемого intent.

### HC-SEC-005 — External request provenance
Внешний запрос допускается только через явно настроенный trusted gateway с однозначными client/proto/host данными. Неизвестный, составной или подменённый provenance должен отклоняться до authentication/mutation processing; внутренние operational endpoints не публикуются наружу.

## Nodes / Enrollment

### HC-NODE-001 — Node enrollment
Home Center должен поддерживать безопасный ввод нового сервера/узла в доверенный контур без ручного конструирования конечной конфигурации на каждой ноде.

### HC-NODE-002 — Capability discovery
После enrollment система должна автоматически определять OS, hardware, storage, network, virtualization и доступные service capabilities в versioned schema.

### HC-NODE-003 — Compatibility gate
Перед установкой компонентов/ролей система должна проверять совместимость ноды с текущим deployment profile, версиями модулей, dependencies и topology constraints.

### HC-NODE-004 — Automatic second-node bootstrap
При выборе роли «второй участник кластера» Home Center должен автоматически:

- обнаружить capabilities;
- проверить compatibility;
- установить необходимые системные компоненты и Market-модули;
- применить сетевые/кластерные роли;
- настроить требуемую репликацию;
- подключить health checks и monitoring;
- подключить backup policy;
- настроить failover/role placement;
- завершить workflow только после post-condition gate.

### HC-NODE-005 — Idempotent re-enrollment/reconcile
Повторный запуск enrollment/reconcile после частичного сбоя не должен создавать дубликаты membership, roles, credentials или destructive side effects.

## Maintenance / Decommission

### HC-NODE-010 — Universal Maintenance/Drain/Remove
Home Center должен предоставлять единый workflow вывода из эксплуатации любой ноды независимо от её порядка добавления и текущих активных ролей.

### HC-NODE-011 — Quorum/service-loss protection
Система должна запрещать drain/remove, если операция приведёт к потере обязательного сервиса, недоступности данных, нарушению redundancy policy или quorum.

### HC-NODE-012 — Role/VIP relocation
Перед удалением ноды система должна автоматически переносить/переключать поддерживаемые роли, ownership и VIP согласно placement/failover policy.

### HC-NODE-013 — Membership cleanup
После успешного drain система должна корректно удалить cluster membership, obsolete trust references и topology state, сохранив audit/evidence.

### HC-NODE-014 — Destructive wipe separation
Очистка локальных данных выведенной ноды не должна быть неявной частью remove; destructive wipe оформляется отдельной контролируемой операцией.

## Cluster / HA

### HC-HA-001 — Role placement policy
Для каждой кластерной роли должны быть определены допустимые ноды, redundancy, affinity/anti-affinity и degraded-mode правила.

### HC-HA-002 — Replication health
Реплицируемые роли должны иметь измеримый replication health и production gate перед failover/remove/upgrade.

### HC-HA-003 — Failover
Поддерживаемые роли должны иметь детерминированный failover path, health trigger и post-failover validation.

### HC-HA-004 — Split-brain prevention
Для ролей с leader/quorum semantics должны быть определены fencing/quorum правила, исключающие одновременную запись конфликтующих владельцев.

## Modules / Market

### HC-MOD-001 — Versioned module manifest
Каждый модуль должен иметь versioned manifest с platform/capability requirements, dependencies, conflicts, privileges, networking, data, health, backup и lifecycle contracts.

### HC-MOD-002 — Install/upgrade/remove lifecycle
Установка, обновление и удаление модуля должны быть typed jobs с preflight, post-condition и recovery semantics.

### HC-MOD-003 — Dependency safety
Удаление/обновление модуля запрещается либо переводится в согласованный migration plan, если от него зависят обязательные сервисы или другие модули.

## Observability

### HC-OBS-001 — Unified health
Control Plane должен агрегировать readiness/liveness/dependency health, drift и last reconcile по нодам и модулям.

### HC-OBS-002 — Action evidence
Каждый значимый job должен сохранять достаточное evidence для подтверждения результата без записи секретов.

## Backup / Recovery

### HC-BKP-001 — Backup contract
Каждый stateful модуль/роль обязан декларировать backup scope, consistency requirements, retention class и restore procedure.

### HC-BKP-002 — Restore verification
Backup не считается рабочим только по факту создания; должны существовать периодические restore/verification gates.

### HC-BKP-003 — Cluster-aware recovery
Backup/recovery должен учитывать membership, replicated state, leader/quorum и порядок восстановления зависимостей.

## Contracts / Compatibility

### HC-API-001 — Versioned API
Публичные и agent-facing API должны иметь versioned canonical contracts и backward-compatibility policy.

### HC-CONTRACT-001 — Schema evolution
Capability, Desired State, events, module manifests и job schemas должны иметь правила versioning/migration и compatibility tests.

## Testing / Release gates

### HC-TEST-001 — Contract tests
Изменение опубликованного canonical contract не допускается без автоматических compatibility/contract tests и явного versioning решения.

### HC-TEST-002 — Failure/recovery tests
Критические lifecycle workflows — enrollment, upgrade, failover, drain/remove, backup/restore — должны иметь negative и interruption tests.

### HC-TEST-003 — Production gate
Capability объявляется готовой только при наличии acceptance evidence по happy path, failure/recovery, security и observability.

### HC-TEST-004 — Release-candidate E2E
Release candidate обязан пройти детерминированную цепочку artifact build-twice → runtime/API → authentication → backup/restore verification → exact acceptance validation и отдельную live двухузловую production acceptance после появления exact artifact.

## Release / Supply Chain

### HC-REL-001 — Explicit signed promotion
Новый build не является authority для обновления. Допускается только release с явным `stable`-решением, подписанным отдельным Home Center release-signing credential и связанным с exact source, artifact, provenance и production acceptance.

### HC-REL-002 — Durable immutable artifact
Promoted artifact должен находиться в Home Center-owned content-addressed storage независимо от срока хранения CI artifact. Digest, byte count, internal manifest и `VERSION`/`REVISION` проверяются до любой mutation.

### HC-REL-003 — Append-only channel state
Состояния `stable`, `superseded` и `quarantined` фиксируются в подписанном append-only ledger. Quarantine терминален для автономной установки; старое, но корректно подписанное состояние не может обойти более новый локальный checkpoint.

### HC-REL-004 — Separate trust and credentials
Release trust root и signing credentials принадлежат только Home Center и не переиспользуют Web CA, peer CA, SSH, Control Center или обычные PR/CI credentials. Private signing keys отсутствуют на управляемых нодах и в artifacts/evidence.

### HC-REL-005 — Verification/deployment separation
Проверка канала выдаёт только immutable `VerifiedStableRelease`. Сетевой fetch, root-owned admission, `dc02 → dc01` rollout, soak, retry и rollback реализуются отдельным persisted update workflow и не входят в verifier.

### HC-REL-006 — Closed release-candidate acceptance
Release-candidate evidence должно быть bounded closed-schema документом, связанным с exact predecessor/candidate identities и доказывающим `dc02 → dc01`, reverse rollback `dc01 → dc02`, backup/restore verification, двухузловую parity, сохранность Web/peer PKI, Domain SID/DRS и отсутствие запрещённых mutations. Evidence не является release authority и не заменяет подписанное stable-решение.

## Трассируемость

Каждая будущая issue/PR должна ссылаться минимум на один `HC-*` Requirement ID. Новое архитектурное отступление оформляется ADR, а изменение поведения интерфейса — versioned contract.
