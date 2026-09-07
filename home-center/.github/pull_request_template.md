## Development control

Target release: `0.x.y`
Tracking issue: #
Workstream: `workstream-id`

> Target release must match the PR base branch (`release/X.Y.Z`) or be `main` for main maintenance.

## Цель

<!-- Один проверяемый vertical slice Home Center. -->

## Изменения

-

## Acceptance criteria

- [ ] Issue acceptance criteria выполнены и подтверждены repository evidence.
- [ ] Изменение не заявляет больше полномочий/готовности, чем фактически реализовано.

## Безопасность и восстановление

- [ ] Нет generic shell/root API.
- [ ] RBAC/fail-closed границы сохранены.
- [ ] Secrets не попадают в код, logs или evidence.
- [ ] Backup/rollback impact оценён.
- [ ] Samba AD/DNS/DHCP не изменяются неявно.
- [ ] Product docs не раскрывают внутренний процесс/инфраструктуру разработки.

## Проверки

- [ ] `make ci`
- [ ] GitHub-hosted CI PASS на exact PR head.
- [ ] Development Control PASS.
- [ ] Contract/security negative tests PASS.
- [ ] Документация и evidence обновлены.
