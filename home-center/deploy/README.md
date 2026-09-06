# Deployment Profiles

`deploy/` предназначен для будущих декларативных deployment profiles Home Center. До снятия `PREPARATION_ONLY` здесь нет исполняемых production deploy scripts.

## Profile model

Deployment Profile должен описывать целевую topology, а не последовательность ручных команд:

- nodes и node classes;
- required capabilities;
- roles/services;
- module set/versions;
- networks/VIP/ports;
- storage classes;
- placement/redundancy policy;
- replication/failover settings;
- monitoring/health policy;
- backup policy;
- upgrade/maintenance constraints.

## Planned profiles

- `single-node` — минимальная установка без ложных HA-гарантий;
- `two-node` — роли с явно описанной active/standby или active/active semantics и ограничениями quorum;
- последующие multi-node profiles — только после утверждения соответствующей HA semantics.

## Reconcile rule

Enrollment, upgrade, recovery и drain/remove должны вычислять действия из Desired State + Deployment Profile + Actual State, а не хранить независимые hand-crafted процедуры.

## Safety

- profile change проходит impact/preflight;
- destructive migration отделяется от обычного reconcile;
- unsupported topology блокируется до выполнения изменений;
- profile version фиксируется в job/audit evidence.
