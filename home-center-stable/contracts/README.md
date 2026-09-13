# Публичные контракты Home Center

Закрытые JSON Schema и OpenAPI в этом каталоге определяют публичные контракты Home Center 0.62.1 для запросов и ответов API, конфигурации, deployment profile, инвентаризации инфраструктуры, обнаружения, планирования, вспомогательных объектов, модулей, health, ресурсов, резервного копирования и release records.

Потребитель обязан отклонять неизвестные поля и неподдерживаемые schema identity. Схемы release acceptance, source provenance и publication являются version-neutral: каждая запись содержит и валидирует собственную семантическую версию продукта.

Текущий Public Stable — 0.62.1. Наличие контракта в этом каталоге само по себе не расширяет квалифицированный release profile и не означает поддержку multi-node HA, automatic failover, неподтверждённого provider execution или иных возможностей, которые не заявлены для конкретной Stable release identity.
