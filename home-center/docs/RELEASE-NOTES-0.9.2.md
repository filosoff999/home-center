# Home Center 0.9.2

0.9.2 is the generalized upgrade-compatibility maintenance release.

## Upgrade policy

- `X.0.0` accepts every older valid Home Center semantic version.
- Other `X.Y.Z` releases accept every older version within major line `X`.
- Compatibility never bypasses target artifact integrity, rollback, PKI, DRS or protected-service gates.

The production reference upgrade for this release is direct `0.7.0 -> 0.9.2` using the accepted maintenance identity `3112a0d798fc3f47153e4ad98a7223f6b348f5fc`.

## Authentication migration

Pre-0.8 systems with no local administrator verifier receive a one-time, node-local migration from the existing root-controlled legacy secret. The secret is never logged or transported; each node receives a fresh salted scrypt verifier. Legacy token authentication remains disabled in the new runtime, and the old file is retained only for rollback to the previous release.

## Preserved

- local administrator + optional disabled-by-default AD authentication;
- desktop and Android-like Chrome acceptance;
- mobile one-column Nodes UI and version display;
- external-access boundary and rate limits;
- `dc02 -> canary/soak -> dc01`;
- reverse rollback, backup/restore, Web PKI, peer mTLS, DRS/SID and protected-service sentinels;
- no implicit Samba AD, DNS, DHCP, GPO, router or failover mutation.
