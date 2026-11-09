# workspace-admin CLI

Part of the [workspace-admin design](README.md). Subcommands that give
a terminal user everything the [`/config` page](CONFIG_PAGE.md)
offers: managing assets in [`config.toml`](CONFIG_FILE.md), signing in
([Sign-in](SIGN_IN.md)) and reading status. With no subcommand,
`workspace-admin` starts the service as today.

## Commands

| Command | Does | Needs the running service |
| :--- | :--- | :--- |
| `asset list` | Assets with backend, connection and last result | No (status columns only with it) |
| `asset add <name> --backend s3` | Adds an S3 asset | No |
| `asset add <name> --backend git --url <url> --branch <b> --auth gitlab\|token` | Adds a git asset; a token is read from stdin or a prompt | No |
| `asset remove <name>` | Removes an asset; data stays | No |
| `asset allow-delete <name>` | Lifts the deletion brake for one round | Yes |
| `connect gitlab\|keycloak` | Signs in: prints a link, waits for approval | Yes |
| `connect s3keys` | Stores a static key pair read from a prompt | Yes |
| `disconnect gitlab\|keycloak\|s3keys` | Revokes and deletes the connection | Yes |
| `status` | Connections and per-asset status | Yes |

Without the service, `asset list|add|remove` use the writer directly
and the change applies at the next start; the others say "workspace-admin
is not running".

## Examples

```bash
workspace-admin asset add models --backend s3
workspace-admin asset add thesis --backend git \
    --url https://gitlab.example.org/alice/thesis.git --branch main --auth gitlab
workspace-admin connect gitlab
workspace-admin status
```

Signing in from a terminal:

```console
$ workspace-admin connect gitlab
Open this link in the browser you use for DTaaS:
  https://gitlab.example.org/oauth/authorize?client_id=8a1f...&redirect_uri=https%3A%2F%2Fdtaas.example.org%2Flogin-relay%2Foauth%2Fgitlab%2Fcallback&response_type=code&scope=openid+write_repository&state=user1.k8Qz...&code_challenge=Qm1...&code_challenge_method=S256
Waiting for approval (10 minutes, Ctrl-C to cancel)...
Connected as user1.
```

Status:

```console
$ workspace-admin status
Connections
  gitlab    connected as user1 since 2026-10-17 09:00
Assets
  models    s3    last success 2026-10-17 09:05
  thesis    git   last success 2026-10-17 09:05
  private   git   last error   auth_failed at 2026-10-16 18:00
```

A pasted git token, read from stdin so it stays out of shell history
and process listings:

```console
$ workspace-admin asset add notes --backend git \
    --url https://git.example.com/me/notes.git --branch main --auth token < token.txt
Added asset notes.
```

## Exit codes

| Code | Meaning |
| :--- | :--- |
| 0 | Done |
| 1 | Refused: invalid input, `config_changed`, owner mismatch, sign-in cancelled or timed out |
| 2 | Usage error (`argparse`) |
| 3 | The service is not running and the command needs it |

## Control socket

The CLI talks to the running service over a Unix socket at
`/tmp/workspace-admin-<uid>/control.sock`. The service creates the
directory with mode `0700`, refuses to start if it exists with another
owner or mode, removes a stale socket, binds the socket itself and
hands it to `uvicorn`. The directory's mode is the authentication: only
the workspace user can connect, and nginx never proxies it.

| Route | Purpose |
| :--- | :--- |
| `GET /control/state` | Same JSON as the page's state endpoint, without the CSRF token |
| `POST /control/assets`, `POST /control/assets/<name>/remove`, `POST /control/assets/<name>/allow-delete` | Same as the page |
| `POST /control/connections/<provider>/connect` | Start a sign-in; returns `poll_id` and `authorize_url` |
| `GET /control/logins/<poll_id>` | Wait up to 30 seconds; `pending`, `connected`, `needs_confirmation` or an error kind |
| `POST /control/connections/<provider>/confirm` | Confirm the GitLab account in `confirm` mode |
| `POST /control/connections/<provider>/disconnect` | Disconnect |
| `PUT /control/connections/s3keys` | Store a static key pair |

The service runs two `uvicorn` servers in one event loop: TCP on
`127.0.0.1:${ADMIN_SERVER_PORT}` for nginx and the socket for the CLI.

## Signing in from the CLI: options considered

| Option | Works with the browser outside the container | Verdict |
| :--- | :--- | :--- |
| CLI starts the sign-in; the user's browser completes it on the workspace callback | Yes | **Chosen** |
| Device flow: short code, approve anywhere | Yes, if the provider supports it | Follow-up |
| Loopback redirect to `127.0.0.1:<port>` | No: the browser cannot reach the container | Rejected |
| User pastes a code shown on a page into the CLI | Yes, with an extra copy step | Rejected |
| CLI redeems the code and writes token files itself | Yes, but duplicates OAuth code and races token rotation | Rejected |

## Design

| Module | Responsibility |
| :--- | :--- |
| `main.py` | `argparse` subparsers; no subcommand starts the service |
| `cli/client.py` | Socket client with a 35-second timeout |
| `cli/commands.py` | One function per subcommand; prints, never decides policy |

Validation, writing and sign-in live in `admin/assets` and
`admin/login`; the CLI and the page are thin clients of the same
functions. `asset add` takes every value as a flag and does not prompt
with `--yes`, so it can be scripted.
