# Home Center Web UI

Web UI — действующий пользовательский интерфейс Home Center и клиент versioned Control Plane API. Текущая production-часть показывает topology/health, TLS trust/renewal status и безопасные bounded action results; расширенные mutation workflows остаются плановыми.

В 0.11 интерфейс `Модули` может безопасно показать детерминированный preview разрешений точного admission-плана. Этот экран не сохраняет решение, не выдаёт полномочия и не запускает установку или активацию.

Интерфейс также показывает actor-scoped историю краткоживущих подтверждений ознакомления. Он не создаёт подтверждение сам, не трактует handoff reference как разрешение и не предоставляет approve/grant/install/activate controls.

Lifecycle-область показывает только безопасный no-pending status или заблокированный install preview с preflight и reverse recovery. Она не содержит start/resume/authorize/consume controls и не расширяет backend authority.

## UX invariants

Для изменяющей операции UI обязан показывать:

1. что именно будет изменено;
2. preflight и блокирующие зависимости;
3. ожидаемое влияние/недоступность;
4. прогресс Change/Job;
5. фактический post-condition;
6. degraded/failed state;
7. доступный recovery/rollback path;
8. audit correlation ID.

## Основные области

- Overview / health;
- Nodes;
- Services / Roles;
- Modules / Market;
- Network;
- Storage;
- Backup / Restore;
- Cluster / HA;
- Changes / Jobs;
- Events / Audit;
- Users / RBAC;
- Settings / Deployment Profile.

## Запреты

Web UI не должен содержать скрытую бизнес-логику, обходящую canonical API, или выполнять privileged actions напрямую.

Новый функциональный UI-код добавляется только вместе с утверждёнными API/UX contracts и не может расширять backend permissions.
