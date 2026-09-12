# Архитектура

Home Center состоит из Python control plane, статического Web-интерфейса, локального SQLite state store и закрытых JSON-контрактов. Runtime использует отдельные Web и взаимно аутентифицированные peer listeners.

Планирующие компоненты возвращают типизированные планы и сами по себе не дают execution authority. Root-служба предоставляет только фиксированные helper actions через локальный Unix socket; вызывающая сторона не может передать произвольный executable path или shell-команду.

Peer reconciliation работает с коллекциями, а deployment profiles поддерживают от одного до 64 узлов. Single-node является полноценным режимом. Поставляемый двухузловой профиль сохраняет conservative single-writer semantics до отдельной квалификации более сложной HA-модели.

Health, инфраструктурная инвентаризация, backup/recovery, Audit, module admission, resource snapshots и release identity являются отдельными подсистемами с fail-closed проверкой входных данных. Любая risk-bearing mutation должна завершаться post-condition verification; факт запуска команды не считается подтверждением успеха.
