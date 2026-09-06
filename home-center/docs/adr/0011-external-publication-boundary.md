# ADR-0011: External publication through an exact trusted reverse proxy

## Status

Accepted for Home Center 0.9.0 release.

## Context

Publishing the existing Web/API listener directly would make client provenance, TLS termination, origin policy and rate limiting ambiguous. Home Center must also avoid acquiring router, NAT, DDNS or firewall mutation authority.

## Decision

External publication is disabled by default and supports one model: an operator-managed reverse proxy on an exact private/loopback address. The proxy terminates public TLS, verifies the Home Center Web TLS identity on the backend hop and overwrites exactly one `X-Forwarded-For`, `X-Forwarded-Proto=https` and `X-Forwarded-Host` value.

The runtime rejects untrusted forwarding headers, `Forwarded`, missing/duplicate/chained values, wrong proto/host, invalid client addresses and direct global peers. Internal health, metadata, trust-anchor and peer paths are hidden externally. The only unauthenticated external probe is a minimal health envelope. Authentication and mutation paths retain the session, Origin and Fetch Metadata gates.

Client and proxy-wide limits are both enforced so rotating `X-Forwarded-For` cannot bypass the bound. Audit records only the validated client/proxy addresses and access class, never credentials or forwarding input rejected before validation.

The packaged example points only to dc01 and disables proxy failover. Moving external traffic to dc02 remains an explicit cluster failover operation requiring fencing/operator proof.

## Consequences

Home Center gains a testable publication boundary without network-infrastructure authority. An operator must provision the gateway, DNS and certificates and must explicitly enable config v4. Gateway/browser production acceptance remains separate from code CI.
