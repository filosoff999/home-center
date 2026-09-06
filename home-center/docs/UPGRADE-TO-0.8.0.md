# Upgrade plan Home Center 0.7.0 → 0.8.0

## Status

This is a release plan, not production authorization. The exact 0.8 merged-main revision and artifact SHA-256 are intentionally unresolved until the release PR passes build-twice verification.

Rollout order remains **dc02 → canary/soak → dc01 → cluster acceptance**. Automatic update and automatic failover remain disabled.

## Exact admitted source

Both nodes must start from the exact published 0.7.0 identity:

- version: `0.7.0`;
- revision: `f28fc1c820b065616758ca3c220794555c30a25a`;
- artifact SHA-256: `6ab15ef38b5d4b064ddb45c47009c73de3f5425553a059d75c9fb3c77bc82b82`;
- release: `/opt/home-center/releases/0.7.0-f28fc1c820b0-6ab15ef38b5d`.

A direct upgrade from 0.6.x or older fails closed.

## Authentication migration preflight

Before any 0.8 deployment:

1. create an independent local administrator verifier on each node with the packaged root-only interactive provisioner;
2. confirm each `/etc/home-center/secrets/local-admin.json` is a regular non-symlink file, owner `root`, group `home-center`, mode `0640`;
3. do not copy or compare verifier bytes between nodes because salts are intentionally independent;
4. retain no `admin.token` authority in the rendered 0.8 artifact;
5. keep `ad_auth.enabled=false` for the first rollout;
6. verify local login and local typed-action authorization before any later AD activation.

Passwords must never be supplied through command arguments, environment variables, redirected stdin, logs, issues or artifacts. The provisioner accepts hidden input only from an interactive TTY.

## Target artifact

Only an immutable artifact from the exact merged 0.8 main revision is eligible:

```text
TARGET_VERSION=0.8.0
TARGET_REVISION=<40-hex merged-main revision>
TARGET_SHA256=<64-hex artifact SHA-256>
TARGET_ARTIFACT=home-center-0.8.0-linux-amd64.tar.gz
```

The artifact must prove:

- exact VERSION/REVISION/MANIFEST identity;
- exact 0.7 predecessor policy above;
- config schema `home-center.config.v3`;
- local-admin credential gates;
- absence of bootstrap Bearer/token and authenticated deployment probes;
- preserved readiness, peer mTLS, backup, durable transaction, rollback and protected-service gates.

## Controlled rollout

Run only the packaged bootstrap from the verified target artifact. The script must reject source drift, unresolved transactions, unsafe credentials, lock contention, artifact mismatch, PKI ambiguity or failed protected-service sentinels before mutation.

The dc02 canary must pass twice across the bounded soak before dc01 installation begins.

## Required acceptance

- exact 0.8 version/revision/artifact parity;
- local administrator login on both nodes;
- old bootstrap token cannot authenticate;
- dc02-first canary/soak PASS;
- peer mTLS in both directions PASS;
- Web identity and Web/peer CA fingerprints preserved;
- backup and rollback evidence PASS;
- Domain SID and DRS health preserved;
- no Samba AD, DNS, DHCP, GPO, user or group mutation;
- terminal cluster transaction `succeeded`.

## Optional AD activation after local acceptance

AD remains disabled during the base upgrade. A later explicit change may enable it only after:

1. exact KDC reachability and Kerberos clock/DNS health are verified;
2. the configured administrator-group mapping is confirmed;
3. mapped-member success and non-member denial pass;
4. intentional KDC outage returns a generic unavailable result;
5. local administrator login still succeeds during that outage;
6. audit/API output is checked for absence of password, ticket and raw directory data.

No Home Center workflow creates or changes AD objects or GPOs.

## Rollback

Reverse rollback order is dc01 → dc02. Any ambiguous outcome becomes `recovery_required`; forward progress is blocked until exact postconditions prove a known release, authentication and PKI state.
