# Threat model Home Center P1

Статус: Accepted baseline, 05.09.2026. Requirements: HC-SEC-001..004, HC-HA-004, HC-TEST-002..003.

## Активы

- node/cluster identity;
- Desired/Actual State, jobs и audit evidence;
- admin/session/audit keys и cluster PKI;
- backup/restore points;
- целостность существующего HM.DM.

## Trust boundaries

1. Браузер ↔ Web/API: TLS 1.3, same-origin, signed HttpOnly session, CSP.
2. `dc01 ↔ dc02`: отдельный mTLS listener, cluster CA, exact certificate CN + node identity checks.
3. Runtime ↔ ОС: непривилегированный service account и systemd sandbox.
4. GitHub ↔ production bootstrap: audited operator channel; после установки runtime от GitHub не зависит.

## Основные угрозы и controls

| Угроза | Control | Negative gate |
|---|---|---|
| Неавторизованный API read/mutation | deny-by-default auth; mutation отсутствует | unauthenticated 401; DELETE 405 |
| Кража bootstrap token из cookie/log | token меняется на signed session; body/headers не логируются | token echo test |
| Brute force | constant-time compare + per-IP limiter | limiter test |
| Подмена peer | CA validation, TLS 1.3, client cert, CN и node schema identity | mismatched identity rejected |
| Audit tampering | keyed hash chain; startup/readiness/backup verification | row mutation causes failure |
| Artifact tampering | exact SHA-256 + internal manifest before install | checksum failure blocks install |
| Archive traversal/special file | path/type gates before release activation | installer gate |
| Privilege escalation | no generic shell API, empty capabilities, systemd hardening | static security gate |
| Split-brain | one writer; no auto-failover without witness/fencing | profile contract test |
| AD damage | preserve SID/domain; P1 performs no Samba mutation | deployment profile invariant |

## Residual risks / next gates

- bootstrap token must be rotated into full Identity/RBAC before privileged actions;
- browser trust of the cluster CA must be distributed through a separate reviewed HM.DM policy;
- replicated control-state and automatic failover require witness/fencing design;
- public module supply chain and signing are outside P1.
