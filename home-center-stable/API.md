# API

Каноническое описание API находится в `contracts/openapi/home-center.v1.openapi.json`.

Для административных endpoints обязательны аутентификация, same-origin проверки, типизированные request bodies, ограниченные размеры входных данных и закрытые response contracts. Серверная authorization boundary является источником истины; клиентский UI не может расширять полномочия пользователя.

Инфраструктурная инвентаризация является read-only. Planning endpoints возвращают планы/evidence и сами по себе не разрешают execution, installation, изменение инфраструктуры или external publication.

Unknown, stale, malformed или несовместимое состояние обрабатывается fail-closed и не должно превращаться в ложный Success/Healthy.
