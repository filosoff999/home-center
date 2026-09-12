# Конфигурация

Скопируйте подходящий пример из `deploy/config` и замените **все** примерные значения. Текущая конфигурация описывается закрытым контрактом `home-center.config.v5` и включает identity кластера и узла, management addresses, Web/peer ports, пути state и backup, хранилище локального администратора, опциональную directory authentication, TLS-файлы, границу external access и до 63 явно заданных peer endpoints.

Runtime также принимает single-peer форму `home-center.config.v4` для поддерживаемого staged upgrade. Адреса из `192.0.2.0/24` и имена в `example.invalid` являются только документационными примерами и не должны переноситься в рабочую среду.

Профиль v1 в `deploy/profiles` — пример двухузловой конфигурации. Профиль v2 в `deploy/examples` демонстрирует переносимую модель планирования от одного до 64 узлов. Single-node остаётся полноценным режимом.

Не включайте automatic failover, пока для конкретного deployment/provider не доказаны fencing, split-brain prevention и recovery semantics. External publication, NAT/port-forwarding и удалённый доступ не должны включаться неявно конфигурацией обычного локального сервиса.
