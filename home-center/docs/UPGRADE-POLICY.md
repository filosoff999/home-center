# Home Center Upgrade Compatibility Policy

Status: mandatory from Home Center 0.9.2.

## Compatibility rule

Home Center separates **version compatibility** from **artifact integrity**.

1. A target `X.0.0` is a major bridge. It accepts every strictly older valid Home Center semantic version.
2. Any other target `X.Y.Z` accepts every strictly older Home Center version whose major component is also `X`.
3. Equal-version reinstall and downgrade are not upgrades and are rejected by the upgrade policy.
4. Malformed semantic versions fail closed.

Examples:

- `1.7.5`, `1.7.6`, `1.7.7` -> `2.0.0`: admitted.
- `0.6.0`, `0.7.0`, `0.8.0`, `0.9.0`, `0.9.1` -> `0.9.2`: admitted.
- `0.9.2` -> `0.9.2`: not an upgrade.
- `0.9.9` -> `1.7.7`: rejected because a non-`X.0.0` release does not bridge major lines.

## Safety properties that are never relaxed

Compatibility does not bypass release integrity. Production deployment still requires strict source release structure, exact target VERSION/REVISION and artifact SHA-256, manifest verification, backup/restore evidence, durable transaction state, `dc02 -> canary/soak -> dc01`, reverse rollback, preserved Web and peer PKI, peer mTLS, Domain SID/DRS checks, protected-service sentinels and zero implicit AD/DNS/DHCP/GPO mutation.

## Authentication bridge for pre-0.8 installations

0.8 and newer use local administrator credentials and do not accept the legacy Bearer/token browser contract. When an admitted older installation has no `local-admin.json`, 0.9.2 performs a bounded one-time local migration:

- reads the existing root-controlled legacy secret only on the node;
- never places the secret in argv, environment, logs, GitHub issues or artifacts;
- derives an independent scrypt verifier with a fresh random salt on each node;
- writes `local-admin.json` atomically as `root:home-center` mode `0640`;
- retains the old file only as inert rollback material for the pre-0.8 release;
- the 0.9.2 runtime does not use or accept that legacy token.

The migrated local administrator username is `admin`; the existing legacy token value becomes the initial local administrator password. It may be rotated later using the supported interactive root-only provisioner.
