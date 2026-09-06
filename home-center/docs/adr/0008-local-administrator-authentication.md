# ADR-0008: Local administrator authentication for Home Center 0.8

- Status: Accepted
- Date: 2026-09-06
- Related issue: #29
- Applies to: Home Center 0.8 development line

## Context

Home Center 0.7 uses a bootstrap Bearer token both as an interactive Web credential and as part of deployment plumbing. That model is appropriate only for bootstrap/migration and is not acceptable as the normal administrator authentication model for Home Center 0.8.

Home Center must remain fully operable on a standalone installation without Active Directory. When AD is available it may be added as a separate authentication provider, but loss of AD must not remove the local recovery/admin path.

The control-plane service runs as the unprivileged `home-center` account and treats `/etc/home-center` as read-only. Authentication material therefore has to be provisioned by a privileged installation/migration workflow and must not be writable by the Web process.

## Decision

Home Center 0.8 uses a local administrator account as the mandatory baseline authentication provider.

The local credential file uses schema `home-center.local-admin-credential.v1` and contains only a canonical username, random salt and a scrypt verifier. Plaintext or reversibly encrypted passwords are never persisted. The verifier uses fixed admitted scrypt parameters; parameter downgrade is rejected rather than silently accepted.

The production credential file is a regular, non-symlink file owned by root, group-readable by the Home Center service group, and not writable by group or others. Runtime loads and validates it through a bounded, no-follow read path and treats metadata drift as an authentication configuration failure.

Interactive login accepts exactly `username` and `password`. Invalid username, invalid password and legacy token-shaped login requests return the same generic credential failure. Candidate validation retains the configured password-KDF work factor rather than exposing a cheap malformed-input path. Login attempts are rate limited.

After successful authentication the password is discarded and the browser receives only a short-lived HMAC-signed session cookie with `Secure`, `HttpOnly` and `SameSite=Strict`. Authenticated actors use the canonical form `local-admin:<username>` for RBAC and audit attribution. Browser POST requests are additionally constrained by exact HTTPS Origin/Host comparison and Fetch Metadata; cross-site and same-site cross-origin requests fail before credential processing. Logout requires an authenticated session.

Bearer/bootstrap-token authentication is removed from the Home Center Web/API runtime. A bootstrap token may temporarily remain in 0.7 deployment/migration plumbing, but it cannot authenticate a 0.8 runtime session and must be removed from 0.8 deployment probes before 0.8 deployment artifacts are admitted.

The runtime configuration schema is `home-center.config.v3`: it replaces `admin_token_file` with `local_admin_credentials_file` and adds the disabled-by-default `ad_auth` object.

Active Directory authentication is a later additive provider. It must not implicitly modify AD, GPO, domain membership, DNS or client trust, and it must not remove the local administrator recovery path.

## Consequences

0.8 development is source-only until a dedicated migration/install change can provision the local credential safely and replace the legacy technical Bearer probes. CI may run deterministic tests for `develop/0.8.0`, but must not publish a deployment artifact under the 0.7 release identity.

Existing 0.7 release and stabilization branches retain their current authentication/deployment contract. Changes from 0.8 are not backported unless separately reviewed as a 0.7-compatible security fix.

No production administrator username, password, verifier, salt or other credential material is stored in the repository, issues, logs, CI artifacts or documentation examples.
