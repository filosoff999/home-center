# Release 0.22.3

The release set contains the Linux runtime archive, source archive, canonical
SPDX 2.3 document, acceptance record, release manifest, and `SHA256SUMS`. Both
archives contain `VERSION`, `REVISION`, and an internal `MANIFEST.sha256`.
Verify the complete set before installation and require the annotated
`v0.22.3` tag to resolve to the same revision recorded by the artifacts.

This promotion incorporates the approved Home Center development source at
revision `6f56e3b7f134b62722e7b53c0ce8e8a6d5c8349f`. The checked-in
`APPROVED-SOURCE.json` records every approved path and digest, whether it is
byte-identical, adapted to the hardened stable runtime, or excluded by
release-specific stable requirements.

For subsequent stable releases, `VERSION` is the canonical publication
identity. The release branch must be exactly `release/<VERSION>`, the annotated
tag is `v<VERSION>`, and `VERSION`, Python package metadata, and the runtime
version must agree. Publication is permitted only from the exact `main` head
while both the tag and GitHub release are vacant. The candidate qualified by CI
is reused byte-for-byte for publication; it is never rebuilt by the publishing
job.
