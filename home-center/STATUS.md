# Текущий статус Home Center

**Дата:** 06.09.2026  
**Repository:** `ControlCenterSoft/home-center`  
**Статус:** `INDEPENDENT_BOOTSTRAP_IN_PROGRESS`

## Принятые границы

- самостоятельный продукт;
- отдельные repository, issues, CI/CD, releases и runtime;
- GitHub-hosted engineering compute only;
- HM.DM target: `dc02 → dc01`;
- automatic failover disabled до witness/fencing certification;
- domain services не изменяются неявно.

## Текущий этап

Одноразовый перенос принятого P1 `0.1.0` в независимый repository с повторным CI и production acceptance. Старое production evidence не считается актуальным для независимого репозитория до нового canary rollout.
