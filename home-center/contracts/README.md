# Home Center Contracts

`contracts/` — canonical source для machine-readable границ Home Center. После допуска к разработке generated code может создаваться из contracts, но не должен становиться альтернативным источником истины.

## Планируемая структура

```text
contracts/
├── openapi/                  # Control Plane API
├── agent/                    # Control Plane ↔ Node Agent protocol
├── capabilities/             # node capability schemas
├── actions/                  # typed action registry/request/result
├── desired-state/            # desired/actual state schemas
├── jobs/                     # Change/Job/action/result schemas
├── events/                   # audit/domain/health events
├── modules/                  # Module Manifest и lifecycle contracts
├── deployment-profiles/      # topology/role/placement declarations
├── health/                   # readiness/liveness/dependency schemas
└── backup/                   # backup/restore metadata contracts
```

Каталоги создаются фактическими schema-файлами после утверждения соответствующего контракта; пустые placeholders не считаются контрактом.

## Обязательные правила

1. Каждый контракт имеет явную версию.
2. Breaking change требует ADR/migration/compatibility plan.
3. Неизвестные поля обрабатываются согласно явно зафиксированной forward-compatibility policy.
4. Security-sensitive поля маркируются и не должны автоматически попадать в logs/telemetry/evidence.
5. Privileged action описывается typed schema; generic shell command не является допустимым public product contract.
6. Compatibility проверяется автоматически contract tests после снятия `PREPARATION_ONLY`.
7. Contract change в PR должен ссылаться на `HC-*` Requirement ID.

## Первый обязательный набор schemas

- `NodeCapability`;
- `NodeIdentity/Enrollment`;
- `DeploymentProfile`;
- `RolePlacementPolicy`;
- `DesiredState` / `ActualState`;
- `Change` / `Job` / `StepResult`;
- `HealthStatus`;
- `ModuleManifest`;
- `BackupContract` / `RestorePoint`;
- `ClusterMembership`;
- `DrainPlan` / `DrainResult`.

## Definition of done для контракта

Контракт считается готовым к реализации, когда определены:

- schema и semantic invariants;
- versioning;
- validation rules;
- compatibility behavior;
- security/data classification;
- positive/negative examples;
- migration/recovery semantics;
- тестовые fixtures.

## P2.1 active contracts

- `actions/action-registry.v1.schema.json` — immutable executable action catalog;
- `actions/action-request.v1.schema.json` — strict request envelope with local target and idempotency key;
- `actions/action-result.v1.schema.json` — persisted terminal job response;
- `jobs/job.v1.schema.json` — lifecycle, steps, evidence and recovery model.

P2.1 admits only `service.state.read.v1` for Home Center-owned allowlisted units. A registry entry without a matching certified executor fails startup.
