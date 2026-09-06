# Home Center 0.8.0 release notes

## Release boundary

Home Center 0.8.0 is the security and authentication release following exact 0.7.0:

- predecessor version: `0.7.0`;
- predecessor revision: `f28fc1c820b065616758ca3c220794555c30a25a`;
- predecessor artifact SHA-256: `6ab15ef38b5d4b064ddb45c47009c73de3f5425553a059d75c9fb3c77bc82b82`;
- direct upgrade from older identities is rejected.

The published release is pinned to source revision `bbb2b1e952b2072c8ce30ad6b3220c7c14280949` and artifact SHA-256 `25fb72fdffab972703d9be2b7e457b1f4ed053bc1c013a6237558fd693e73ca8`.

## Added

- mandatory local Home Center administrator with a root-owned scrypt verifier;
- interactive root-only credential provisioner with no password argv, environment or redirected-stdin surface;
- short-lived signed Secure, HttpOnly, SameSite=Strict browser sessions;
- optional AD authentication, disabled by default, with explicit realm, KDC and administrator-group policy;
- token-free deployment/recovery rendering;
- exact 0.7 predecessor policy and config schema `home-center.config.v3`.

## Preserved safety properties

- rollout order is `dc02 → canary/soak → dc01`;
- rollback evidence and exact release identity are mandatory;
- peer mTLS identity remains separate from browser Web PKI;
- no automatic update or automatic failover;
- no implicit Samba AD, DNS, DHCP, GPO, user or group mutation.

Production activation remains a separate exact-artifact operation.
