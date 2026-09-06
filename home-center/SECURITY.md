# Security Policy

Уязвимости Home Center фиксируются приватно владельцем репозитория до подготовки исправления.

Не публикуйте credentials, tokens, private keys, customer data или точные secret-bearing logs в issues.

Критичные инварианты:

- fail-closed mutation boundary;
- least privilege;
- typed actions only;
- audit without secrets;
- immutable artifacts and checksums;
- explicit signed release promotion, append-only quarantine state and anti-replay floor before autonomous update;
- dedicated Home Center release-signing keys; no Web/peer/SSH/Control Center credential reuse;
- backup/rollback before production mutation;
- no automatic failover without witness/fencing proof.

`0.5.0` содержит только offline/read-only verifier. Production signing keys, pinned trust bootstrap, durable artifact origin и automatic updater не настроены. Сообщение о них как об активных без отдельного acceptance считается security defect.
