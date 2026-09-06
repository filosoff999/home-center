## Цель

<!-- Один проверяемый vertical slice Home Center. -->

## Изменения

-

## Безопасность и восстановление

- [ ] Нет generic shell/root API.
- [ ] RBAC/fail-closed границы сохранены.
- [ ] Secrets не попадают в код, logs или evidence.
- [ ] Backup/rollback impact оценён.
- [ ] Samba AD/DNS/DHCP не изменяются неявно.

## Проверки

- [ ] `make ci`
- [ ] GitHub-hosted CI PASS
- [ ] Contract/security negative tests PASS
- [ ] Документация и evidence обновлены
