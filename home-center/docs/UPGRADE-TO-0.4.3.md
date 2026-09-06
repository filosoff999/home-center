# Home Center: upgrade exact 0.3.0/0.4.2 → 0.4.3

Status: **completed server-side on 2026-09-06**. Exact `0.4.3` is now the accepted source baseline for `0.5.0`; this file preserves the historical rollout/recovery procedure. Managed-client CA/browser trust remains a separate open P2.3 evidence gate.

> **HISTORICAL ACCEPTED RUNBOOK — результат зафиксирован, но этот документ не является повторно используемым production authorization.**

Текущий принятый baseline — exact `0.4.3` (`64f798ceae0b669cbac01b452c3cf4fd96070136`). До завершённого rollout источником был accepted `0.3.0` (`6b0c0db144bfd2a7b7a7db1a868d649f20825721`) либо временно deployed `0.4.2` (`9f376e3d39eb29b2c8e402d085cba8b9fee4258d`). `0.4.2` теперь имеет статус `QUARANTINED / DO_NOT_DEPLOY`: первая Web rotation безопасно остановилась до переключения identity, потому что validator ожидал gid `0` у root-only release marker, который capability-free helper создаёт с primary group `home-center`. Исторический bootstrap `0.4.3` допускал только одинаковую exact source identity на обеих нодах; это описание не расширяет admission gate будущих версий.

Версия `0.4.0` quarantined из-за Ed25519 Web chain и TLS alert 40 для наблюдавшегося Android/Chrome ClientHello. Версия `0.4.1` quarantined из-за ошибочного строкового сравнения GNU `stat %F` для пустого regular flock-файла. Ни один digest `0.4.0`–`0.4.2` нельзя использовать для нового rollout.

## Инварианты

| Контур | До upgrade | После acceptance |
|---|---|---|
| Web `:8443` | legacy Ed25519 peer-certificate fallback, TLS 1.2+ | отдельный ECDSA P-256/SHA-256 Web leaf, TLS 1.2+ |
| Peer `:9443` | Ed25519 cluster CA/node identity, TLS 1.3 mTLS | без изменения |
| Web CA private key | `0.3.0`: отсутствует; deployed `0.4.2`: только `dc01`, root `0600` | только `/etc/home-center/pki/web-ca/ca.key` на `dc01`, root `0600` |
| Web CA public cert | `0.3.0`: отсутствует; deployed `0.4.2`: одинаковый cert на обеих нодах | одинаковый `/etc/home-center/pki/web-ca/ca.crt` на обеих нодах |
| Rollout | — | `dc02 → dc01`; automatic failover отключён |

Home Center полностью независим от Control Center. Ни installer, ни rotation не должны менять `dc01-control-agent.service`, Samba AD, DNS, DHCP, Domain SID или replication topology.

## 1. Source и artifact gate

1. Создать release branch от exact current `main`.
2. Получить PASS всех unit/contract/security tests, включая реальные TLS 1.2 и TLS 1.3 handshakes с `ecdsa_secp256r1_sha256` без Ed25519.
3. Слить только reviewed exact-head commit и дождаться PASS `main` CI для merge SHA.
4. Скачать artifact именно этого run; проверить внешний SHA-256 и внутренний `MANIFEST.sha256`.
5. Зафиксировать source SHA, workflow run/artifact ID, archive SHA и размер. Любое несовпадение блокирует mutation.
6. Никогда не использовать для нового rollout artifact/digest `0.4.0`, `0.4.1` или `0.4.2`.

## 2. Production preflight

На обеих нодах до изменения зафиксировать:

- hostname, management IP, Home Center version/revision/artifact SHA и release path;
- `home-center.service`, helper, maintenance timer и backup timer state/start counters;
- публичные SHA-256 fingerprints peer CA, node certificate и public key;
- latest verified backup и свободное место;
- `dc01-control-agent.service` state/start counter только как неизменяемый sentinel;
- Samba Domain SID и read-only DRS baseline;
- отсутствие незавершённого Web candidate/current corruption;
- связь `dc01 → dc02`, root-controlled SSH host key и passwordless bounded `sudo` для штатных scripts.

Unknown, partial или mismatched state означает `BLOCKED`; автоматическое продолжение запрещено.

## 3. Web CA provisioning

Канонический `bootstrap-hm-dm.sh` до установки новой конфигурации:

1. атомарно создаёт Web CA ECDSA P-256/SHA-256 на `dc01`, только если Web CA ещё отсутствует и активной Web identity нет;
2. проверяет self-chain, key/cert match, CA constraints, owner/mode и срок более 427 дней;
3. проверяет, что Web CA отличается от peer CA;
4. передаёт на `dc02` только публичный `ca.crt` через случайный закрытый staging path;
5. блокирует upgrade, если `ca.key` найден на `dc02` или public fingerprints CA различаются.

Routine rotation не создаёт новый trust root молча. Отсутствующий, частичный, истекающий или изменившийся Web CA требует отдельного recovery/CA rollover plan.

## 4. Immutable software rollout

