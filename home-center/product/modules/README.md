# Modules / Market

Эта область предназначена для будущего модульного runtime Home Center и интеграции с Market catalog.

## Module lifecycle

`Discovered/Available → CompatibilityChecked → Planned → Installed → Healthy → Upgrading → Healthy` либо контролируемый `Rollback/Recovery`; удаление проходит отдельный dependency-aware workflow.

## Manifest contract

Модуль обязан декларировать минимум:

- ID, version, publisher/source;
- supported platform/version;
- required node capabilities;
- dependencies/conflicts;
- privileged actions/permissions;
- ports/protocols/firewall requirements;
- persistent data/storage;
- install/upgrade/remove semantics;
- readiness/liveness/health;
- backup/restore requirements;
- migration/rollback/recovery;
- cluster/HA behavior, если поддерживается.

## Market boundary

Catalog metadata не является разрешением на установку. Перед каждым lifecycle action Control Plane повторно выполняет policy, compatibility, dependency и topology gates.

## Cluster integration

При вводе новой ноды Home Center может автоматически установить на неё необходимые Market-модули только как часть рассчитанного Deployment Profile/Role Plan и после успешного compatibility gate.

Функциональный модульный runtime и catalog code создаются только после DEV admission.
