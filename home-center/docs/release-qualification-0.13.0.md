# Home Center 0.13.0 release qualification

Home Center 0.13.0 qualifies infrastructure intents only through a closed,
deterministic planning chain. A qualification report proves that the same
request was accepted by the authenticated API, evaluated against one trusted
resource snapshot, passed capacity preflight, received deterministic placement,
obtained an active bounded reservation, and reached the typed provider preflight
boundary without changing infrastructure.

The qualified operations are storage-share planning and Proxmox VM or LXC
planning. Each report binds the exact API plan, resource snapshot, placement,
reservation, provider operation, and selected node identities with SHA-256
identities. Repeating qualification with identical inputs produces the same
report.

Qualification fails closed when any stage is blocked, missing, expired, or does
not exactly match the preceding stage. This includes request/API divergence,
resource-snapshot drift, altered placement, altered or expired reservation,
provider kind/capability/health/node-scope mismatch, and any enabled execution or
production-mutation flag.

The report contains only bounded identities, checks, and digests. It contains no
provider endpoint, credential, command, shell argument, or secret-bearing input.
It does not create an execution ticket. Provider execution and production
mutation remain disabled; release qualification is not production deployment
authorization.
