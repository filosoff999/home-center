# Home Center Security

Этот каталог предназначен для threat model, trust model, RBAC/policy, secrets, audit и security acceptance Home Center.

## Security invariants

- default deny для privileged operations;
- least privilege для пользователей, services и Node Agent adapters;
- short-lived/bounded enrollment trust;
- separation of control-plane identity, node identity и human identity;
- no plaintext secrets в Git, logs, issues, telemetry или generic evidence;
- no hidden vendor/CI root channel;
- typed privileged actions вместо generic remote shell API;
- immutable/tamper-evident audit semantics должны быть предусмотрены архитектурой;
- destructive actions имеют повышенный policy/confirmation gate;
- update/module artifacts должны поддерживать integrity/authenticity verification до исполнения.

## Planned documents

- `THREAT-MODEL.md`;
- `TRUST-MODEL.md`;
- `RBAC-MODEL.md`;
- `SECRETS-MODEL.md`;
- `AUDIT-MODEL.md`;
- `SUPPLY-CHAIN.md`;
- `SECURITY-TEST-MATRIX.md`.

Документы создаются как отдельные versioned artifacts по мере утверждения; этот README не считается заменой threat model.
