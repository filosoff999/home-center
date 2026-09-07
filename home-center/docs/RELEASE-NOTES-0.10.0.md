# Home Center 0.10.0

Home Center 0.10.0 establishes the versioned Core planning boundary while
preserving the accepted 0.9.2 runtime, authentication, release-integrity and
two-node safety controls.

## Exact predecessor

- version: `0.9.2`;
- source revision: `689d90995a4f365e2f19640486b136b525f29c6d`;
- artifact SHA-256:
  `90d5cb0b657abfdd13f077b4a2347c5066fbaddcd4f4bfa6b25944e73a8abe87`.

## Core foundation

- closed command, result and error schemas;
- typed input and output contracts for the five admitted planning actions;
- plan-only Node, Upgrade, Configuration, Service and Policy modules;
- exact action dispatch and an explicit API compatibility matrix;
- default-deny policy behavior and deterministic dependency planning.

## Safety boundary

The new Core can validate requests and describe plans. It has no executor,
process runner, filesystem mutation surface, network client, scheduler,
reconciler or production activation authority. No public Core HTTP route is
introduced in this release.

Publishing `v0.10.0` is not production deployment authority. The accepted
production baseline remains Home Center `0.7.0`; any future rollout still
requires exact version/revision/artifact admission and the staged
`dc02 -> canary/soak -> dc01` procedure.

The 0.9.x feature line is closed on the published `v0.9.2` release. Any later
0.9.x security or maintenance exception requires explicit authorization.
