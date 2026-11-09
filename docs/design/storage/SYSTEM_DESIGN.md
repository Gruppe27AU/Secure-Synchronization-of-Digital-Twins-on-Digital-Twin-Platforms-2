# Complete system design

Part of the [storage design](README.md). Puts the other documents
together: context, deployment, quality requirements and decisions.

## System context

```mermaid
flowchart LR
    user([Workspace user]) -- browser --> traefik[Traefik + Oathkeeper]
    op([Operator]) -- compose, consoles --> host[(DTaaS host)]
    traefik -- introspect, authz --> lr[login-relay]
    traefik --> ws[Workspace container per user]
    lr -- OAuth --> idp[GitLab or Keycloak]
    ws -- OAuth refresh, git HTTPS --> idp
    ws -- introspect, exchange --> lr
    ws -- STS, S3 --> s3[(SeaweedFS or cloud S3)]
    s3 -. signing keys .-> lr
```

## Inside a workspace

The `workspace-admin` process (packages, listeners, files, threads, and
what happens at start, in a round and at stop) is described in
[workspace-admin architecture](../workspace-admin/ARCHITECTURE.md),
with its [requirements](../workspace-admin/REQUIREMENTS.md).

## Deployment (Docker Compose)

| Service | Networks | Ports | Changes |
| :--- | :--- | :--- | :--- |
| traefik, oathkeeper, login-relay | as today (F9 adds them to four deployments) | as today | login-relay: profile, `/token/exchange`, bounce, signing key secret ([LOGIN_RELAY](../auth/LOGIN_RELAY.md)) |
| keycloak (Keycloak deployment) | as today | as today | `workspace-backup` client: public, PKCE, consent, `offline_access`, bounce redirect URI |
| GitLab (GitLab deployments) | external or in the stack | as today | `dtaas-login` and `workspace-backup` applications |
| `user<N>` workspaces | `users` | none | `cap_drop: [NET_RAW]`; S3, OIDC and GitLab variables; admin on `127.0.0.1`; nginx `auth_request` |
| `seaweedfs` | `users` only | none | `weed server -s3`, `/data` volume, `s3.json`, `iam.json` |
| `storage-provision` | `users` | none | One-shot, profile `provision`: bucket, versioning, lifecycle (`NONCURRENT_VERSION_DAYS`, default 365), IAM files trusting login-relay, Keycloak client |

Admin credentials for SeaweedFS, Keycloak and GitLab exist only in
`storage-provision` and the operator's `.env`; workspaces never get
them. On Kubernetes the same pieces become a StatefulSet, a Job and a
ConfigMap; that port is out of scope here.

## Quality requirements

| Quality | Requirement | How |
| :--- | :--- | :--- |
| Security | Neighbouring workspaces cannot reach a workspace's tools or admin | nginx `auth_request`, `127.0.0.1`, socket directory `0700` |
| Security | No secret in logs, argv, status or page | Write-only fields; credentials via environment for `rclone` and git |
| Security | Configuration changes need the owner's session or socket | Session check, CSRF, socket |
| Reliability | One failing asset or connection never stops others | Per-asset errors and per-section validation |
| Reliability | No data loss from an empty or unmounted folder, or mass deletion | Empty-source refusal, `S3_MAX_DELETE` |
| Availability | Backups run while the page is unused | Credentials refreshed per round |
| Responsiveness | Sync never blocks HTTP | Background thread, as today |
| Portability | Cloud S3 by configuration | Standard protocols only |
| Maintainability | New files under 250 lines, functions under 25; files already over 250 do not grow; pylint 9.0+ | Small modules per concern |
| Dependencies | No new Python runtime dependency; one pinned `rclone` | Standard library HTTP, XML, locking |

## Decisions

| # | Decision | Reason |
| :--- | :--- | :--- |
| D1 | `rclone` for S3 | Sync, excludes and version restore built in |
| D2 | One backend per asset | Git pulls and S3 mirrors conflict on one folder |
| D3 | Free asset names `^[a-z0-9]{1,63}$`, no special names | Sharing is a server policy, not a client choice |
| D4 | Add and remove only | Renaming moves data across folders, prefixes and repositories |
| D5 | One bucket, prefix per user | Bucket limits and cost in clouds; prefix policies suffice |
| D6 | S3 via the deployment's identity provider, git via GitLab | Central revocation, short-lived credentials |
| D7 | GitLab connection alongside pasted tokens | Non-breaking; other git hosts keep working |
| D8 | Browser login with PKCE first; device flow later | Reuses the DTaaS session; works on any GitLab |
| D9 | Admin on `127.0.0.1`; CLI on a Unix socket in a `0700` directory | Nothing that changes configuration is reachable without the owner's session or account |
| D10 | Session check through login-relay's `/token/introspect`, also for nginx | One path for opaque GitLab and JWT Keycloak tokens; no crypto dependency; closes the front door |
| D11 | Hand-editable `config.toml` with version check | Page, CLI and editor coexist; comments lost on machine saves |
| D12 | Provisioning in the DTaaS repository | User list, compose and Keycloak live there |
| D13 | Fresh credentials every round | Revocation takes effect next round |
| D14 | One bounce redirect URI at login-relay for all users | Providers need exact URIs; adding a user must not change the registration |
| D15 | `cap_drop: [NET_RAW]` on workspaces | Internal OAuth and STS traffic is plain HTTP |
| D16 | GitLab deployments use GitLab directly | Decided; no Keycloak added to them |
| D17 | login-relay issues storage passes in both profiles | GitLab gives no signed token on refresh; one STS trust setup |
| D18 | One GitLab grant for S3 and git in GitLab deployments | One click; the S3-only user grants `write_repository` too |
| D19 | Grants end after 180 days; noncurrent versions after 365 days by default | Answers to open questions; both configurable |

## Open questions

None at the design level. Versions follow the latest stable
releases, pinned by the spike tasks S1 to S4 of the
[roadmap](../../FEATURE_ROADMAP.md).
