# ADR-0002 — P1 runtime foundation

- Статус: Accepted with bounded lifetime
- Дата: 05.09.2026
- Requirements: HC-CORE-001, HC-CORE-002, HC-SEC-001..004, HC-OBS-001..002, HC-BKP-001..003

## Контекст

Первая поставка должна дать проверяемый control-plane baseline на Ubuntu 26.04 без подключения внешнего package registry во время production install.

## Решение

- Python ≥3.12 standard library only;
- HTTPS API/Web и отдельный mTLS peer listener;
- SQLite с `WAL`, `synchronous=FULL` и migration ledger для P1 read-only state;
- HMAC audit chain с проверкой при startup/readiness/backup restore;
- systemd sandbox и непривилегированный пользователь;
- mutating endpoints отсутствуют и возвращают fail-closed denial.

## Ограничение

SQLite не объявляется финальным HA data plane. До открытия mutating jobs требуется отдельный ADR и migration gate для replicated control state (PostgreSQL либо другой измеренно подтверждённый механизм), включая split-brain/fencing semantics.
