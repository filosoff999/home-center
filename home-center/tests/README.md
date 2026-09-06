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
- DSSE payload/signature/key tampering and canonical JSON ambiguity;
- signed-ledger replay, equivocation, history rewrite, illegal transition and terminal quarantine;
- exact content-addressed archive bytes, bounded member types/paths, complete manifest and release identity;
- two byte-identical builds from the same clean source revision.

## Evidence standard

Release gate должен хранить machine-readable result, version/revision, environment/profile, scenario ID и проверенные post-conditions. Логи без проверенного результата не считаются достаточным acceptance evidence.

Для signed channel CI PASS и checksum не означают `stable`. Production acceptance evidence сначала хешируется и связывается immutable release record, затем отдельный Home Center signer публикует DSSE ledger transition. Node verifier доказывает подпись/историю/artifact, но runtime-аутентификация внешних evidence sources остаётся ответственностью promotion authority.
