# Control Plane

Будущий runtime-компонент Home Center. До снятия `PREPARATION_ONLY` здесь хранится только описание границ.

## Responsibility boundary

- API и orchestration;
- Identity/RBAC integration;
- Desired/Actual State;
- Changes/Jobs;
- inventory/capabilities;
- topology/cluster membership;
- role placement;
- module lifecycle orchestration;
- health/audit aggregation;
- backup/recovery policy coordination.

## Не входит

- произвольный shell/SSH remote control;
- хранение plaintext production secrets в обычной БД;
- скрытые vendor/CI privileged channels;
- platform-specific side effects без Node Agent typed action.

## Planned internal modules

`api`, `identity`, `rbac`, `state`, `jobs`, `inventory`, `topology`, `modules`, `health`, `audit`, `backup`, `policy`.

Фактические каталоги/код создаются только после DEV admission и утверждения соответствующих contracts/ADR.