1. Развернуть exact `0.4.3` artifact на `dc02`.
2. Проверить exact version/revision/artifact SHA, readiness, helper probe, timers, backup и legacy Web fallback.
3. Проверить peer mTLS `dc01 → dc02`, DRS и неизменность peer public identities.
4. Выдержать 30-секундный canary soak и повторить readiness, service/timer и peer-mTLS gates. Terminal regression вызывает rollback `dc02` к exact зафиксированному source (`0.3.0` или `0.4.2`) и quarantine нового digest.
5. Только после PASS повторить install на `dc01`.
6. Проверить parity version/revision/artifact SHA и cluster overview на обеих нодах.

Installer восстанавливает release symlink, config и все core/optional systemd units при ошибке. Persistent Web PKI не удаляется version rollback, поэтому retry остаётся детерминированным.

## 5. Web certificate rotation

Запустить exact bundled `rotate-web-tls.sh` на `dc01`. Fixed `flock` запрещает параллельную rotation.

Для каждой ноды leaf должен иметь:

- ECDSA P-256 public key и `ecdsa-with-SHA256` signature;
- `CA:FALSE`, `serverAuth`, без `clientAuth`;
- SAN: exact node FQDN, `home-center.hm.dm` как reserved future identity и exact management IP;
- fingerprint-addressed release и atomic `web/current` symlink.

Порядок обязателен:

1. stage/activate `dc02`;
2. exact presented fingerprint, `/readyz`, TLS 1.2 cipher `ECDHE-ECDSA-AES128-GCM-SHA256`, TLS 1.3, hostname/chain и peer-mTLS canary;
3. выдержать 30 секунд и повторить restricted TLS 1.2/1.3, readiness и peer-mTLS gates на `dc02`;
4. только после PASS stage/activate `dc01`;
5. повторить restricted TLS 1.2/1.3, readiness и peer-mTLS проверки;
6. подтвердить byte-stable peer CA/certificate/public-key fingerprints на обеих нодах.

Activation заранее классифицирует предыдущую цепочку. Candidate получает durable owner marker с operation ID и SHA-256 обоих файлов до публикации credential; release marker создаётся helper как `root:home-center:0600`, поэтому остаётся root-only без требования отсутствующей `CAP_CHOWN`. Cleanup выполняется только после exact CAS-проверки marker/digests. Если postflight новой identity не проходит, helper возвращает предыдущий symlink, перезапускает только `home-center.service` и проверяет прежний presented fingerprint. Timeout mutating action имеет статус `unknown/recovery_required`, а не ложный `failed`. Fixed reconcile восстанавливает только marker-owned partial state и доказывает соответствие durable `current` фактически обслуживаемому fingerprint до снятия recovery latch.

## 6. Acceptance matrix

- `dc01` и `dc02`: exact `0.4.3` source revision и artifact SHA;
- Web cert/CA: `profile=ecdsa-p256-sha256`, `profile_valid=true`, `san_policy_valid=true`, valid chain/hostname/horizon;
- Android/Chrome: handshake завершается, TLS alert 40 / `ERR_SSL_VERSION_OR_CIPHER_MISMATCH` отсутствует;
- managed Windows/browser: Web CA явно установлен административным процессом, hostname/chain trusted без warning;
- `/api/v1/tls/ca.crt`: отдаёт только публичный Web CA, не peer CA;
- peer `:9443`: TLS 1.3 + `CERT_REQUIRED`, mTLS PASS в обе стороны, TLS 1.2 rejected;
- peer CA/node certificate/public-key fingerprints совпадают с preflight;
- Samba Domain SID и DRS: PASS, non-zero failures отсутствуют;
- `dc01-control-agent.service` и domain-service start counters не изменены из-за Home Center rollout;
- failed-rotation synthetic test доказывает rollback; backup/restore verification остаётся PASS;
- automatic failover/VIP activation остаются отключены.

Server-side доказательства получены для exact revision `64f798ceae0b669cbac01b452c3cf4fd96070136`, artifact `b2dde6a51ec9450ddd23e802db50be2605c8854e804803ba014422878a02d3d4`, transaction `20260906T160641Z-4618b29b86a5`. Authoritative GDrive CURRENT и GitHub evidence обновлены; issue остаётся открытой до реального managed-client trust evidence.

## 7. Failure, retry и quarantine

- transient transport failure: повторить read-only preflight, убедиться в отсутствии partial state, затем bounded retry с backoff;
- checksum, signature, profile, identity, DRS, peer-mTLS или rollback regression: terminal failure, немедленный stop и digest quarantine;
- `dc02` failure всегда блокирует `dc01`;
- неизвестный результат mutating helper требует запуска штатного reconcile; blind replay запрещён;
- cluster journal хранит source release и снимки peer CA/node certificate/public key обеих нод; `rolled_back` публикуется только после exact source/readiness/overview/role/peer-mTLS доказательств;
- canonical crash recovery — повторный запуск того же immutable bootstrap artifact/digest; несовпадающий artifact или непроверяемый owner marker блокируется;
- rollback failure фиксируется как `ROLLBACK_FAILED` и требует контролируемого восстановления из exact rollback point без обхода authorization marker;
- все результаты публикуются в Home Center issue/release ledger без secrets или private-key material.
