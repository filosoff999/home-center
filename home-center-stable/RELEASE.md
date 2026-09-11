# Release 0.43.0

The release set contains the Linux runtime archive, source archive, canonical
SPDX 2.3 document, acceptance record, release manifest, and `SHA256SUMS`. Both
archives contain `VERSION`, `REVISION`, and an internal `MANIFEST.sha256`.
Verify the complete set before installation and require the published
`v0.43.0` tag and release artifacts to agree with the release manifest.

This promotion incorporates the approved Home Center development source at
canonical revision `980e9d84736b64a6e1558bae524e4da2a1c8115a` (`v0.43.0`).
The checked-in `APPROVED-SOURCE.json` records that canonical revision, approved
manifest digest `f82f1d80515fab27f320a69341e1cd137154faf6ad0281c2bd5349bd5c002958`,
the stable release-boundary revision
`c9a9c2ab5c5ccd76369409aed5d0b906bafb77b7`, and the per-file disposition
(`identical`, `adapted`, or `excluded`).

The published public `v0.43.0` annotated tag resolves to stable release commit
`006cbf824c2a3a894a98d1619daadd0029c636b5`. This SHA intentionally differs
from the canonical development SHA because the public stable tree applies the
approved hardened/sanitized export mapping. Commit equality between canonical
and public repositories is not a release requirement; the version, approved
source mapping, manifests, checksums, SBOM and acceptance evidence are the
authoritative link.

For subsequent stable releases, `VERSION` is the canonical publication
identity. The release branch must be exactly `release/<VERSION>`, the annotated
tag is `v<VERSION>`, and `VERSION`, Python package metadata, and the runtime
version must agree. Publication is permitted only through the stable release
procedure; current documentation updates do not mutate an already published
tag or GitHub Release.
