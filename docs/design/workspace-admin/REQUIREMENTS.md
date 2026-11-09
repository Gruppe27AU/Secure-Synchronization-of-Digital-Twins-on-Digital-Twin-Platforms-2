# workspace-admin requirements

Part of the [workspace-admin design](README.md). What the
`workspace-admin` package must do once the storage backup of
[workspace#102](https://github.com/INTO-CPS-Association/workspace/issues/102)
is in place. Feature-level requirements are in the
[spec](../../specs/102-s3-backup.md); this document states them from
the package's point of view and adds what the package needs to be
built, run and maintained.

## What the package is

A Python service, one process per workspace container, running as the
workspace user (`MAIN_USER`) and started by
`workspaces/src/startup/custom_startup.sh`. It is installed with
`pipx` from a wheel built in the image (`install_admin.sh`) and exposes
one command, `workspace-admin`.

| Responsibility | Today | After #102 |
| :--- | :--- | :--- |
| Service discovery (`/services`) | Yes | Unchanged |
| Health (`/health`) | Yes | Unchanged |
| Backup status (`/storage/status`) | Git assets | Git and S3 assets, connections |
| Backup of assets | Git, `private` and `common` only | Git or S3, any asset name |
| Configuration | Hand-edited `config.toml`, read at start | Hand-edited, `/config` page or CLI; live reload |
| Credentials | Pasted git tokens | Connections from a direct PKCE sign-in; pasted tokens still accepted |
| Session checks | None | For `/config` and for nginx's front door, through login-relay |
| Command line | Flags to start the service | Subcommands to manage assets, connections and status |

## Users and their needs

| User | Needs |
| :--- | :--- |
| Workspace user in a browser | See backup health; add and remove assets; connect and disconnect GitLab or Keycloak; never paste a token |
| Workspace user in a terminal | Everything the page offers, scriptable, without a browser except for approving a sign-in |
| Operator | Set deployment-wide settings once; read status for monitoring; revoke access centrally; no per-user step |
| Developer | Small modules with clear seams; tests without network; no new runtime dependency |

## Functional requirements

- **WA-1** Serve today's public routes unchanged: `/`, `/services`,
  `/health`, `/storage/status` (spec FR-22).
- **WA-2** Back up each configured asset with its backend, git or S3,
  on a fixed interval, one failing asset never stopping the others
  (FR-4, FR-9 to FR-11).
- **WA-3** Read `config.toml` with per-section validation; apply
  changes without restart ([Config file](CONFIG_FILE.md); FR-6 to FR-8).
- **WA-4** Offer a `/config` page to list, add and remove assets, and
  connect, disconnect and allow deletions ([Config page](CONFIG_PAGE.md);
  FR-20).
- **WA-5** Acquire and keep its own tokens with a direct PKCE sign-in
  to GitLab or Keycloak, started from the page or the CLI
  ([Sign-in](SIGN_IN.md); FR-13 to FR-18).
- **WA-6** Offer CLI subcommands with the same feature set as the page
  ([CLI](CLI.md); FR-21).
- **WA-7** Each round, obtain fresh credentials once: refresh grants,
  exchange at login-relay for a storage pass, exchange the pass at STS
  ([Login core](LOGIN_CORE.md); FR-16).
- **WA-8** Verify browser sessions for `/config` and for nginx's
  `auth_request /_auth` through login-relay (SR-4, SR-5).
- **WA-9** Report each asset's last attempt, success and error kind,
  and each connection's state, without secrets (FR-22).

## Security requirements

- **WA-S1** Listen on `127.0.0.1` for nginx; serve control routes only
  on a Unix socket in a `0700` directory (SR-3).
- **WA-S2** Never write a secret to logs, process arguments, status,
  page responses or `config.toml` (except the pasted git token the user
  chose); store connections in `0600` files (SR-1).
- **WA-S3** Never upload `.config/workspace-admin/` or
  `.local/share/workspace-admin/` (SR-2).
- **WA-S4** Require a verified session, a CSRF token and a matching
  `Origin` for every state-changing page request (SR-5, SR-6).
- **WA-S5** Keep OAuth `state` and PKCE verifiers server-side, single
  use, bound to the user, valid 10 minutes (SR-7).
- **WA-S6** Refuse a connection whose provider username differs from
  `MAIN_USER` (FR-15).

The threats behind these are in the
[storage threat model](../storage/THREAT_MODEL.md).

## Non-functional requirements

| Quality | Requirement |
| :--- | :--- |
| Responsiveness | No sync, sign-in or network call blocks the HTTP server |
| Shutdown | Ends within 8 seconds, including a running `git` or `rclone` |
| Time limits | Every network call and subprocess has one |
| Dependencies | No new Python runtime dependency; one pinned `rclone` binary in the image |
| Code size | New files under 250 lines, functions under 25; files already over 250 lines do not grow |
| Quality gates | pylint 9.0 or better; coverage not below the baseline recorded before F1 |
| Portability | Nothing specific to SeaweedFS, GitLab or Keycloak outside configuration and provider modules |

## Interfaces

| Interface | Consumer | Documented in |
| :--- | :--- | :--- |
| Public HTTP routes | DTaaS website, monitoring | `workspaces/src/admin/DOCUMENTATION.md` |
| `/config` page and `/config/api/*` | User's browser through nginx | [Config page](CONFIG_PAGE.md) |
| `/config/callback/<provider>` | User's browser, bounced by login-relay | [Sign-in](SIGN_IN.md) |
| `/_auth` | nginx `auth_request` | [Login core](LOGIN_CORE.md#session-verification) |
| Control socket `/control/*` | The CLI | [CLI](CLI.md#control-socket) |
| `config.toml` | User, page, CLI | [Config file](CONFIG_FILE.md) |
| Environment variables | Operator, through compose | [Connect storage backends](../storage/CONNECT_BACKENDS.md#deployment-wide-settings) |
| login-relay `/token/introspect`, `/token/exchange` | The login core | [LOGIN_RELAY](../auth/LOGIN_RELAY.md#api) |

## Compatibility

- Running `workspace-admin` without a subcommand serves as today.
- Every `config.toml` with `[assets.private]` or `[assets.common]` that
  is valid today keeps its meaning; other `[assets.*]` sections, ignored
  today, become active (release notes say so).
- `/storage/status` is byte-identical for configurations without S3
  assets, connections or GitLab-connected git assets.
- `--host` defaults to `127.0.0.1`; passing `--host 0.0.0.0` explicitly
  still works for development, with a warning.

## Out of scope

Restore through the page or CLI (runbook only), renaming assets,
shared areas between users, device-flow sign-in (a follow-up), and
splitting `git/sync.py` (technical debt).
