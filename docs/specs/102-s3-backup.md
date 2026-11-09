# Spec: workspace storage backup to git and S3

| | |
| :--- | :--- |
| Issue | [workspace#102](https://github.com/INTO-CPS-Association/workspace/issues/102) |
| Status | Draft |
| Design | [design/storage](../design/storage/README.md), [LOGIN_RELAY](../design/auth/LOGIN_RELAY.md) |
| Roadmap | [FEATURE_ROADMAP.md](../FEATURE_ROADMAP.md) |
| Plan | [plan](../plans/102-s3-backup.md) |
| Tasks | [tasks](../tasks/102-s3-backup.md) |

## Problem

Workspace files are bind-mounted from host directories and protected
by nothing beyond the host filesystem. The git backup (#93) needs a
git remote and a hand-pasted token, and suits small text files. Large
or binary assets bloat a git history. Tokens are created, pasted and
renewed by hand, with no central revocation. MinIO is no longer a
viable default S3 server, so this feature adopts SeaweedFS. DTaaS
deployments authenticate with `traefik-forward-auth`, whose cookie no
other service can verify.

## Goals

1. Back up named folders (assets) to git or S3, one scheme per asset.
2. Keep previous versions of S3-backed files for point-in-time restore.
3. Centralise access through the deployment's identity provider
   (GitLab or Keycloak) and login-relay; revocation in one place; no
   per-user S3 keys in the default setup.
4. One login per provider, from the user's own browser, then nothing.
5. Manage assets and connections from a web page, a CLI or by editing
   `config.toml`.
6. Replace `traefik-forward-auth` with Oathkeeper and login-relay in
   `server`, `secure-server`, `secure-server_with_integrated-gitlab`
   and `workspace/keycloak/production`; GitLab deployments use GitLab
   directly.
7. SeaweedFS in Docker Compose now; cloud S3 later by configuration.
8. Existing git-backup configurations keep working.

## Non-goals

Restore from the page or CLI (runbook only); renaming assets; shared
areas chosen by the workspace; S3 FUSE mounts; Kubernetes manifests;
SeaweedFS replication; encryption at rest in SeaweedFS; device flow
and internal TLS (follow-ups).

## Users and scenarios

- **Workspace user** adds assets and connects once.
- **Operator** provisions storage and registers clients once, monitors
  status, restores files and revokes access.

1. In a GitLab deployment, user1 opens `/user1/config`, adds asset
   `models` on S3 and clicks Connect with GitLab: one consent, and S3
   and git backups both work ([STORAGE_GITLAB](../design/storage/STORAGE_GITLAB.md)).
2. In the Keycloak deployment, user1 connects Keycloak for S3 and,
   if configured, GitLab for git ([STORAGE_KEYCLOAK](../design/storage/STORAGE_KEYCLOAK.md)).
3. A terminal user does the same with `workspace-admin asset add` and
   `workspace-admin connect`, approving in their own browser.
4. A user deletes a folder by mistake; the operator restores it as it
   was an hour ago.
5. The S3 server is down; workspaces keep working, status reports
   `s3_unreachable`, backups resume afterwards.
6. The operator removes user1 from `WORKSPACE_USERS`; logins and S3
   backups stop at the next round.
7. A workspace with today's `config.toml` behaves as before.

## Requirements

### Login (login-relay, [LOGIN_RELAY](../design/auth/LOGIN_RELAY.md#requirements))

- **FR-1** login-relay meets LR-1 to LR-10: profiles `gitlab` and
  `keycloak`; PKCE login; ID-token checks; TLS-aware cookie;
  RFC 7662 introspection for both profiles; storage-pass exchange;
  callback bounce; internal-only endpoints; no secrets in logs.
- **FR-2** The four deployments run Oathkeeper and login-relay
  instead of `traefik-forward-auth`, with an introspection cache of
  30 seconds.

### Assets and configuration ([Storage schemes](../design/storage/STORAGE_SCHEMES.md), [config.toml](../design/workspace-admin/CONFIG_FILE.md))

- **FR-3** Asset names match `^[a-z0-9]{1,63}$`; no name is special.
- **FR-4** Each asset has one backend, `git` (default) or `s3`.
- **FR-5** Assets can be added and removed; removal deletes neither
  the folder nor stored copies.
- **FR-6** `config.toml` stays hand-editable. Page and CLI write it
  through one validator, atomically, refusing to overwrite a file
  changed since it was read; comments are lost on a machine save,
  with a warning before the first.
- **FR-7** Changes apply without restart; an invalid section is logged
  and skipped; the others run.
- **FR-8** `private` and `common` sections valid today keep their
  meaning; other `[assets.*]` sections, ignored today, are read from
  F1 on, as the release notes say.

### S3 backup

- **FR-9** An S3 asset is mirrored, including deletions, to
  `s3://<S3_BUCKET>/<MAIN_USER>/<name>/` every round; `MAIN_USER`
  equals the identity provider's username.
- **FR-10** Versioning keeps overwritten and deleted objects; noncurrent
  versions expire after `NONCURRENT_VERSION_DAYS` (default 365).
- **FR-11** The backend refuses to sync when versioning is off or the
  folder is missing or empty; skips symbolic links; stops at
  `S3_MAX_DELETE` deletions (default 1000) until the user allows them
  once.
- **FR-12** An operator can restore an asset or path to a point in
  time by a documented procedure.

### Connections ([OIDC security](../design/storage/OIDC_SECURITY.md), [Connect storage backends](../design/storage/CONNECT_BACKENDS.md), [Sign-in](../design/workspace-admin/SIGN_IN.md))

- **FR-13** Connections: `gitlab`, `keycloak`, `s3keys`; at most one
  of each. S3 uses the deployment's identity provider
  (`IDENTITY_PROVIDER`); in GitLab deployments one `gitlab` connection
  (`openid write_repository`) serves S3 and git.
- **FR-14** `gitlab` and `keycloak` connections are created by the
  browser authorization-code flow with PKCE (S256), public
  `workspace-backup` clients, and login-relay's one bounce address.
- **FR-15** Owner check before storing: the provider's username
  (`nickname`, `preferred_username`) equals `MAIN_USER`, or, with
  `GITLAB_OWNER_CHECK = "confirm"`, the user confirms; a mismatch names
  both.
- **FR-16** Every round: refresh, save any rotated refresh token
  atomically, exchange the access token at login-relay for a 5-minute
  storage pass, exchange the pass at STS for `S3_SESSION_SECONDS`
  (default 3600). Nothing is cached across rounds.
- **FR-17** A connection older than `CONNECTION_MAX_AGE_DAYS` (180)
  reports `login_expired`; Keycloak's offline maximum is also 180 days.
- **FR-18** Disconnect revokes at the provider, then deletes the
  connection.
- **FR-19** Git assets choose `GIT_AUTH = "gitlab"` or `"token"`
  (default); pasted `GIT_REPO_TOKEN` stays fully supported.

### Clients and status ([`/config` page](../design/workspace-admin/CONFIG_PAGE.md), [CLI](../design/workspace-admin/CLI.md), [sign-in](../design/workspace-admin/SIGN_IN.md))

- **FR-20** `/<user>/config` lists connections and assets with status,
  and adds, removes, connects, disconnects and allows deletions.
- **FR-21** CLI subcommands `asset list|add|remove|allow-delete`,
  `connect|disconnect <provider>`, `status`; `asset list|add|remove`
  work without the running service; a CLI login prints the provider
  link and waits.
- **FR-22** `/storage/status` adds `s3` and `connections` only when
  used, with the kinds in
  [Connect storage backends](../design/storage/CONNECT_BACKENDS.md#status-kinds);
  otherwise it is byte-identical to today's.

### Provisioning (DTaaS)

- **FR-23** One idempotent job creates the bucket with versioning and
  lifecycle; writes SeaweedFS `s3.json` and `iam.json` trusting
  login-relay; in the Keycloak deployment creates the
  `workspace-backup` client. Registration of GitLab applications is
  documented (or scripted for the integrated GitLab). No per-user
  step, file or secret.

## Security requirements ([Threat model](../design/storage/THREAT_MODEL.md))

- **SR-1** No secret in logs, process arguments, status, page
  responses or errors; secret fields are write-only; git credentials
  reach git through the environment.
- **SR-2** `.config/workspace-admin/` and `.local/share/workspace-admin/`
  are never uploaded.
- **SR-3** The admin's TCP listener binds `127.0.0.1`; control routes
  are served only on a Unix socket in a `0700` directory.
- **SR-4** nginx checks the session (`auth_request`) for every path
  except `/ping` and
  today's public read-only routes (`/services`, `/health`,
  `/storage/status`), so no workspace tool is reachable by bypassing
  Oathkeeper.
- **SR-5** `/config` and `/_auth` verify the session through
  login-relay's `/token/introspect` with `username == MAIN_USER`;
  `X-User-*` headers are ignored; failure denies.
- **SR-6** State-changing requests need an `X-CSRF-Token` (HMAC of the
  session username) and `Origin == PUBLIC_BASE_URL`.
- **SR-7** OAuth `state` and PKCE verifier are server-side, single
  use, bound to the session username, valid 10 minutes.
- **SR-8** The page sends CSP `default-src 'self'; script-src 'self';
  frame-ancestors 'none'`, `nosniff`, `Referrer-Policy: no-referrer`,
  `Cache-Control: no-store`.
- **SR-9** The S3 role trusts only login-relay's issuer, allows only
  the actions in the
  [threat model](../design/storage/THREAT_MODEL.md#storage-side-hardening)
  under the user's prefix, and denies version deletion and bucket
  configuration changes.
- **SR-10** SeaweedFS 4.40 or later (CVE-2026-77298); off-host
  endpoints use HTTPS.
- **SR-11** Workspaces run with `cap_drop: [NET_RAW]`.
- **SR-12** login-relay's signing key, and SeaweedFS, Keycloak and
  GitLab admin credentials, never reach a workspace.

## Non-functional requirements

- **NFR-1** Sync never blocks HTTP; shutdown within 8 seconds.
- **NFR-2** Every network call and subprocess has a time limit.
- **NFR-3** No new Python runtime dependency in workspace-admin; one
  pinned `rclone`; login-relay keeps its current dependencies.
- **NFR-4** New files under 250 lines, functions under 25, pylint 9.0+;
  files already over 250 lines do not grow.
- **NFR-5** Coverage of workspace-admin and login-relay does not drop.

## Acceptance criteria

1. In each of the four deployments, a user logs in through login-relay
   and reaches only their own workspace.
2. After one GitLab connect, a file in an S3 asset appears under
   `<bucket>/<user>/<name>/` and a git asset pushes, within one round.
3. A locally deleted file can be restored from its previous version.
4. Connecting as another user is refused, stores nothing, names both.
5. User A's credentials get `AccessDenied` under user B's prefix and
   on `DeleteObjectVersion`.
6. Removing a user from `WORKSPACE_USERS`, or revoking at the provider,
   stops their S3 backup next round.
7. From another workspace, requests to `user1:8080` (Jupyter, VS Code,
   `/config`, `/_auth`) and `user1:8091` are denied.
8. `/token/exchange` and `/token/introspect` are unreachable through
   Traefik.
9. A POST without CSRF token or with a foreign `Origin` is denied.
10. Logs, status, page responses and the bucket contain no secret; no
    token appears in `ps` output.
11. Today's configurations and tests pass unchanged;
    `/storage/status` is byte-identical for them.
12. A back-up, delete, restore round trip passes against real
    login-relay, Keycloak and SeaweedFS, and against login-relay's
    GitLab fake; GitLab contract tests replay responses recorded from
    a real GitLab.
13. Logging out ends on login-relay's signed-out page, not in a silent
    re-login.
