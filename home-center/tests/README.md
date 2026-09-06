# Home Center Test Architecture

`tests/` предназначен для cross-component и release-gate тестов. Unit tests живут рядом с будущим кодом; здесь находятся проверки системных контрактов и поведения продукта.

## Planned suites

```text
tests/
├── contract/
├── integration/
├── e2e/
├── security/
├── failure-recovery/
├── upgrade-compatibility/
├── backup-restore/
├── ha/
└── performance/
```

Фактические каталоги создаются вместе с исполняемыми tests/fixtures после DEV admission.

## Mandatory scenarios

- enrollment первой/дополнительной ноды;
- повторный reconcile после interruption;
- несовместимая нода блокируется до mutation;
- automatic second-node cluster join;
- loss of connectivity во время lifecycle job;
- module install/upgrade/remove rollback;
- role/VIP failover;
- drain/remove active node;
- drain/remove блокируется при потере quorum/mandatory service;
- backup creation + independent restore verification;
- upgrade N→N+1 и совместимость contracts;
- secret/redaction negative tests;
- unauthorized/over-scoped action denial.

## Evidence standard

Release gate должен хранить machine-readable result, version/revision, environment/profile, scenario ID и проверенные post-conditions. Логи без проверенного результата не считаются достаточным acceptance evidence.
