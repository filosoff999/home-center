# Upgrade Home Center 0.6.0 → 0.7.0

## Scope

Этот runbook описывает только контролируемое обновление Home Center на HM.DM. Production Release Manager, automatic polling/install и automatic failover в `0.7.0` не активируются.

Порядок rollout неизменяем: **dc02 → canary/soak → dc01 → cluster acceptance**.

## Exact admitted source

Перед первой mutation обе ноды должны иметь exact published `0.6.0` identity:

- version: `0.6.0`;
- revision: `2235670d77bccc1c777223eb4f50a546313b4d2d`;
- artifact SHA-256: `bf68e870339f18351f4401895f7bff18c093fa9809eb4eefda2633391fe9a811`;
- release: `/opt/home-center/releases/0.6.0-2235670d77bc-bf68e870339f`.

Любой другой predecessor блокирует staged bootstrap. Прямой переход с `0.5.x`/`0.4.x` в `0.7.0` не допускается.

## Target artifact

Использовать только immutable artifact из exact merged `main` commit после принятия release PR:

```text
TARGET_VERSION=0.7.0
TARGET_REVISION=<40-hex merged-main revision>
TARGET_SHA256=<64-hex artifact SHA-256>
TARGET_ARTIFACT=home-center-0.7.0-linux-amd64.tar.gz
```

Требуется exact совпадение `REVISION` внутри artifact, Git commit, CI evidence и target revision.

## Mandatory preflight

До mutation:

1. обе ноды exact 0.6.0 source identity;
2. нет unresolved Home Center node/cluster transaction;
3. readiness PASS на dc01/dc02;
4. Web CA/leaf/public-key и peer CA/certificate/public-key fingerprints сняты;
5. peer mTLS PASS в обе стороны и negative client-auth probe PASS;
6. backup/rollback paths доступны;
7. DRS и Domain SID health PASS;
8. protected-service sentinels PASS;
9. target artifact SHA/size/MANIFEST/VERSION/REVISION подтверждены;
10. automatic failover остаётся disabled.

## Artifact validation

```bash
sha256sum home-center-0.7.0-linux-amd64.tar.gz
cat home-center-0.7.0-linux-amd64.tar.gz.sha256

tar -xOf home-center-0.7.0-linux-amd64.tar.gz ./VERSION
tar -xOf home-center-0.7.0-linux-amd64.tar.gz ./REVISION
```

Ожидается exact `0.7.0` и exact merged-main revision.

```bash
TMP=$(mktemp -d)
tar -xzf home-center-0.7.0-linux-amd64.tar.gz -C "$TMP"
(
  cd "$TMP"
  sha256sum -c MANIFEST.sha256
)
rm -rf "$TMP"
```

## Release policy proof

Immutable artifact содержит staged deployment scripts, которые build-time renderer фиксирует на:

- target node installer: exact `0.7.0`;
- cluster predecessor: exact `0.6.0 / 2235670d77bccc1c777223eb4f50a546313b4d2d / /opt/home-center/releases/0.6.0-2235670d77bc-bf68e870339f`.

Renderer fail-closed: неожиданное изменение reviewed source shape блокирует artifact build.

## Controlled rollout

Запускается только packaged `deploy/bootstrap-hm-dm.sh` из проверенного target artifact:

```bash
sudo ./deploy/bootstrap-hm-dm.sh \
  --artifact /absolute/path/home-center-0.7.0-linux-amd64.tar.gz \
  --sha256 "$TARGET_SHA256"
```

Bootstrap обязан остановиться до mutation при source identity drift, unresolved transaction, lock contention, artifact mismatch, TLS/peer ambiguity или protected-service failure.

## Required acceptance

Успех требует доказать:

- dc02 upgraded first;
- dc02 canary/soak PASS;
- dc01 upgraded only after dc02 PASS;
- exact `VERSION=0.7.0` parity;
- exact target `REVISION` parity;
- exact artifact identity parity;
- Web/peer PKI fingerprints preserved;
- readiness PASS;
- authenticated overview PASS;
- peer mTLS PASS in both directions;
- backups PASS;
- DRS / Domain SID PASS;
- no implicit Samba AD/DNS/DHCP mutation;
- protected-service sentinels unchanged;
- terminal cluster transaction `succeeded`.

## Rollback / recovery

Proven rollback-safe failure использует reverse rollback **dc01 → dc02**. Ambiguous outcome становится `recovery_required`; forward progress запрещён до exact postcondition proof.

## 0.7 Release Manager boundary

`0.7.0` включает production-inert offline Release Manager foundation:

- fixed root-owned public trust/signed-envelope locations;
- durable anti-replay checkpoint;
- fixed-inbox content-addressed artifact admission;
- P2.4 full artifact verification;
- no caller-controlled source/destination paths;
- no network/download authority;
- no systemctl/SSH/deployment primitive.

`PRODUCTION_RELEASE_MANAGER_ENABLED = False`; production private signing key, automatic poller/timer и automatic installation остаются отдельным activation gate.
