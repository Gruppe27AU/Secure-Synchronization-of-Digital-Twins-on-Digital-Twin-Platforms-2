# Storage design

Design of workspace backup to git and S3 storage, with connections
created by GitLab or Keycloak logins through login-relay. Issue
[workspace#102](https://github.com/INTO-CPS-Association/workspace/issues/102).

## Reading order

| Order | Document | Read it for |
| :--- | :--- | :--- |
| 1 | [Storage schemes](STORAGE_SCHEMES.md) | Assets, git and S3 backup, SeaweedFS storage, cloud S3 |
| 2 | [LOGIN_RELAY](../auth/LOGIN_RELAY.md) | How users log in, what Oathkeeper and login-relay do, login-relay requirements and API |
| 3 | [Storage in GitLab deployments](STORAGE_GITLAB.md) | Worked examples: connecting and a backup round with GitLab |
| 4 | [Storage in the Keycloak deployment](STORAGE_KEYCLOAK.md) | The same with Keycloak |
| 5 | [OIDC security](OIDC_SECURITY.md) | Identity profiles, tokens and lifetimes, flows, Keycloak and GitLab compared |
| 6 | [Connect storage backends](CONNECT_BACKENDS.md) | Connections, credentials per round, life cycle, status kinds, settings |
| 7 | [Threat model and protection](THREAT_MODEL.md) | Actors, boundaries, threats, controls, residual risks |
| 8 | [workspace-admin design](../workspace-admin/README.md) | Inside the workspace: requirements, `config.toml`, the `/config` page, direct PKCE sign-in, CLI, login core |
| 9 | [Complete system design](SYSTEM_DESIGN.md) | Context, deployment, quality requirements, decisions |
| 10 | [Decision record: Route B](ROUTE_B_DECISION.md) | Why GitLab deployments use GitLab directly and login-relay issues storage passes |

Orders 1 to 4 give the picture with examples; 5 to 9 are the
reference design, with the workspace-admin documents in their own
folder; 10 records why.

## Related

- [Feature roadmap](../../FEATURE_ROADMAP.md): every planned pull
  request in build order, with dependencies.
- [Spec](../../specs/102-s3-backup.md),
  [plan](../../plans/102-s3-backup.md) and
  [tasks](../../tasks/102-s3-backup.md).
- Git backup as built: `workspaces/src/admin/README.md` and
  `DOCUMENTATION.md`.
