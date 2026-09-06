# Control Plane

Исполняемый runtime-компонент Home Center. Read-only API, topology/inventory, audit/backup, bounded typed action foundation, Web TLS lifecycle и P2.4 release verifier реализованы; расширенные Desired State, persisted Changes/Jobs, RBAC и module orchestration остаются плановыми.

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

## Internal modules

Реализованы `api`, `auth`, `actions`, `inventory`, `runtime`, `store`, `backup`, `helper_client`, TLS lifecycle и `release_channel`. Плановые области: полноценные `rbac`, Desired/Actual `state`, persisted `jobs`, `modules` и policy orchestration.

Новая mutation capability добавляется только после отдельного contract/ADR, deny-by-default policy, failure/recovery tests и production gate.
