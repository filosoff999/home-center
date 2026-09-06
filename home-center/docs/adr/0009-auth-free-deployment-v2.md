# ADR-0009: Authentication-free deployment probes for Home Center 0.8

- Status: Accepted
- Date: 2026-09-06
- Related issue: #29
- Depends on: ADR-0008
- Applies to: Home Center 0.8 development and release line

## Context

Home Center 0.7 rollout and recovery scripts use the bootstrap administrator token as both a deployment secret and an HTTP Bearer credential for authenticated `/api/v1/overview` probes. Home Center 0.8 removes Bearer/bootstrap-token authentication from the runtime and makes a local administrator account the mandatory interactive recovery/admin baseline.

Reusing the administrator password for deployment automation would recreate the same security problem in a different form. It would require a plaintext or replayable credential in rollout tooling, logs, CI, remote shell state or process arguments, and it would couple machine health checks to an interactive human identity.

The 0.7 deployment scripts are already part of the predecessor release line and retain their rollback/recovery semantics. Editing those scripts in place would increase regression risk for 0.7 while 0.8 is developed in parallel.

## Decision

Home Center 0.8 deployment, recovery and acceptance probes do not authenticate as an administrator.

The only administrator-related deployment precondition is the presence of a valid root-controlled `local-admin.json` verifier on each node. Deployment validates the file metadata, schema and admitted KDF parameters without knowing or recovering the password. Each node may have a verifier generated with an independent random salt; verifier byte equality between nodes is not a cluster invariant.

Machine health and cluster acceptance use non-user identities and purpose-specific surfaces:

- `/readyz` for public bounded readiness;
- mutual TLS on `/internal/v1/node` for peer identity and node capability checks;
- systemd state for local service liveness;
- backup/restore evidence, durable transaction state and release identity for rollback safety;
- existing two-node peer, soak and recovery gates for cluster acceptance.

Authenticated `/api/v1/overview` is not a deployment probe in 0.8. `admin.token`, `Authorization: Bearer`, generated curl auth files, administrator-token equality and equivalent user-authenticated automation are forbidden from the rendered 0.8 deployment scripts.

The 0.8 deployment scripts are rendered deterministically from the reviewed 0.7 sources by `render-auth-deployment-v2.py`. The renderer is deliberately fail-closed: it recognizes the exact legacy auth blocks, replaces only the admitted authentication/probe surface, preserves required HA/rollback markers, and aborts when source shape drifts or forbidden authentication residue remains.

The legacy 0.7 deployment scripts remain unchanged and are valid only as predecessor/source material. They are not the executable 0.8 deployment contract.

## Alternatives considered

### Use the local administrator password in automation

Rejected. It would require exposing an interactive credential to unattended tooling and would violate ADR-0008's separation between password verification and machine orchestration.

### Create another long-lived bearer/bootstrap token

Rejected. This would preserve the mechanism explicitly removed from the 0.8 runtime and create another replayable administrator-equivalent secret.

### Edit the 0.7 bootstrap/install scripts in place

Rejected for the parallel release model. It would risk changing the predecessor release and make 0.7 rollback semantics depend on incomplete 0.8 work.

### Remove authenticated overview probes without replacement gates

Rejected. 0.8 preserves readiness, peer mTLS identity, backup, transaction, rollback, release and soak gates. Only the administrator-authenticated observation path is removed.

## Security and data impact

No administrator password, plaintext derivative or reusable HTTP credential is required by deployment automation. The local credential file contains only the scrypt verifier defined by ADR-0008 and is validated as root-controlled metadata before rollout.

Machine-to-machine trust remains mTLS-based and does not acquire the authority of a local or AD administrator account.

The renderer rejects residual `admin.token`, Bearer headers, token variables, authenticated overview paths and known legacy auth configuration state before producing an admitted 0.8 deployment script.

## Compatibility and migration impact

Home Center 0.7 keeps its original deployment scripts and token-based bootstrap behavior on its own release line. Home Center 0.8 consumes rendered deployment-v2 scripts only after the 0.8 release-artifact gate is separately admitted.

Before an existing node can move from 0.7 to 0.8, `local-admin.json` must be provisioned through the privileged interactive workflow accepted in ADR-0008. This prerequisite is checked before mutation begins.

The 0.8 migration does not require matching verifier bytes between dc01 and dc02. The same canonical username may be provisioned independently on both nodes while retaining separate salts/verifiers.

## Failure and recovery impact

If the legacy source shape differs from the renderer's reviewed patterns, build/test fails closed instead of generating a guessed deployment script.

If local administrator credential metadata/schema/KDF validation fails on either node, deployment fails before the transaction is published.

If readiness, mTLS peer identity, backup, transaction, release or soak gates fail, the existing rollback/recovery path remains authoritative. Removing the authenticated overview probe does not suppress those failure gates.

## Consequences

Deployment automation and human administrator authentication become separate security domains in 0.8. This reduces credential exposure and makes readiness/peer health independently testable.

The renderer becomes part of the release-critical 0.8 build surface and therefore must be covered by deterministic tests and `security_gate_080.py`.

The 0.8 release artifact remains blocked until release identity/policy wiring explicitly stages the rendered deployment-v2 scripts and passes reproducibility, migration and rollback acceptance.

## Validation and evidence

Required automated evidence includes:

- exact source-shape failure tests;
- absence of `admin.token`, Bearer headers, administrator-token variables and `/api/v1/overview` from rendered deployment output;
- preservation of `/readyz`, `/internal/v1/node`, bidirectional peer identity, rollback, transaction, backup and soak markers;
- `security_gate_080.py` execution of the renderer against the checked-in predecessor deployment sources;
- Python 3.12 and Python 3.14 deterministic CI gates.

## Supersedes / superseded by

This ADR supersedes the 0.7 deployment-authentication mechanism only for the Home Center 0.8 release line. It does not change the 0.7 release contract. No later ADR supersedes this decision at the time of acceptance.
