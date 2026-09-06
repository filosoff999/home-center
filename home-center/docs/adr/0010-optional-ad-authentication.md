# ADR-0010 — Optional bounded Active Directory authentication

- **Status:** Accepted
- **Date:** 2026-09-06
- **Related:** issue #29; Home Center 0.8 authentication line

## Context

Home Center must remain usable on a standalone server with a local administrator. When Active Directory is available, explicitly authorized AD accounts may also sign in. AD integration must not reintroduce bootstrap tokens, make local login depend on AD, persist AD passwords, or grant ambient directory mutation/search authority.

## Decision

Home Center 0.8 uses an explicit login provider value: `local` or `ad`.

The AD provider is disabled unless the root-controlled config explicitly enables it. Its configuration contains only:

- exact Kerberos realm;
- one to four exact KDC FQDNs;
- one to sixteen explicitly mapped administrator groups;
- a timeout from 1 to 10 seconds;
- an absolute private credential-cache root.

The runtime invokes only fixed `/usr/bin/kinit` and `/usr/bin/id` paths. It never invokes a shell. The supplied password is written only to `kinit` stdin; it is absent from argv, environment, config, state, logs, audit details and API output.

Each attempt receives a private temporary Kerberos config and FILE credential cache. DNS KDC/realm discovery, reverse DNS canonicalization, forwarding and proxying are disabled. The cache is removed before the request completes.

After Kerberos succeeds, read-only NSS group resolution runs for the same canonical principal. A session is created only when at least one returned group matches the configured administrator-group allowlist. The signed actor is `ad-admin:<canonical-principal>`; this actor receives only the same fixed typed-action permissions currently admitted for the local administrator.

## Failure semantics

- disabled provider, invalid principal/password and membership denial produce the same non-secret invalid-credentials result;
- executable absence, timeout, malformed or oversized membership output, unsafe cache-root metadata and cleanup failure produce a generic authentication-unavailable result;
- no fallback from an explicitly requested AD attempt to local credentials occurs;
- the local provider remains independently usable when AD is unavailable;
- rate limiting applies before either provider;
- no failure path writes the password or directory output into audit evidence.

## Alternatives rejected

- LDAP bind in the control-plane process: rejected for the current dependency-free/offline runtime and larger search/TLS authority surface;
- `wbinfo -a user%password`: rejected because it exposes the password in argv;
- implicit provider fallback: rejected because it makes identity and failure behavior ambiguous;
- automatic AD group/user creation: rejected as an unapproved AD mutation.

## Compatibility and migration

The runtime config advances to `home-center.config.v3`. Deployment profiles carry a complete but disabled AD block. Enabling it is a separate root-controlled configuration change followed by live Kerberos/NSS acceptance. Existing local credentials are preserved and are not copied or compared across nodes.

## Validation

Synthetic fixtures cover disabled, wrong realm, invalid password, membership denied, successful mapped membership, timeout, command/argv bounds, stdin-only password transport and cache cleanup. API/session/action tests cover the `ad-admin` actor and generic failure responses on Python 3.12 and 3.14.

Production activation and live HM.DM acceptance remain pending.
