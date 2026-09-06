# External access runbook for Home Center 0.9.0

## Boundary

Home Center does not configure a router, NAT, DDNS, firewall, public certificate, reverse proxy or DNS record. External publication is disabled in both packaged HM.DM configs. The supported boundary is one operator-managed reverse proxy on a private/loopback address, with TLS on both public and backend hops.

The proxy is not a cluster failover mechanism. It points to `dc01` only. Changing the backend to `dc02` requires the existing explicit failover procedure and its fencing/operator proof.

## Required configuration

1. Put the gateway on an exact private/loopback address reachable by Home Center.
2. Install a public certificate for the chosen FQDN on the gateway.
3. Trust the Home Center Web CA for the gateway-to-`dc01.hm.dm` TLS hop.
4. Start from `deploy/external-access/nginx-home-center.example.conf` and replace the example FQDN and certificate paths.
5. The gateway must overwrite, not append, exactly one `X-Forwarded-For`, `X-Forwarded-Proto` and `X-Forwarded-Host`; it must clear `Forwarded`.
6. In both Home Center configs set the same `public_hostname` and exact gateway IP, then set `enabled=true` through the root-controlled configuration path.
7. Restart only through the controlled deployment/change procedure and validate local access before external access.

Example Home Center fragment:

```json
{
  "external_access": {
    "enabled": true,
    "mode": "trusted-reverse-proxy",
    "public_hostname": "home.example.net",
    "trusted_proxy_addresses": ["192.168.10.10"]
  }
}
```

Do not copy the example unchanged. The FQDN and gateway address must be exact deployment values.

## Acceptance

Required positive checks:

- public TLS hostname and chain pass;
- desktop and mobile login pass at `https://<public-host>/`;
- authenticated UI/API requests pass;
- `/external/healthz` returns only the minimal external health schema.

Required negative checks:

- direct public peers are rejected;
- forwarding headers from any non-allowlisted peer are rejected;
- missing, duplicate, chained, non-HTTPS and wrong-host forwarding values are rejected;
- external access to `/healthz`, `/readyz`, `/api/v1/meta`, the Web CA and `/internal/*` is hidden;
- wrong Origin and rate-limit overflow fail closed;
- responses, audit and evidence contain no credentials, session values or internal failure details.

Keep `enabled=false` if any check fails. Enabling the policy is not production acceptance by itself.
