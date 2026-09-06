# Home Center release supply chain

Status: P2.4 verifier implemented in `0.5.0`; production signing/channel activation pending.

## Authorities

| Input | What it proves | What it does not prove |
|---|---|---|
| Git commit + successful CI | tests ran for an exact source revision | production acceptance or promotion |
| SHA-256 + internal manifest | artifact integrity | who authorized it or whether it is still stable |
| Acceptance evidence | observed HM.DM postconditions | release authority unless bound into a signed record |
| DSSE release ledger | Home Center signing threshold authorized exact history/state | safe two-node mutation by itself |
| Local anti-replay checkpoint | the node has not accepted an older/forked head | artifact availability or health |

Only the complete chain `pinned trust policy → valid DSSE → canonical append-only ledger → accepted stable record → exact content-addressed artifact → P2.5 transaction gates` may authorize a future automatic update.

## Key separation

- release signing is a dedicated Home Center authority;
- Web CA, peer CA, SSH, GitHub PR tokens and Control Center credentials are forbidden as release signers;
- private release keys are absent from the repository, artifacts, PR jobs, `dc01`, `dc02`, logs and evidence;
- public keys are identified by SHA-256 of DER SPKI and become trusted only through a reviewed root-owned bootstrap;
- a threshold policy may contain active, retired and revoked public keys; retired signatures do not satisfy the current threshold, revoked signatures are rejected.

Key generation, custody, signer access, recovery, rotation and public-fingerprint approval are intentionally not improvised by `0.5.0`. They require a separately reviewed operational ceremony. A future rotation is shipped through an already trusted software/trust-root transition; network metadata cannot silently replace its own root.

## Signed objects

The DSSE payload is exact canonical ASCII JSON. The release record binds source, platform, artifact, CI provenance and acceptance. The full ledger binds ordered events and the resulting stable pointer. Stable replacement is atomic; quarantine is terminal for autonomous install. A larger version number or fresh CI artifact has no authority without a signed transition.

Signed evidence references contain a locator and content SHA-256. The release signer is responsible for validating those sources before promotion. The node verifier proves that the signer bound them; it does not independently authenticate GitHub or GDrive at runtime.

## Artifact storage

GitHub Actions artifacts are temporary provenance inputs. A production stable record must refer to a Home Center-owned content-addressed object copied from the exact CI output, never rebuilt after acceptance. The signed object key is relative and derived from the artifact SHA-256; origin configuration is local and root-owned.

A future fetcher must be unprivileged, HTTPS-only, bounded and separated from the offline verifier. It must reject credentials in URLs, arbitrary hosts, cross-origin redirects, unknown content length and oversized data. Admission uses a no-follow, exclusive temporary file, file/directory fsync and a final digest-named atomic move.

## Current `0.5.0` boundary

`release-channel-verify.py` is read-only. It accepts an explicit trust policy and either an existing checkpoint or the conspicuous one-time `--bootstrap-no-checkpoint` flag. It verifies a local artifact store and returns a next checkpoint but does not persist it, fetch content, invoke an installer, restart services or change either server.

The first `0.5.0` deployment therefore uses the already audited exact-artifact operator path from accepted `0.4.3`. This is a transitional software rollout, not stable-channel activation. Automatic polling remains disabled.

## Fail-closed outcomes

- malformed, noncanonical, unsigned, under-threshold, unknown-key or wrong-scope data: reject;
- expired/future/forked/replayed/rewritten ledger: no new update;
- no stable record: no new update;
- quarantined target: no new update; separately authorized exact rollback may still be possible;
- missing/tampered/unsafe artifact: reject before installer execution;
- missing checkpoint after trust bootstrap: production update path is not configured;
- verifier or storage uncertainty: `recovery_required` in P2.5, never best-effort deployment.
