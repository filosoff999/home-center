# Operations

Monitor the authenticated health, typed infrastructure inventory, TLS, backup,
and audit views. Treat inventory identity inconsistencies as unavailable state;
the API intentionally does not expose rejected persisted facts.
Keep two independently verified backups and test restore regularly. Use the
backup timer for scheduled copies. Local-administrator provisioning and
recovery entrypoints require a local root-controlled console. Treat degraded
peer health, incomplete release identity, audit-chain failure, and malformed
configuration as blocking conditions.
