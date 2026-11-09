# Security threat model and protection

Part of the [storage design](README.md). Covers the backup data, the
connections, the `/config` page, the CLI, the storage servers, and
the workspace front door they all depend on.

## What is protected

| Asset | Why it matters |
| :--- | :--- |
| Users' backed-up files and their versions | The point of the feature; may hold unpublished research |
| Connections (refresh tokens, static keys, pasted git tokens) | Long-lived; whoever holds one can back up as the user |
| `config.toml` | Decides where files go; a changed URL redirects data |
| SeaweedFS, Keycloak and GitLab admin credentials | Control every user's storage and identity |
| login-relay's signing key | Whoever holds it can mint storage passes for any listed user |

## Actors

| Actor | Can reach | Trusted? |
| :--- | :--- | :--- |
| Workspace owner | Their workspace through Oathkeeper; its terminal | Yes, for their own data |
| Other DTaaS users | Their own workspace, and from it every container on `dtaas-users` | No |
| Any website the owner visits | The owner's browser | No |
| Script running in the owner's tools (a notebook, an HTML file) | The owner's origin and session | No, but can act as the owner |
| Internet attacker | Traefik's public ports | No |
| Operator | Hosts, compose, Keycloak, GitLab, SeaweedFS | Yes |

## Trust boundaries

```text
Internet ──► Traefik ──► Oathkeeper ──► workspace nginx :8080 ──► Jupyter, VS Code, VNC
                                          │                   └─► admin 127.0.0.1:8091
                       other workspaces on dtaas-users reach :8080 directly
workspace terminal ──► admin control socket (0600 in a 0700 directory)
admin ──► Keycloak or GitLab   admin ──► login-relay (introspect, exchange)
admin ──► STS + S3 (SeaweedFS trusts login-relay's keys)   admin ──► git
```

## The front-door problem (exists today)

Every workspace shares `dtaas-users`, and nginx listens on all
interfaces. Behind it, Jupyter runs without a token and with XSRF
checks off, and VS Code runs with `--auth none`. So today any
workspace can run code in any other workspace by calling
`user1:8080/user1/api/kernels` directly, bypassing Oathkeeper. That
also defeats everything below: code running as user1 can read
user1's tokens and use the control socket.

This design therefore requires two fixes before production (roadmap
F5 and F9), not as follow-ups. Both rely on login-relay and
Oathkeeper, which F9 brings to the four deployments:

1. **Session check at the front door.** nginx calls
   `auth_request /_auth` for every location except `/ping` and
   today's public read-only routes (`/services`, `/health`,
   `/storage/status`).
   `/_auth` is served by the admin's session verifier (the same check
   as layer 2 below) and answers 204, 401, or 503 when login-relay is
   unreachable. Requests that bypass Oathkeeper then fail at nginx.
2. **No packet spoofing between tenants.** Workspaces run with
   `cap_drop: [NET_RAW]`, so one tenant cannot ARP-spoof another and
   read the plain-HTTP traffic on `dtaas-users` (refresh grants,
   introspection and exchange calls, STS replies with secret keys).
   TLS on internal hops is the alternative where `NET_RAW` is needed.

## Protection layers for the `/config` page and control socket

0. **Front door** (above): nginx session check on every path.
1. **Oathkeeper**: asks login-relay about the cookie and requires the
   username to match the path ([LOGIN_RELAY](../auth/LOGIN_RELAY.md)).
