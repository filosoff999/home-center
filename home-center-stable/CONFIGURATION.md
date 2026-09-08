# Configuration

Copy one of the files under `deploy/config` and replace every example value.
The configuration is closed by `home-center.config.v4` and defines cluster and
node identity, management addresses, Web and peer ports, state and backup
paths, local-administrator storage, optional directory authentication, TLS
files, an external-access boundary, and the peer endpoint. Addresses in
`192.0.2.0/24` and names below `example.invalid` are documentation values.

The deployment profile under `deploy/profiles` is a two-node example. Keep
automatic failover disabled unless the deployment has an independently proven
fencing design.
