# Архитектура

Текущий Public Stable **0.63.0** квалифицирован в профиле `single-node-core`. Архитектурные контракты и reference deployment profiles могут содержать multi-node/peer capabilities, но их наличие в коде или конфигурации не означает production-поддержку multi-node/HA для 0.63.0. Такая поддержка появляется только для release identity, где соответствующий профиль отдельно опубликован и квалифицирован.

Home Center состоит из Python control plane, статического Web-интерфейса, локального SQLite state store и закрытых JSON-контрактов. Runtime использует отдельные Web и peer listener contracts; peer-listener требования применяются в production только к квалифицированному multi-node профилю.

Планирующие компоненты возвращают типизированные планы и сами по себе не дают execution authority. Root-служба предоставляет только фиксированные helper actions через локальный Unix socket; вызывающая сторона не может передать произвольный executable path или shell-команду.

Peer reconciliation работает с коллекциями, а архитектурная модель допускает deployment profiles от одного до 64 узлов. Для Public Stable 0.63.0 это не расширяет текущий квалифицированный `single-node-core` scope. Поставляемый двухузловой профиль является reference/staged-конфигурацией и не должен использоваться как доказательство production HA до отдельной qualification с fencing, split-brain prevention, failure/recovery и update guarantees.

Health, инфраструктурная инвентаризация, backup/recovery, Audit, module admission, resource snapshots и release identity являются отдельными подсистемами с fail-closed проверкой входных данных. Любая risk-bearing mutation должна завершаться post-condition verification; факт запуска команды не считается подтверждением успеха.
