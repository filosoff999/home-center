# Home Center 0.9.1

Home Center 0.9.1 consolidates the complete supported work from the published 0.7.0, 0.8.0 and 0.9.0 lines without reintroducing obsolete token authentication.

## Exact predecessor

- version: `0.9.0`
- revision: `29b2f61071067028c14febbbaf0103c5600380e9`
- artifact SHA-256: `66531867f806c6665f41d2bb82dccfb5670403acd0c9988271714da09172f668`

## Integrated 0.7 maintenance delta

- real desktop and Android-like Chrome acceptance against the immutable release artifact;
- mobile logout control and one-column mobile node presentation;
- bounded generic login errors with no backend detail rendering;
- post-login session verification before unlocking the UI;
- fail-closed local UI lock on logout even when the server request fails.

The legacy 0.7 bootstrap-token browser contract is deliberately not carried forward.

## Preserved from 0.8

Local administrator credentials, optional disabled-by-default AD authentication, Secure/HttpOnly/SameSite=Strict sessions, cross-origin protections, auth-free deployment health gates and exact artifact policy.

## Preserved from 0.9

Disabled-by-default trusted reverse-proxy publication boundary, forwarding/origin validation, rate limits, exact two-node release-candidate evidence, `dc02 -> canary/soak -> dc01` rollout, reverse rollback, backup/restore, Web PKI, peer mTLS, DRS/SID and protected-service sentinels.

A published GitHub release is not production deployment authority. Production still requires exact version/revision/artifact identity and the dedicated Home Center server channel.
