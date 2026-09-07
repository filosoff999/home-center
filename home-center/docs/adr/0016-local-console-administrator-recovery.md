# ADR-0016: Physical-console local administrator recovery

Status: Accepted for 0.12 development

## Decision

Password-loss recovery is an offline node-local operation. The packaged recovery program accepts no password option, environment input, pipe, file, API call or remote execution transport. It runs only with effective UID 0 when stdin, stdout and stderr are the same Linux virtual console `/dev/tty1` through `/dev/tty63`. SSH pseudo-terminals are not admitted. Before password input, the operator repeats a random, non-secret confirmation phrase. The new password and its confirmation are then read with echo disabled.

The program uses the same fixed credential path, complexity policy, fresh-salt scrypt generation, lock, prevalidation, atomic replacement and rollback primitive as normal password rotation. A success event is appended to `/var/lib/home-center-recovery/events.jsonl` before rollback cleanup. The file and its directory are root-only; every entry has a closed allowlist of safe metadata and is linked to the previous entry by SHA-256. Existing evidence is fully verified before a new recovery begins. If success evidence cannot commit, the credential transaction restores the previous verifier.

The long-running runtime reloads the credential file through its exact ownership/mode validator before every local login. Therefore a successful offline reset takes effect without restarting Home Center or introducing a service-control command into the recovery program.

## Consequences

- password material remains only in the interactive process memory and is never accepted from CLI arguments, stdin pipes, environment variables, SSH, logs or evidence;
- console presence and root authorization are independently required;
- recovery is node-local and does not imply that another node has the same intended password;
- the evidence chain is locally tamper-evident and root-controlled, but is not copied to the peer;
- two-node coordination remains a separate transaction defined by issue `#91`;
- no production deployment is authorized by this decision.

Tracking: GitHub issue `#90`.
