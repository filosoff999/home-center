# Deployment Profiles

`deploy/` содержит декларативные profiles и versioned fail-closed scripts поставки Home Center. Exact `0.4.3` (`64f798ceae0b669cbac01b452c3cf4fd96070136`, artifact `b2dde6a51ec9450ddd23e802db50be2605c8854e804803ba014422878a02d3d4`) принят как server-side production baseline; отдельное managed-client trust evidence ещё не закрыто. `0.5.0` добавляет инертный P2.4 verifier signed stable channel. Канонический двухузловой rollout выполняется immutable artifact в порядке `dc02 → dc01`; каждый node install создаёт rollback point и проверяет readiness, helper, timers и backup.

## Исполняемые процедуры

- `scripts/build-artifact.sh` — собирает versioned archive с внутренним manifest;
- `scripts/install-node.sh` — проверяет digest/archive, переключает immutable release и автоматически откатывает release/config/systemd units при ошибке;
- `scripts/bootstrap-hm-dm.sh` — оркестрирует HM.DM canary `dc02 → dc01`, создаёт отдельный P-256 Web CA на `dc01` и передаёт на `dc02` только публичный `ca.crt`;
- `scripts/rotate-web-tls.sh` — под fixed lock и marker/digest CAS выпускает P-256 Web leaf, активирует сначала `dc02`, выдерживает 30-секундный canary с TLS 1.2/1.3 без Ed25519 и только затем активирует `dc01`;
- `scripts/rollback-node.sh` — возвращает подтверждённый node rollback point, не удаляя persistent PKI state.
- `runtime/release-channel-verify.py` — offline/read-only проверяет DSSE ledger, anti-replay checkpoint и exact content-addressed artifact; ничего не скачивает, не устанавливает и не перезапускает.

Web CA provision является precondition новой конфигурации с обязательным `web_ca`. Routine rotation не заменяет отсутствующий trust root: `ca.key` должен существовать только на `dc01`, а его наличие на `dc02` блокирует операцию. До первой Web rotation listener сохраняет явный legacy fallback; после активации partial/dangling `web/current` не откатывается молча к legacy identity, а завершает запуск fail-closed.

Каждый cluster rollout публикует fsync-устойчивый журнал с exact source/target/artifact и снимками peer CA/node certificate/public key обеих нод. После interruption разрешён только повтор того же immutable bootstrap: он восстанавливает node transactions, проверяет обе `/readyz`, оба `/overview` с ролями leader/standby и двусторонний peer mTLS, затем переводит journal в `rolled_back`. Web candidate/release staging восстанавливается только по root-owned owner marker и точным digest; неподтверждённое состояние остаётся `recovery_required`.

Эти scripts относятся только к Home Center. Они не управляют `dc01-control-agent.service` и не выполняют Samba AD/DNS/DHCP mutations.

Первый rollout `0.5.0` является явно ограниченным trust-bootstrap переходом из единственного допустимого source release `0.4.3-64f798ceae0b-b2dde6a51ec9`. Наличие verifier внутри target artifact само по себе не делает target доверенным. Production stable-channel polling остаётся выключенным до отдельного signing-key/trust-root bootstrap, durable artifact store и P2.5 persisted updater.

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

## Profiles

- `single-node` — будущая минимальная установка без ложных HA-гарантий;
- `hm-dm-two-node.v1.json` — текущий production leader/standby profile без automatic failover;
- последующие multi-node profiles — только после утверждения соответствующей HA semantics.

## Reconcile rule

Enrollment, upgrade, recovery и drain/remove должны вычислять действия из Desired State + Deployment Profile + Actual State, а не хранить независимые hand-crafted процедуры.

## Safety

- profile change проходит impact/preflight;
- destructive migration отделяется от обычного reconcile;
- unsupported topology блокируется до выполнения изменений;
- profile version фиксируется в job/audit evidence.