2. **Session verification in the admin.** Every `/config` request
   must carry a `dtaas_access_token` that login-relay's
   `/token/introspect` reports `active` (a live token of the login
   client, user listed) with `username == MAIN_USER`. `X-User-*`
   headers are ignored. login-relay unreachable means 503, never a
   pass ([Login core](../workspace-admin/LOGIN_CORE.md#session-verification)).
3. **Forged-request protection.** The cookie is `SameSite=Lax`. Every
   POST needs an `X-CSRF-Token` equal to an HMAC of the session
   username under a key created at service start, and an `Origin`
   equal to `PUBLIC_BASE_URL`. The token survives the cookie's
   rotation. GET changes nothing except OAuth callbacks.
4. **OAuth callback protection.** Random `state` and PKCE verifier,
   server-side, single use, 10 minutes, bound to the session username
   of whoever started or completes the login. The verifier never
   leaves the admin. Owner checks run before anything is stored
   ([OIDC security](OIDC_SECURITY.md#owner-checks-at-connect-time)).
   Providers return only to login-relay's one bounce address, which
   forwards only to `/<user>/config/callback/<provider>` for a user in
   `WORKSPACE_USERS` and never redeems the code.
5. **Input validation.** Names `^[a-z0-9]{1,63}$`; git URLs HTTPS
   only; the GitLab connection only for `GITLAB_URL`'s host; existing
   branch checks. One validator for page, CLI and hand edits. Saves
   are atomic and refused if the file changed since it was read.
6. **Secret handling.** Secrets are write-only: never rendered, never
   returned. `Cache-Control: no-store`. Logs record events, not values.
   Disconnect revokes, then deletes.
7. **Browser hardening.** Static HTML and one script; DOM filled with
   `textContent` only. Headers: `Content-Security-Policy: default-src
   'self'; script-src 'self'; frame-ancestors 'none'`, `nosniff`,
   `Referrer-Policy: no-referrer`.
8. **Listeners.** The admin's TCP port binds `127.0.0.1`. The control
   socket lives in a directory of mode `0700` owned by the workspace
   user, so the socket's own mode does not matter; a stale socket is
   removed at start.

## Threats and controls

| ID | Threat | Control |
| :--- | :--- | :--- |
| TM0 | Other workspace runs code in user1's Jupyter or VS Code via `user1:8080` | 0 |
| TM1 | Other user opens `/user1/config` in a browser | 1, 2 |
| TM2 | Other workspace calls `user1:8080/user1/config/api/...` | 0, 2 |
| TM3 | Other workspace calls `user1:8091` | 8 |
| TM4 | Spoofed `X-User-Name` | 2 (ignored) |
| TM5 | Malicious site submits a form in the owner's browser | 3 |
| TM6 | Replayed or leaked callback URL | 4 |
| TM7 | Attacker's account planted as the owner's connection | 4 (owner checks) |
| TM8 | Asset name with `..` or `/` | 5 |
| TM9 | Page overwrites a hand edit | 5 (version check) |
| TM10 | Token leak through page, cache, Referer or logs | 6, 7 |
| TM11 | Script injection into the page | 7 |
| TM12 | Network access to control commands | 8 |
| TM13 | Sniffing tokens on `dtaas-users` | `cap_drop: [NET_RAW]`, or TLS |
| TM14 | Stolen S3 credentials delete history | Role policy denies version deletion |
| TM15 | Stolen git token rewrites history | Protected branch, no force-push |
| TM16 | Unmounted volume mirrored as "delete all" | Empty-source refusal, `--max-delete` |
| TM17 | SeaweedFS trust-policy bypass ([CVE-2026-77298](https://osv.dev/vulnerability/CVE-2026-77298)) | Pin 4.40 or later |
| TM18 | Abandoned grant used months later | 180-day maximum (Keycloak setting, `CONNECTION_MAX_AGE_DAYS`) |
| TM19 | Git token visible in process listings | Credentials via `GIT_CONFIG_*` environment (F7) |
| TM20 | login-relay's signing key stolen | Docker secret, never in workspaces; rotation with overlapping keys |
| TM21 | `/token/exchange` or `/token/introspect` reached from the internet | Traefik routes only `/login-relay` and `/logout`; compose test |
| TM22 | Bounce used as an open redirect | Fixed target path; user must be listed; `state` format checked |
| TM23 | Any GitLab account logs in (public `gitlab.com`) | `WORKSPACE_USERS` allow-list in login-relay |

## Storage-side hardening

**S3 role** (SeaweedFS IAM or AWS IAM):

- Allow `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` on
  `<bucket>/${jwt:preferred_username}/*`.
- Allow `s3:ListBucket` on `<bucket>`, limited by `s3:prefix` to
  `${jwt:preferred_username}/*`; allow `s3:GetBucketVersioning` on
  `<bucket>`.
- Deny `s3:DeleteObjectVersion`, `s3:PutBucketVersioning`,
  `s3:PutLifecycleConfiguration`, `s3:PutBucketPolicy`,
  `s3:CreateBucket`. Stolen credentials can hide current objects, but
  every version survives.
- Trust policy: issuer login-relay, audience `workspace-backup`.

Shared areas between users are an operator decision in this policy,
not something a workspace can request.

**Git**: protect the backup branch against force-push (the sync never
force-pushes); pasted tokens should be project access tokens with
only `write_repository`; the GitLab connection asks only for
`openid write_repository`.

## Residual risks

1. **Shared origin.** `/user1/config`, Jupyter and VS Code share one
   origin, and the cookie has `path=/`. A script running in the owner's
   tools can drive the page, but it can already run any command as the
   owner. The page protects against other users and the network, not
   against the owner's own compromised tools. Isolation would need a
   host name per workspace.
2. **Owner processes read tokens.** By design: the backup runs as the
   owner.
3. **Host-side copies.** Tokens live under `$PERSISTENT_DIR`, the host
   bind mount `./files/<user>`. Any host backup of `./files` copies
   long-lived grants. Operators should protect such backups like
   credentials; the 180-day maximum bounds their life.
4. **Revocation latency.** A revoked grant stops the next round, but
   STS credentials already issued stay valid up to
   `S3_SESSION_SECONDS`. Logged-out browser sessions stay valid for up
   to 30 seconds (the introspection caches).
5. **Until F9 lands.** Deployments still on `traefik-forward-auth`
   have no cookie the admin can verify. There the front-door check stays
   off (it is enabled only when `LOGIN_RELAY_INTERNAL_URL` is set),
   today's neighbour exposure remains, and the page and CLI login are
   unavailable. F9 removes this for all four deployments.
6. **Single SeaweedFS on the same disk** loses data with the disk
   ([storage schemes](STORAGE_SCHEMES.md#durability-limit)).
