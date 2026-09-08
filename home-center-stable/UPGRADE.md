# Upgrade

For every upgrade, verify `SHA256SUMS`, the embedded `MANIFEST.sha256`, release
identity, the SPDX document, acceptance record, and release manifest. Stage the
new version in a new directory, retain the previous immutable directory, and
change the current pointer only after local validation. Upgrade one node at a
time while preserving a ready node and the single-writer role. Roll back by
restoring the previous pointer and rechecking health and release identity.
