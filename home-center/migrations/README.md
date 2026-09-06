# Home Center Migrations

`migrations/` предназначен для будущих versioned data/state migrations Home Center.

До снятия `PREPARATION_ONLY` исполняемые production migrations здесь не создаются.

## Требования к migration

- уникальный version/ID;
- source/target schema versions;
- explicit preconditions;
- compatibility window;
- backup/checkpoint requirement;
- idempotency либо явный one-shot guard;
- forward-recovery/rollback strategy;
- validation queries/checks;
- expected impact/downtime;
- audit/evidence linkage;
- test fixture для upgrade/recovery.

## Cluster-aware rule

Migration, влияющая на replicated/cluster state, обязана определять порядок действий по нодам, совместимость смешанных версий, leader/quorum ограничения и поведение при interruption.

## Запрет

Нельзя использовать migration как скрытый канал произвольного operational scripting. Каждое изменение должно иметь известную schema/state semantics и трассируемое требование.
