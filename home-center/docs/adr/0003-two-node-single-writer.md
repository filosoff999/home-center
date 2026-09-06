# ADR-0003 — Двухузловой single-writer без небезопасного auto-failover

- Статус: Accepted
- Дата: 05.09.2026
- Requirements: HC-NODE-004, HC-NODE-010..014, HC-HA-001..004

## Решение

HM.DM разворачивается как:

- `dc01` — Home Center leader;
- `dc02` — Home Center standby;
- оба узла выполняют local inventory, health, audit и backup;
- peer identity и health проверяются mTLS 1.3;
- writer_count = 1;
- automatic failover = false до независимого witness либо доказанного fencing.

## Обоснование

При двух участниках один узел не может надёжно отличить отказ соседа от сетевого разделения. Автоматическое назначение второго writer создаёт split-brain. Degraded state остаётся наблюдаемым, а смена лидера будет отдельным typed job с fencing proof.

## Drain/Remove

Удаление активного leader блокируется без подтверждённого promotion plan. Destructive wipe всегда отдельная операция. Роли Samba AD являются внешними protected roles и не меняются Home Center P1.
