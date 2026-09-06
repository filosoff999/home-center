# Threat model Home Center P2.3

Статус: P2.2 accepted baseline; P2.3 `0.4.2` release candidate, 06.09.2026. Requirements: HC-SEC-001..004, HC-HA-004, HC-TEST-002..003.

## Активы

- node/cluster identity;
- Desired/Actual State, jobs и audit evidence;
- admin/session/audit keys и cluster PKI;
- backup/restore points;
- целостность существующего HM.DM.

## Trust boundaries

1. Браузер ↔ Web/API: TLS 1.2+, separate ECDSA P-256/SHA-256 Web identity, same-origin, signed HttpOnly session, CSP.
2. `dc01 ↔ dc02`: отдельный mTLS listener, cluster CA, exact certificate CN + node identity checks.
3. Runtime ↔ ОС: непривилегированный service account и systemd sandbox.
4. GitHub ↔ production bootstrap: audited operator channel; после установки runtime от GitHub не зависит.

## Основные угрозы и controls

| Угроза | Control | Negative gate |
|---|---|---|
| Неавторизованный API read/mutation | deny-by-default auth; mutation отсутствует | unauthenticated 401; DELETE 405 |
| Кража bootstrap token из cookie/log | token меняется на signed session; body/headers не логируются | token echo test |
| Brute force | constant-time compare + per-IP limiter | limiter test |
| Подмена peer | independent peer-CA validation, TLS 1.3, client cert, CN и node schema identity | mismatched identity rejected |
| Browser handshake failure / downgrade | exact P-256/SHA-256 Web CA+leaf; Web TLS ≥1.2; real restricted-sigalgs TLS 1.2/1.3 gates | Ed25519/P-384/RSA Web candidate rejected; Android/Chrome-compatible handshake required |
| Смешение Web и peer identities | separate paths, trust anchors, EKU and activation flows | clientAuth Web leaf rejected; peer fingerprints must remain unchanged |
| Кража Web CA signing key | root-only key on `dc01`; absent on `dc02`; helper systemd path inaccessible; never serialized | dc02 key-presence gate and API/artifact secret scan |
| Audit tampering | keyed hash chain; startup/readiness/backup verification | row mutation causes failure |
| Artifact tampering | exact SHA-256 + internal manifest before install | checksum failure blocks install |
| Archive traversal/special file | path/type gates before release activation | installer gate |
| Crash/race during Web credential publication | root owner marker, operation ID, exact file digests, same-directory atomic rename, directory fsync and CAS cleanup | unowned/mismatched/partial state stays `recovery_required` |
| False recovery after interrupted mutation | fixed reconcile validates release name/fingerprint, chain, SAN/profile, key match and live listener under node lock | helper latch cannot clear on malformed evidence or listener divergence |
| False cluster rollback success | fsync-stable cluster journal stores exact source and peer identity snapshots; both readiness/overview roles and bidirectional mTLS are re-proved | unknown/mismatched recovery remains non-terminal |
| Privilege escalation | no generic shell API, empty capabilities, systemd hardening | static security gate |
| Split-brain | one writer; no auto-failover without witness/fencing | profile contract test |
| AD damage | preserve SID/domain; P1 performs no Samba mutation | deployment profile invariant |

## Residual risks / next gates

- bootstrap token must be rotated into full Identity/RBAC before privileged actions;
- public Web CA trust must be distributed through a separate reviewed HM.DM policy; P2.3 never mutates AD/GPO implicitly;
- `0.4.0` digest remains quarantined because it uses a browser-incompatible Ed25519 Web chain;
- signed durable stable releases and persisted two-node auto-update reconcile remain P2.4/P2.5 gates;
- replicated control-state and automatic failover require witness/fencing design;
- public module supply chain and signing are outside P1.
