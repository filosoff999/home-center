# ADR-0015: Local administrator password rotation

Status: Accepted for 0.12 development

## Decision

The unprivileged Web runtime sends the current and new passwords only through the protected local Unix socket to a dedicated secret-operation route in the root helper. This route is separate from the normal durable helper engine: it does not hash the request, write it to helper state, capture stdout/stderr, spawn a process, or accept a path/command from the caller.

The root helper rotates only `/etc/home-center/secrets/local-admin.json`. It verifies exact directory/file ownership and mode, serializes mutations with a root-controlled lock, verifies the current password, creates an independently salted scrypt verifier, validates the next credential before commit, and uses atomic replacement plus a fixed rollback file. Recovery accepts a valid target or restores the validated rollback file.

## Consequences

- plaintext passwords remain memory-only and are absent from durable evidence;
- every node produces different salt/verifier bytes for the same password;
- the Web service retains no write access to `/etc/home-center/secrets`;
- the helper receives narrowly scoped write access to that directory;
- API audit events contain only actor, node, outcome, policy identifier and safe reason codes;
- cluster-wide rotation and password-loss recovery require later, separately reviewed protocols.
