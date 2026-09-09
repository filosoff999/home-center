# Configuration

Copy one of the files under `deploy/config` and replace every example value.
The current configuration is closed by `home-center.config.v5` and defines
cluster and node identity, management addresses, Web and peer ports, state and
backup paths, local-administrator storage, optional directory authentication,
TLS files, an external-access boundary, and zero to 63 explicit peer endpoints.
The runtime also accepts the 0.14 `home-center.config.v4` single-peer shape for
staged upgrades. Addresses in
`192.0.2.0/24` and names below `example.invalid` are documentation values.

The v1 profile under `deploy/profiles` is a two-node example. The v2 profile
under `deploy/examples` demonstrates the portable one-to-64-node planning
shape. Keep automatic failover disabled unless the deployment has an
independently proven fencing design.
