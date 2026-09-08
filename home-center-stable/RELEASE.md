# Release 0.14.1

The release set contains the Linux runtime archive, source archive, canonical
SPDX 2.3 document, acceptance record, release manifest, and `SHA256SUMS`. Both
archives contain `VERSION`, `REVISION`, and an internal `MANIFEST.sha256`.
Verify the complete set before installation and require the annotated
`v0.14.1` tag to resolve to the same revision recorded by the artifacts.

For subsequent stable releases, `VERSION` is the canonical publication
identity. The release branch must be exactly `release/<VERSION>`, the annotated
tag is `v<VERSION>`, and `VERSION`, Python package metadata, and the runtime
version must agree. Publication is permitted only from the exact `main` head
while both the tag and GitHub release are vacant. The candidate qualified by CI
is reused byte-for-byte for publication; it is never rebuilt by the publishing
job.
