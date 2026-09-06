# Node Agent

Node Agent — будущий минимально привилегированный локальный исполнитель Home Center на управляемом узле.

## Обязанности

- enrollment/trust bootstrap;
- inventory и capability discovery;
- локальная валидация typed actions;
- выполнение platform adapters;
- pre/post-condition checks;
- reconcile Desired State;
- health/metrics/log evidence;
- resumable/retriable job execution;
- безопасное обновление агента.

## Security invariants

- нет публичного generic shell action;
- least privilege для каждого adapter/action;
- action scope проверяется локально повторно;
- credentials/secrets не попадают в обычный output;
- job имеет correlation/idempotency identity;
- потеря связи с Control Plane не должна приводить к бесконтрольному продолжению destructive operation.

## Planned adapter domains

`system`, `packages`, `services`, `network`, `storage`, `containers`, `virtualization`, `cluster`, `backup`, `modules`.

Фактические adapters создаются только после утверждения typed contracts и security model.
