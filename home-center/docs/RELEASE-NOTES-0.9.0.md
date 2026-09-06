# Home Center 0.9.0 release notes

Home Center 0.9.0 is the release-candidate hardening line following exact published 0.8.0 (`bbb2b1e952b2072c8ce30ad6b3220c7c14280949`, artifact SHA-256 `25fb72fdffab972703d9be2b7e457b1f4ed053bc1c013a6237558fd693e73ca8`).

## Added

- disabled-by-default, trusted-reverse-proxy external publication boundary;
- exact forwarding provenance, HTTPS/public-host and same-origin checks;
- client and proxy-wide rate limits resistant to forwarded-address rotation;
- minimal external health and authenticated non-secret policy status;
- deterministic two-node artifact/runtime/external-login/backup E2E gate;
- closed release-candidate acceptance and verification contracts;
- fail-closed verifier for exact rollout, rollback, restore, parity, PKI, DRS and safety evidence;
- exact 0.8.0 → 0.9.0 release policy and operator runbooks.

## Preserved

- local administrator and optional disabled-by-default AD authentication;
- `dc02 → canary/soak → dc01` rollout and `dc01 → dc02` reverse rollback;
- separate Web and peer PKI identities;
- no automatic update or automatic failover;
- no implicit Samba AD, DNS, DHCP, GPO, router, NAT or DDNS mutation.

The code release is pinned to source revision `29b2f61071067028c14febbbaf0103c5600380e9` and artifact SHA-256 `66531867f806c6665f41d2bb82dccfb5670403acd0c9988271714da09172f668`.

Code completion, CI and publication do not claim production acceptance. Browser/gateway observations and live two-node evidence remain separate mandatory gates.
