# Installation

Home Center 0.48.0 requires Linux, systemd, Python 3.12 or newer, SQLite, and
operator-provided TLS identities. Verify `SHA256SUMS`, unpack the runtime, then
run `sudo bash deploy/scripts/install.sh --config /path/to/config.json` to stage
an immutable versioned directory. Add `--activate` only after reviewing the
configuration, service units, certificate paths, and rollback prerequisites.
The activation mode changes the `current` symlink atomically and restores the
previous target if the services do not start successfully.

Never deploy the documentation values unchanged. Publication of a release does
not authorize production activation.
