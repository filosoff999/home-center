# Архитектура Home Center

## 1. Архитектурная модель

Базовая модель Home Center — modular control plane с несколькими логически разделёнными runtime-компонентами и versioned contracts. Микросервисное дробление, внешний broker и Kubernetes не являются обязательными без измеренной необходимости.

Управляющая цепочка:

`Identity → RBAC → Desired State / Change → Job → Execution → Actual State → Health / Audit`

Все UI, API, automation и будущие AI-функции обязаны использовать эту цепочку и не создавать отдельный privileged path.

## 2. Компоненты

### 2.1 Control Plane

Ответственность:

- API и orchestration;
- inventory узлов и capabilities;
- Desired State / Actual State;
- Change/Job lifecycle;
- dependency graph;
- deployment profiles;
- cluster membership и role placement;
- module lifecycle;
- policy, health и audit aggregation.

Control Plane не должен считать операцию успешной только по факту отправки команды: финальный статус определяется post-condition/health evidence.

### 2.2 Web UI

Web UI является клиентом versioned API и не выполняет privileged действия напрямую. Критические операции требуют явного отображения preflight, impact, progress, result и recovery state.

### 2.3 Node Agent

Node Agent — локальный исполнитель на управляемой ноде. Минимальные обязанности:

- identity/trust bootstrap;
- hardware/OS/service inventory;
- capability discovery;
- выполнение только разрешённых typed actions;
- локальные pre/post checks;
- health/metrics/log evidence;
- безопасное обновление агента;
- reconnect/reconcile после временной потери Control Plane.

Запрещён общий «выполнить произвольную shell-команду» как публичный product API.

### 2.4 Module Runtime / Market

Каждый модуль должен иметь versioned manifest с:

- module ID/version;
- supported platforms;
- required capabilities;
- dependencies/conflicts;
- permissions/privileged actions;
- ports/network requirements;
- persistent data locations;
- install/upgrade/remove hooks;
- health checks;
- backup/restore contract;
- rollback/recovery contract.

### 2.5 Contracts

Canonical contracts хранятся в `home-center/contracts/` и включают OpenAPI, event schemas, module manifests, node capability schemas, desired-state schemas и compatibility metadata.

### 2.6 Release Channel Verifier

P2.4 verifier — отдельная offline/read-only граница между supply-chain metadata и будущим updater. Он принимает только явно переданные local DSSE envelope, public trust policy, anti-replay checkpoint и content-addressed artifact root; проверяет подписи, canonical ledger, transitions, freshness, history и exact artifact; возвращает immutable `VerifiedStableRelease` либо стабильный rejection code.

Verifier не выполняет network fetch, не сохраняет checkpoint, не вызывает installer/systemd/SSH и не выбирает «последнюю» версию. P2.5 обязан независимо реализовать root-owned admission, persisted state machine и node-local revalidation.

## 3. Состояние и данные

Минимально разделяются:

- **Desired State** — что должно быть;
- **Actual State** — что подтверждено на нодах;
- **Inventory/Capabilities** — обнаруженные свойства;
- **Jobs/Changes** — транзакционная история управляющих операций;
- **Audit** — кто/что/когда/почему изменил;
- **Secrets references** — ссылки/идентификаторы, а не секреты в обычном event/log payload;
- **Backup metadata** — restore points, scope, verification state.
- **Release ledger/checkpoint** — подписанная глобальная история channel state и локальный root-owned anti-replay floor; не смешиваются с application Desired State.

## 4. Enrollment и ввод нового узла

Целевой state machine:

`Discovered → Preflighted → Trusted → Enrolled → CapabilitiesKnown → DependenciesInstalled → RolesPlanned → Replicating → HealthValidated → Ready`

Для второго участника существующего кластера Home Center должен автоматически:

1. принять новый сервер/узел через enrollment в доверенный контур;
2. обнаружить OS/hardware/network/service capabilities;
3. проверить compatibility с текущим deployment profile;
4. установить необходимые системные компоненты и Market-модули;
5. назначить сетевые и кластерные роли;
6. настроить требуемую репликацию;
7. подключить health checks, monitoring и backup;
8. настроить failover/role-placement зависимости;
9. завершить ввод только после production gate по фактическому состоянию.

Повторный запуск workflow должен быть безопасным и идемпотентным.

## 5. Maintenance / Drain / Remove

Один workflow должен работать для любой ноды — первой, второй, текущего владельца VIP/ролей или обычного участника.

Целевой state machine:

`MaintenanceRequested → Preflight → Draining → RolesRelocated → QuorumValidated → ServicesValidated → RemovedFromCluster → DecommissionReady`

Обязательные правила:

- запрет операции, если она приведёт к потере обязательного сервиса, данных или quorum;
- автоматическая миграция/переключение поддерживаемых ролей и VIP;
- проверка репликации до удаления membership;
- обновление Desired State/topology;
- сохранение audit/evidence;
- отдельное явное решение для destructive wipe локальных данных.

## 6. High Availability

HA не сводится к наличию двух серверов. Для каждой роли должны быть определены:

- placement policy;
- active/standby или active/active semantics;
- replicated state;
- quorum/leader rules, если применимо;
- health signal;
- failover trigger;
- failback policy;
- backup/restore behavior;
- split-brain prevention;
- degraded-mode behavior.

## 7. Наблюдаемость

Каждая управляемая capability должна предоставлять как минимум:

- readiness;
- liveness;
- configuration/state drift;
- error reason;
- last successful reconcile;
- metrics/log references;
- dependency health.

## 8. Security boundaries

- trust bootstrap должен быть ограниченным по времени/назначению;
- RBAC применяется до постановки privileged job;
- Node Agent проверяет action scope повторно локально;
- секреты не попадают в обычные логи, issues, telemetry и evidence;
- destructive actions имеют усиленный policy gate;
- audit append-only semantics должны быть предусмотрены контрактом;
- vendor/CI контуры не получают скрытого постоянного root/SSH доступа к клиентской установке.
- release-signing trust отделён от Web/peer PKI, CI и Control Center; private signing keys отсутствуют на нодах.

## 9. Failure/recovery как часть архитектуры

Для каждой изменяющей операции должны быть определены:

1. preconditions;
2. checkpoints;
3. idempotency key / retry semantics;
4. postconditions;
5. rollback или forward-recovery;
6. timeout/cancellation behavior;
7. evidence, позволяющее доказать результат.

Функция не считается архитектурно завершённой без проверяемого failure/recovery пути.
