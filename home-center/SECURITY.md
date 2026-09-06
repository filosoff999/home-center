# Security Policy

Уязвимости Home Center фиксируются приватно владельцем репозитория до подготовки исправления.

Не публикуйте credentials, tokens, private keys, customer data или точные secret-bearing logs в issues.

Критичные инварианты:

- fail-closed mutation boundary;
- least privilege;
- typed actions only;
- audit without secrets;
- immutable artifacts and checksums;
- backup/rollback before production mutation;
- no automatic failover without witness/fencing proof.
