# ADR-0001 — Независимый repository и execution boundary Home Center

- Статус: Accepted
- Дата: 06.09.2026
- Requirements: HC-API-001, HC-CONTRACT-001, HC-TEST-003

## Решение

Home Center разрабатывается исключительно в отдельном приватном репозитории `ControlCenterSoft/home-center`. Продукт не разделяет код, документацию, задачи, CI/CD, релизы, артефакты, secrets, runtime или версионирование с другими продуктами.

Build/test выполняются только GitHub-hosted runners. AI Development Infrastructure не используется и не является build/runtime dependency.

Production bootstrap выполняется через отдельный audited HM.DM operator transport. Этот transport не является product API и не создаёт зависимости от другого продукта.

## Последствия

- корень репозитория является корнем Home Center;
- repository, product и release lifecycle полностью независимы;
- runtime работает без GitHub после установки;
- любая будущая cross-product code/runtime/build/deploy dependency запрещена;
- одноразовый перенос ранее реализованного кода не является постоянной зависимостью.
