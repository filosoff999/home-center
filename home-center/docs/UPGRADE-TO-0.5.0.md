# Home Center: transitional upgrade exact 0.4.3 → 0.5.0

Status: `0.5.0` release candidate. This runbook is the one-time audited rollout that installs the P2.4 read-only verifier; it does **not** activate a production signed channel or automatic updater.

## 1. Exact source admission

Both nodes must report the same source identity:

- version: `0.4.3`;
- revision: `64f798ceae0b669cbac01b452c3cf4fd96070136`;
- artifact SHA-256: `b2dde6a51ec9450ddd23e802db50be2605c8854e804803ba014422878a02d3d4`;
- release: `/opt/home-center/releases/0.4.3-64f798ceae0b-b2dde6a51ec9`;
- prior cluster transaction `20260906T160641Z-4618b29b86a5` is terminal `succeeded`;
- `/readyz`, peer mTLS, DRS and backup/timers are healthy;
- no unresolved node, cluster or Web TLS mutation exists.

Any mismatch is `BLOCKED`. `0.4.0`, `0.4.1` and `0.4.2` are not admitted as sources for this rollout.

## 2. Candidate gates

The exact `0.5.0` main revision must have:

1. Python 3.12 and 3.14 deterministic gates PASS;
2. all contracts, 0.4.3 TLS/helper/recovery regressions and P2.4 negative tests PASS;
3. build-twice byte-identical archives from a clean exact checkout;
4. exact artifact SHA-256 and full internal manifest;
5. no private signing key or production trust-policy secret;
6. GitHub Actions pinned by full commit SHA;
7. independent review with no unresolved blocker.

Do not create a production `stable` record merely because these gates pass. Promotion needs the separate signing/trust/storage ceremony in ADR-0007.

## 3. Deployment order

Use the exact bundled `bootstrap-hm-dm.sh`, `install-node.sh` and `rollback-node.sh` from the candidate artifact.

1. Pin artifact and its independently recorded SHA-256 in root-only temporary input on `dc01`.
2. Run read-only source/identity/service/DRS/peer/Web preflight and transactionally snapshot the existing peer CA/node plus Web CA/leaf/public-key fingerprints.
3. Install exact candidate on `dc02`.
4. Verify transaction marker, current release, `VERSION`, `REVISION`, full artifact digest, `/readyz`, helper probe, backup/timers, Web TLS 1.2/1.3 and peer mTLS.
5. Hold the mandatory 30-second canary and repeat readiness, service/timer and peer gates.
6. Only after `dc02` PASS, install exact candidate on `dc01`.
7. Verify exact parity and all cluster postconditions, including equality of every snapshotted peer and Web public identity before terminal `succeeded` publication.

The rollout must not rotate Web certificates: `/etc/home-center/pki/web`, Web CA and peer PKI are persistent state and must remain byte/fingerprint stable.

## 4. Required final evidence

- exact `0.5.0` version/revision/artifact/release path on both nodes;
- both node transactions and the cluster transaction terminal `succeeded`;
- archive manifest check PASS on both releases;
- `/healthz`, `/readyz`, authenticated overview and expected leader/standby roles PASS;
- Web TLS 1.2/1.3 hostname/chain/profile and exact preflight fingerprints unchanged;
- peer TLS 1.3 `CERT_REQUIRED`, bidirectional mTLS and all peer public fingerprints unchanged;
- Samba Domain SID `S-1-5-21-483832520-828804035-215000592` and DRS PASS;
- `dc01-control-agent.service` and Samba AD/DNS/DHCP service sentinels unchanged by Home Center;
- backup, helper and maintenance timers active; automatic failover remains disabled;
- P2.4 verifier exists in the exact release and its offline negative suite passed;
- production signing key/trust policy/poller remain absent and disabled.

## 5. Failure and recovery

- failure before any installer mutation: stop with no node change;
- `dc02` failure: rollback exact `dc02` source and never touch `dc01`;
- `dc01` failure after canary: rollback touched nodes through the existing cluster recovery transaction and prove exact source/readiness/roles, peer mTLS, and unchanged peer/Web public identities;
- unknown mutating result: reconcile the existing transaction only; blind retry with another artifact is forbidden;
- rollback failure: record `recovery_required`, preserve evidence and stop;
- no failure path modifies Control Center, Samba AD/DNS/DHCP, Web/peer CA keys or automatic failover.

## 6. Post-deployment boundary

Successful `0.5.0` acceptance means only that the read-only signed-channel primitive is current on both nodes. It does not satisfy the permanent «always current» workflow. P2.5 may be enabled only after a production Home Center signing key, pinned root-owned trust policy, durable artifact store and atomic checkpoint/update state machine are separately accepted.
