# Certificates

Provide separate browser-compatible Web identity and peer mutual-TLS identity.
The Web certificate must match the configured hostname and management address;
the peer certificate must match the node identity. Store keys outside the
source and runtime archives with root-controlled permissions. Validate chains,
names, algorithms, expiry, and key matching before activation, and keep the
previous valid identity available for rollback.
