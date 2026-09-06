# Home Center 0.8.0 release notes

## Release boundary

Home Center 0.8.0 is the security and authentication release following exact 0.7.0:

- predecessor version: `0.7.0`;
- predecessor revision: `f28fc1c820b065616758ca3c220794555c30a25a`;
- predecessor artifact SHA-256: `6ab15ef38b5d4b064ddb45c47009c73de3f5425553a059d75c9fb3c77bc82b82`;
- direct upgrade from older identities is rejected.

The release artifact is eligible only after the pull request into `main` passes Python 3.12/3.14 deterministic gates and reproducible build-twice byte comparison. The exact merged-main revision and artifact digest are recorded as external immutable release evidence because an artifact cannot safely bind its own future merge SHA or digest.

## Added

- mandatory local Home Center administrator with a root-owned scrypt verifier;
- interactive root-only credential provisioner with no password argv, environment or redirected-stdin surface;
- short-lived signed Secure, HttpOnly, SameSite=Strict browser sessions;
- optional AD authentication, disabled by default, with explicit realm, KDC and administrator-group policy;
- fixed Kerberos/NSS executables, bounded timeouts and deleted per-attempt credential cache;
- public secret-free provider availability contract; the AD UI option remains hidden until explicitly enabled;
- exact HTTPS Origin/Host and Fetch Metadata checks for browser POST requests;
- authenticated and audited logout;
- token-free 0.8 deployment/recovery rendering;
- exact 0.7 predecessor policy and config schema `home-center.config.v3`.

## Removed

- bootstrap Bearer/token authentication from the 0.8 Web/API runtime;
- authenticated deployment probes that depended on a shared administrator token;
- any need to copy a local password verifier between nodes.

## Preserved safety properties

- rollout order is `dc02 → canary/soak → dc01`;
- rollback evidence and exact release identity are mandatory;
- peer mTLS identity remains separate from browser Web PKI;
- local administrator login remains independent of AD availability;
- no automatic update or automatic failover;
- no implicit Samba AD, DNS, DHCP, GPO, user or group mutation;
- Home Center remains independent from Control Center and AI Development Fabric.

## Production activation

The code release does not itself authorize production mutation. Production requires the exact merged-main artifact, checksum verification, independent local administrator provisioning on both nodes, backup/rollback preflight, dc02 canary/soak, dc01 promotion and final cluster acceptance.

Optional AD activation is a later explicit step after local-login acceptance and live HM.DM Kerberos/NSS tests. Failure of optional AD must not remove the local recovery path.
