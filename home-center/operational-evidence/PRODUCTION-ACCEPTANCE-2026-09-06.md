# Production acceptance — 2026-09-06

**Status:** `PASS`  
**Source repository:** `ControlCenterSoft/home-center`  
**Version:** `0.1.0`  
**Revision:** `0b9c4c1d0c1d0da85461e324ca780b56652634ec`

## Supply-chain evidence

| Control | Evidence |
|---|---|
| GitHub-hosted CI | [main run 34013822217](https://github.com/ControlCenterSoft/home-center/actions/runs/34013822217), success |
| Artifact | `home-center-0.1.0-linux-amd64-0b9c4c1d0c1d0da85461e324ca780b56652634ec` |
| GitHub artifact ID | `9983284619` |
| Download ZIP SHA-256 | `50285dfa439baa1ce32c499c38314478b6e125f5fcd6d7c898d857c2b91ddc1e` |
| Deployment tarball SHA-256 | `6c4ca0fbe19663d2d391d31ca296abac303756a851bf6d4275d1e45d25215a58` |
| Artifact checks | standalone checksum, internal manifest, Bash syntax and Python compilation passed |

Engineering compute used GitHub-hosted runners only. HM.DM nodes were used solely as deployment targets and for read-only production acceptance.

## Deployment evidence

The audited HM.DM pull channel executed the rollout from `dc01`. The orchestrator deployed canary `dc02` first and `dc01` second.

- [preflight command](https://github.com/ControlCenterSoft/serverops-control/issues/1619#issuecomment-5557204058) and [PASS result](https://github.com/ControlCenterSoft/serverops-control/issues/1619#issuecomment-5557204339);
- [canary deployment command](https://github.com/ControlCenterSoft/serverops-control/issues/1619#issuecomment-5557208966) and [PASS result](https://github.com/ControlCenterSoft/serverops-control/issues/1619#issuecomment-5557210150);
- [post-deployment acceptance command](https://github.com/ControlCenterSoft/serverops-control/issues/1619#issuecomment-5557221528) and [PASS result](https://github.com/ControlCenterSoft/serverops-control/issues/1619#issuecomment-5557221980).

| Node | Role | Release | Rollback point |
|---|---|---|---|
| `dc02.hm.dm` / `192.168.10.253` | standby / canary | `/opt/home-center/releases/0.1.0-0b9c4c1d0c1d-6c4ca0fbe196` | `/var/backups/home-center-deploy/20260906T053242Z-dc02` |
| `dc01.hm.dm` / `192.168.10.254` | leader / control-plane | `/opt/home-center/releases/0.1.0-0b9c4c1d0c1d-6c4ca0fbe196` | `/var/backups/home-center-deploy/20260906T053245Z-dc01` |

Both nodes were upgraded atomically from the prior release. No rollback was required.

## Acceptance matrix

| Check | Result |
|---|---|
| Exact revision and version on both nodes | PASS |
| `home-center.service` active and enabled | PASS |
| Backup timer enabled | PASS |
| Failed systemd units | `0` on both nodes |
| Readiness schema and node identity | PASS |
| Cluster overview on both nodes | healthy, `2/2` ready |
| Role model | `dc01=leader`, `dc02=standby` |
| Peer mTLS in both directions | PASS |
| Latest backup independent verification | PASS |
| Domain SID | `S-1-5-21-483832520-828804035-215000592`, preserved |
| Samba DRS replication | PASS on both nodes |
| Automatic failover | disabled; no witness/fencing certification |
| Product-boundary scan | PASS |
| Samba AD/DNS/DHCP mutations by rollout | none |

## Product boundary

This acceptance applies only to Home Center. Its repository, code, documentation, issues, CI/CD, artifacts, deployment profile, runtime and versioning remain independent. The deployment payload contains no reference to any foreign product repository path, and no unrelated product repository or runtime was modified.
