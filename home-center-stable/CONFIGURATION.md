# Конфигурация

Текущий Public Stable **0.63.0** квалифицирован в профиле `single-node-core`. Наличие в конфигурационном контракте полей для peer identity, peer endpoints или примеров multi-node topology не означает, что multi-node/HA профиль поддерживается этим Stable. Используйте такие поля и примеры в production только для release identity, где соответствующий профиль отдельно опубликован и квалифицирован.

Скопируйте подходящий пример из `deploy/config` и замените **все** примерные значения. Текущая конфигурация описывается закрытым контрактом `home-center.config.v5` и включает identity кластера и узла, management addresses, Web/peer ports, пути state и backup, хранилище локального администратора, опциональную directory authentication, TLS-файлы, границу external access и до 63 явно заданных peer endpoints. Для 0.63.0 production authority ограничена квалифицированным `single-node-core`; дополнительные contract fields не расширяют опубликованный profile scope.

Runtime также принимает single-peer форму `home-center.config.v4` для поддерживаемого staged upgrade. Адреса из `192.0.2.0/24` и имена в `example.invalid` являются только документационными примерами и не должны переноситься в рабочую среду.

Профиль v1 в `deploy/profiles` — пример двухузловой конфигурации. Профиль v2 в `deploy/examples` демонстрирует переносимую модель планирования от одного до 64 узлов. Для Public Stable 0.63.0 эти multi-node примеры являются конфигурационными/reference материалами, а не подтверждением production multi-node/HA support. `single-node-core` остаётся текущим квалифицированным режимом.

Не включайте automatic failover, пока для конкретной release identity и deployment/provider не опубликованы и доказаны fencing, split-brain prevention и recovery semantics. External publication, NAT/port-forwarding и удалённый доступ не должны включаться неявно конфигурацией обычного локального сервиса.
