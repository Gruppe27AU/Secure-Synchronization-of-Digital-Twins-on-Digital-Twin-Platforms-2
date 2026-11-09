# The `/config` page

Part of the [workspace-admin design](README.md). A web page served by
workspace-admin at `https://<server>/<user>/config` for filling in and
managing the workspace's backup configuration: assets in
[`config.toml`](CONFIG_FILE.md) and connections made by
[sign-in](SIGN_IN.md). The [CLI](CLI.md) offers the same features.

## What the user sees

```text
Backup configuration for user1                         [Reload]

Connections
  GitLab     Connected as user1 since 2026-10-17     [Disconnect]
  Keycloak   Not connected                            [Connect]

Assets
  Name      Backend  Uses     Last success         Last error
  models    S3       GitLab   2026-10-17 09:05     -                 [Remove]
  thesis    git      GitLab   2026-10-17 09:05     -                 [Remove]
  private   git      token    2026-10-16 18:00     auth_failed       [Remove]

Add asset
  Name [________]  Backend (o) S3  ( ) git
  git only: URL [______________]  Branch [main]  Auth: GitLab | token [****]
                                                                     [Add]
```

- Connections appear only when configured for the deployment (GitLab
  when `GITLAB_URL` is set, Keycloak in the Keycloak deployment, S3 keys
  when `S3_AUTH=keys`).
- **Allow deletions** appears on an asset reporting `s3_delete_limit`.
- For a git URL on `GITLAB_URL`'s host the page offers the GitLab
  connection; for other hosts a write-only token field.
- A "changed on disk, reload" banner appears when a save is refused
  with `config_changed`; a one-time warning precedes the first save of
  a hand-edited file (comments are lost).

## Flows

| Action | Request | Then |
| :--- | :--- | :--- |
| Open the page | `GET /config`, then `GET /config/api/state` | Page renders from the JSON |
| Add an asset | `POST /config/api/assets` | Writer saves; reload; row appears |
| Remove an asset | `POST /config/api/assets/models/remove` | Writer saves; reload; row disappears |
| Allow deletions | `POST /config/api/assets/models/allow-delete` | Next round runs without the brake |
| Connect | `POST /config/api/connections/gitlab/connect` | Browser goes to the provider ([Sign-in](SIGN_IN.md#started-from-the-page)) |
| Confirm account | `POST /config/api/connections/gitlab/confirm` | Only in `GITLAB_OWNER_CHECK=confirm` mode |
| Disconnect | `POST /config/api/connections/gitlab/disconnect` | Revoked and deleted |
| Store S3 keys | `PUT /config/api/connections/s3keys` | Only when `S3_AUTH=keys` |

Example: adding a git asset.

```http
POST /user1/config/api/assets HTTP/1.1
Host: dtaas.example.org
Content-Type: application/json
Origin: https://dtaas.example.org
X-CSRF-Token: 9f2c...

{"name": "thesis", "backend": "git",
 "git": {"url": "https://gitlab.example.org/alice/thesis.git",
         "branch": "main", "auth": "gitlab"},
 "expected_digest": "3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}
```

```json
{"ok": true, "digest": "a7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a"}
```

Refusals answer `400 {"error": "<kind>"}` with kinds such as
`name_invalid`, `name_taken`, `url_invalid`, `config_changed`; `401`
when the session is missing or expired; `403` for a bad CSRF token or
`Origin`.

## State endpoint

```json
{"user": "user1", "csrf_token": "9f2c...",
 "digest": "3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
 "offer": {"s3": true, "git": true, "connections": ["gitlab"]},
 "connections": {"gitlab": {"state": "connected", "subject": "user1",
                            "since": "2026-10-17T09:00:00Z"}},
 "assets": [{"name": "models", "backend": "s3", "uses": "gitlab",
             "last_success": "2026-10-17T09:05:00Z", "last_error": null}]}
```

It never contains a token, a key or a pasted git token.

## Protection

Summarised from the
[threat model](../storage/THREAT_MODEL.md#protection-layers-for-the-config-page-and-control-socket):

- nginx's `auth_request /_auth` and the route itself both verify the
  session through login-relay; `X-User-*` headers are ignored.
- Every POST and PUT needs `X-CSRF-Token` (an HMAC of the username with
  a key created at start) and `Origin` equal to `PUBLIC_BASE_URL`.
- Responses carry `Content-Security-Policy: default-src 'self';
  script-src 'self'; frame-ancestors 'none'`, `X-Content-Type-Options:
  nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`.
- Secret fields are write-only.

## Expired session

The page's JSON calls get `401` when the DTaaS session has expired. The
page then shows "Session expired" with a **Reload** button; reloading
goes through Oathkeeper and login-relay and returns to the page.

## Implementation notes

- One static HTML file and one script (`web/static/index.html`,
  `app.js`); the script fills the page with `textContent`, never
  `innerHTML`, so no template engine is needed.
- Bodies are JSON, so FastAPI needs no form-parsing dependency.
- Routes live in `web/config_routes.py`; CSRF in `web/csrf.py`;
  headers in `web/headers.py`; all call the same functions as the
  control socket.
- nginx gains `location` blocks for `/<user>/config` and the internal
  `/_auth`.
- Playwright tests cover connecting, adding and removing assets,
  allowing deletions, disconnecting, the changed-on-disk banner and the
  expired session (task T803).
