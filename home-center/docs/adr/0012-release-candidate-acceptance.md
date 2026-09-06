# ADR-0012: Closed release-candidate acceptance evidence

## Status

Accepted for Home Center 0.9.0 release.

## Context

Individual successful probes do not prove that both nodes ran the same artifact, that dc02 preceded dc01, that rollback/restore works, or that PKI/domain safety survived the operation. Free-form evidence is difficult to validate and can leak secrets.

## Decision

The `home-center.release-candidate-acceptance.v1` document is closed, bounded to 64 KiB and contains only exact identities, digests, enumerated results, booleans and zero mutation counters. Unknown fields and duplicate JSON keys fail closed.

Acceptance requires:

- candidate `0.9.0` and predecessor `0.8.0` bound to caller-supplied exact revision/artifact digests;
- rollout `dc02 → dc01` and reverse drill `dc01 → dc02`;
- candidate/predecessor parity on both nodes;
- fresh backup and independent restore verification on both nodes;
- readiness, peer mTLS and DRS PASS;
- unchanged Web and peer PKI identity triplets;
- exact HM.DM Domain SID, one writer and disabled automatic failover;
- external desktop/mobile, spoof rejection, hidden internal surfaces and rate-limit PASS;
- zero AD/DNS/DHCP/GPO mutations and secret-scan PASS.

The verifier has no network, subprocess, deploy, service-control or mutation primitive. It reads a secure regular evidence file and emits a bounded non-secret decision.

## Authority boundary

Acceptance evidence is an observation record, not release authority. Only the separate threshold-signed stable release channel may authorize an artifact for deployment. Live production evidence cannot be manufactured by deterministic CI and remains mandatory after an exact candidate artifact exists.
