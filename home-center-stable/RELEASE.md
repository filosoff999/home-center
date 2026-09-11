# Release 0.41.0

The release set contains the Linux runtime archive, source archive, canonical
SPDX 2.3 document, acceptance record, release manifest, and `SHA256SUMS`. Both
archives contain `VERSION`, `REVISION`, and an internal `MANIFEST.sha256`.
Verify the complete set before installation and require the published
`v0.41.0` tag and release artifacts to agree with the release manifest.

This promotion incorporates the approved Home Center development source at
canonical revision `27f2ee8dae2729cf034f57b8fed29ad99b62a95e` (`v0.41.0`).
The checked-in `APPROVED-SOURCE.json` records that canonical revision, its
approved manifest digest, the stable release boundary revision
`5da7643b2f5eb353fd46a10d2ae285ff1b7f0f03`, and the per-file disposition
(`identical`, `adapted`, or `excluded`).

The published public `v0.41.0` tag resolves to stable commit
`00152132ab6d9db1dc28921ae0a1d0b63b370972`. This SHA intentionally differs
from the canonical development SHA because the public stable tree applies the
approved hardened/sanitized export mapping. Commit equality between canonical
and public repositories is not a release requirement; the version, mapping,
manifest, checksums, SBOM and acceptance evidence are the authoritative link.

For subsequent stable releases, `VERSION` is the canonical publication
identity. The release branch must be exactly `release/<VERSION>`, the annotated
tag is `v<VERSION>`, and `VERSION`, Python package metadata, and the runtime
version must agree. Publication is permitted only through the stable release
procedure; current documentation updates do not mutate an already published
tag or GitHub Release.
