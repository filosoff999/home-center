# ADR-0017: Two-node local administrator password transaction

Status: Accepted for 0.12 development

## Decision

The normal password-change API coordinates one two-node transaction only on the leader. The standby receives a closed command through the existing mutually authenticated TLS 1.3 peer listener. The command names exactly one of `prepare`, `commit`, `canary`, `rollback`, `status` or `finalize`; it contains no path, command, executable or environment field.

The order is standby prepare, local prepare, standby commit, three bounded standby authentication canaries with fixed soak intervals, leader commit, leader canary, and finalization on both nodes. In the HM.DM profile this is `dc02 → canary/soak → dc01`. Each node passes the in-memory password to its own fixed-path privileged helper, so the nodes independently generate different salts and verifier bytes.

The transaction ID and a random intent token bind retries without hashing or persisting a password. A different intent, a concurrent transaction or an invalid phase is rejected. Each phase is stored in the node-local SQLite transaction ledger and HMAC audit chain using only safe metadata. A process restart converts any unfinished phase to `recovery_required` because the password is deliberately unavailable after process memory is lost.

On a determinate failure, the coordinator compensates in reverse order. A node that still accepts the old password records rollback without rewriting the credential; a node that accepts only the new password rotates back using a newly generated salt. If the current credential or peer result cannot be established, the operation fails closed as `recovery_required` rather than claiming success or rollback.

Once both nodes have independently authenticated the new password, a failure while finalizing transaction metadata must not compensate credential state: both verified credentials stay authoritative and the incomplete ledger is reported as `recovery_required`. This avoids creating a split credential after successful two-node verification.

## Consequences

- normal Web password change updates both intended nodes or returns a safe rollback/recovery outcome;
- the standby must be reachable and authenticated before the leader credential changes;
- plaintext passwords exist only in browser, Web runtime, protected helper socket and peer mTLS request memory for the operation lifetime;
- logs, audit events, transaction rows, responses and artifacts contain no password, salt, verifier or password-derived hash;
- the operation is initiated on the leader; a standby Web request is rejected;
- production rollout remains a separate authorization after release acceptance.

Tracking: GitHub issues `#86` and `#91`.
