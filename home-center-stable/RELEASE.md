# Релиз Home Center 0.59.0

Официальный release set содержит Linux runtime archive, source archive, SPDX 2.3 SBOM, acceptance evidence, release manifest, `SHA256SUMS` и отдельный checksum sidecar для Linux-архива. Архивы содержат `VERSION`, `REVISION` и внутренний `MANIFEST.sha256`.

Перед установкой проверяйте полный набор: опубликованный tag `v0.59.0`, release assets, release manifest, checksums, SBOM и acceptance evidence должны согласовываться между собой. Любое расхождение release identity или контрольных сумм является блокирующим.

Публичный annotated tag `v0.59.0` соответствует опубликованному Stable-релизу. Связь релиза с утверждённым исходным набором фиксируется машиночитаемым approved-source/provenance evidence и per-file disposition. Пользователю не требуется сравнивать SHA с внутренними контурами подготовки релиза; источником истины являются публичная release identity и опубликованный evidence set.

Для последующих Stable-релизов `VERSION` является канонической пользовательской publication identity. Tag имеет форму `v<VERSION>`, а `VERSION`, package metadata и runtime version должны совпадать. Публикация допускается только через отдельную Stable release procedure и явное release authority; изменение текущей документации не изменяет уже опубликованный tag или GitHub Release.

Важно: checksum подтверждает целостность конкретного артефакта, но не подменяет qualification install/upgrade path. Для особенностей updater-path версии 0.59.0 см. [UPGRADE.md](UPGRADE.md).
