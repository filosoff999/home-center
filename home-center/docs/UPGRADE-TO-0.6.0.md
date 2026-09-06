# Upgrade Home Center 0.5.0 → 0.6.0

## Scope

Этот runbook описывает только контролируемое обновление Home Center на двух узлах HM.DM. Он не включает production activation signed-channel poller/P2.5 auto-reconcile и не меняет Samba AD, DNS, DHCP, Domain SID, replication topology, client trust stores или `dc01-control-agent.service`.

Порядок неизменяем: **dc02 → canary/soak → dc01 → cluster acceptance**.

## Exact admitted source

Обе ноды перед началом обязаны иметь один и тот же принятый baseline:

- version: `0.5.0`;
- revision: `1d1ff0be759667c40361bbd04b9da273a778b9c8`;
- artifact SHA-256: `3898daba8711dc1b24266c2677b29fc2302877724f49bf0d5cab9a5e4bcda58a`;
- release: `/opt/home-center/releases/0.5.0-1d1ff0be7596-3898daba8711`.

Любое отличие блокирует rollout. Переход напрямую с 0.4.x в 0.6.0 не допускается.

## Target artifact

Использовать только immutable artifact из GitHub-hosted CI точного merged `main` commit после принятия release PR. Перед rollout обязательно зафиксировать:

```text
TARGET_VERSION=0.6.0
TARGET_REVISION=<40-hex merged-main revision>
TARGET_SHA256=<64-hex artifact SHA-256>
TARGET_ARTIFACT=home-center-0.6.0-linux-amd64.tar.gz
```

`TARGET_REVISION` внутри `REVISION` артефакта, Git commit и evidence должны совпадать. `TARGET_SHA256` должен совпадать с uploaded `.sha256` и локальным `sha256sum`.

## Mandatory preflight

До первой mutation:

1. `dc01` и `dc02` доступны через утверждённый management path;
2. обе ноды exact 0.5.0 source baseline;
3. нет unresolved Home Center node/cluster transaction;
4. `home-center.service`, backup timer, helper и TLS maintenance находятся в принятом состоянии;
5. Web CA/leaf/public-key и peer CA/certificate/public-key fingerprints сняты с обеих нод;
6. readiness PASS на обеих нодах;
7. peer mTLS PASS в обе стороны, включая negative client-auth probe;
8. DRS и Domain SID health PASS;
9. protected-service sentinels PASS;
10. backup/rollback path доступен.

## Artifact validation

На управляющем узле:

```bash
sha256sum home-center-0.6.0-linux-amd64.tar.gz
cat home-center-0.6.0-linux-amd64.tar.gz.sha256

tar -xOf home-center-0.6.0-linux-amd64.tar.gz ./VERSION
tar -xOf home-center-0.6.0-linux-amd64.tar.gz ./REVISION
```

Ожидается exact `0.6.0` и exact merged-main revision.

Внутренний manifest:

```bash
TMP=$(mktemp -d)
tar -xzf home-center-0.6.0-linux-amd64.tar.gz -C "$TMP"
(
  cd "$TMP"
  sha256sum -c MANIFEST.sha256
)
rm -rf "$TMP"
```

## Controlled cluster rollout

Запускается только packaged `deploy/bootstrap-hm-dm.sh` из проверенного 0.6.0 artifact. Build-time fail-closed renderer фиксирует в нём единственный допустимый predecessor 0.5.0 baseline.

```bash
sudo ./deploy/bootstrap-hm-dm.sh \
  --artifact /absolute/path/home-center-0.6.0-linux-amd64.tar.gz \
  --sha256 "$TARGET_SHA256"
```

Bootstrap обязан самостоятельно остановиться до mutation при любой source identity, artifact, host, lock, unresolved transaction, TLS/peer или protected-service ambiguity.

## Required acceptance

Rollout считается успешным только если доказаны:

- dc02 upgraded first;
- dc02 software canary/soak PASS;
- dc01 upgraded only after dc02 PASS;
- `dc01 VERSION == dc02 VERSION == 0.6.0`;
- `dc01 REVISION == dc02 REVISION == TARGET_REVISION`;
- exact artifact identity совпадает;
- Web identity fingerprints сохранены;
- peer PKI fingerprints сохранены;
- readiness PASS;
- authenticated overview PASS;
- peer mTLS PASS in both directions;
- backups PASS;
- DRS and Domain SID PASS;
- Samba AD/DNS/DHCP mutation evidence отсутствует;
- protected-service sentinels unchanged;
- cluster transaction terminal `succeeded`.

## Rollback/recovery

При доказуемо rollback-safe failure выполняется reverse rollback **dc01 → dc02**. Неоднозначный outcome переводится в `recovery_required`; продолжение вперёд запрещено до точного доказательства postcondition.

Не удалять rollback points или durable transaction evidence до отдельного post-acceptance cleanup.

## P2.5 activation boundary

0.6.0 устанавливает P2.5 persisted reconcile primitives, но `PRODUCTION_ACTIVATION_ENABLED = False`. Релиз не включает production signing private key, автоматический poller/timer, неявную загрузку artifact или автоматический deploy. Их activation требует отдельного reviewed gate.
