# Trust model Home Center P1

## Identities

- Human: `bootstrap-admin` only for P1; secret stored root/group-readable in `/etc/home-center/secrets/admin.token` and never printed to GitHub evidence.
- Service: local `home-center` Unix account with no login shell and no Linux capabilities.
- Node: stable `node_id` + hashed machine identity + CA-signed Ed25519 certificate.
- Cluster: `hm-dm-production`, locally generated cluster CA; CA private key remains only on `dc01` root storage.

## Authorization

- anonymous: `/healthz`, `/readyz`, `/api/v1/meta` and static login UI;
- authenticated bootstrap admin: versioned read API;
- mutating operations: none in P1, fail-closed;
- peer API: only CA-valid client certificate whose CN equals the configured peer identity.

## Secret handling

Secrets are generated on `dc01`, delivered to `dc02` only through the existing authenticated SSH bootstrap path, stored outside release directories, mode `0600/0640`, and excluded from API/audit/backup artifacts. Node private keys are distinct; CA private key is not copied to `dc02`.
