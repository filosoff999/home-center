# Home Center 0.42.0

Home Center is a local-first control plane for managed home infrastructure. It
provides authenticated administration, typed read-only infrastructure
inventory, deterministic discovery and health aggregation, safe node
maintenance planning, backup, resource and intent planning, module admission,
and a bounded helper for local credential rotation. Version 0.15 supports
validated deployment profiles containing one to 64 nodes while retaining the
two-node, single-writer profile as a conservative deployment example.

## Release channels

This repository is the **stable channel**. Version `0.42.0` is the current
qualified PUBLIC STABLE RELEASE.

The official canonical/source line is published separately in
[`ControlCenterSoft/home-center-development`](https://github.com/ControlCenterSoft/home-center-development),
where the latest officially published source release is also `0.42.0`.

Canonical source and public stable use separate release identities. The
checked-in `APPROVED-SOURCE.json` records the approved canonical revision and
its mapping into this hardened public stable tree; commit SHA equality between
the two repositories is therefore not required.

Features added after stable `0.42.0` must not be treated as available in this
stable channel until the corresponding stable package is separately qualified
and published here.

## Start here

Start with [INSTALL.md](INSTALL.md), then tailor the examples described in
[CONFIGURATION.md](CONFIGURATION.md). Release identity and integrity files are
described in [RELEASE.md](RELEASE.md).
