# Tasks: workspace storage backup

Implements the [plan](../plans/102-s3-backup.md); interfaces named here
are defined there, and the order and dependencies are in the
[roadmap](../FEATURE_ROADMAP.md#planned-work-in-order). Each task is
one reviewable unit with its own tests, ends with the plan's test
commands passing, and updates the docs it makes stale. Step-level,
test-first detail for a feature is written just before it starts.

## Spike

- [ ] **S1 SeaweedFS.** Pin the latest stable release `>= 4.40`.
  Confirm: versioning and `rclone copy --s3-version-at` restore;
  `NoncurrentVersionExpiration` on a versioned bucket;
  `AssumeRoleWithWebIdentity` with an ES256 JWT from an external
  issuer (`iss`, `aud`, JWKS URL) as login-relay will issue; a
  `${jwt:preferred_username}` prefix policy with `s3:prefix` denies
  another prefix; the denies and `GetBucketVersioning` allow of the
  threat model; `--s3-no-check-bucket`; role ARN format.
- [ ] **S2 rclone.** Pin the latest stable release with
  `--s3-version-at`, the `SeaweedFS` provider, session tokens,
  `--skip-links`, `--s3-no-check-bucket`; record the SHA-256.
- [ ] **S3 GitLab.** On the latest stable GitLab: non-confidential
  application with PKCE; exact redirect URIs; `openid` and
  `openid write_repository`; fields of `/oauth/token/info` and
  `/oauth/userinfo`; 2-hour access tokens; refresh rotation; no ID
  token on refresh.
- [ ] **S4 Keycloak.** `KC_HOSTNAME_BACKCHANNEL_DYNAMIC` gives
  internal backchannel URLs and the public `iss`; public
  `workspace-backup` client with PKCE, `offline_access` and consent;
  180-day offline maximum; login-relay's confidential client can
  introspect `workspace-backup` tokens.
  *Check*: findings recorded under "Spike findings"; design docs
  updated where they differ.

## F1 Backup foundation (PR 1, PR 2)

- [ ] **T101 Status contract.** `test_status_snapshot_unchanged` for a
  git-only config, before any change; record test count and coverage.
- [ ] **T102 Backend package.** `backup/*` and `git/backend.py`; move
  `SyncErrorKind` into `BackupErrorKind`; update imports. Tests:
  `test_scheduler_runs_each_backend`,
  `test_failure_in_one_backend_does_not_stop_others`,
  `test_stop_event_ends_round`. (PR 1.)
- [ ] **T103 Asset model and loader.** Name rule, `BACKUP_BACKEND`,
  `GIT_AUTH` (default `token`), per-section validation; drop
  `ASSET_NAMES`. Tests: `test_private_common_configs_unchanged`,
  `test_name_rejected[Private|my-asset|a.b|""|64 chars]`,
  `test_invalid_section_skipped_others_run`.
- [ ] **T104 Writer.** `read_snapshot`, `add_asset`, `remove_asset`,
  TOML emitter, atomic `0600`. Tests: `test_writer_refuses_changed_file`,
  `test_written_file_reloads_identically`.
- [ ] **T105 Wiring and live reload.** Tests:
  `test_reload_waits_for_round`, `test_unparseable_edit_keeps_running`.
- [ ] **T106 Loopback bind.** `main.py` default and
  `custom_startup.sh` use `127.0.0.1`. *Check*: port 8091 refuses
  from another container.
- [ ] **T107 Drop NET_RAW** on workspaces in the test compose files
  and `ci/` variants. (PR 2.)

## F2 S3 storage with static keys (PR 3, PR 4)

- [ ] **T201 SeaweedFS in test compose**: `users` network only, no
  ports or labels, `/data` volume, config mounts, healthcheck;
  host refuses, `user1` reaches. (PR 3.)
- [ ] **T202 S3 settings.** Tests: `test_prefix_is_derived`,
  `test_missing_endpoint_reports_not_configured`.
- [ ] **T203 Token store** `shared.py` and `login/store.py`. Tests:
  `test_store_save_is_atomic`, `test_connection_repr_hides_secret`.
- [ ] **T204 rclone runner and errors.** Tests:
  `test_credentials_never_in_argv`, `test_timeout_kills_group`.
- [ ] **T205 S3 backend.** Tests: `test_sync_refuses_empty_tree`,
  `test_excludes_on_every_call`, `test_versioning_off_refused`,
  `test_delete_limit_reported`.
- [ ] **T206 Image and integration.** Pinned `rclone`; round trip
  against SeaweedFS with a static identity. (PR 4.)

## F3 login-relay identity profiles (PR 5)

- [ ] **T301 Profile interface.** Add `pytest-cov` to
  `requirements-dev.txt`; record login-relay's test count and coverage;
  move today's Keycloak code out of `_helpers.py` behind `Profile`.
  Tests: existing tests pass unchanged.
- [ ] **T302 PKCE, nonce, cookies, compatibility.** PKCE S256 and
  `nonce` in the login; no `prompt` unless `LOGIN_PROMPT` is set;
  `Secure` on both cookies only with `TLS=true`; `KEYCLOAK_CLIENT_*`
  accepted as aliases; only the active profile's secret required.
  Tests: `test_authorize_url_has_pkce_and_nonce`,
  `test_redeem_sends_authorize_redirect_uri`,
  `test_cookies_secure_follow_tls`, `test_old_variable_names_work`.
- [ ] **T303 GitLab profile and adapter.** `/oauth/*` endpoints,
  `GITLAB_INTERNAL_URL`, ID-token checks, introspection adapter,
  revoke on logout and the signed-out page; cross-user check uses
  `introspect`. Tests with `tests/fakes/gitlab.py` and contract tests
  replaying spike S3 responses: `test_adapter_answer_matches_contract`,
  `test_unlisted_user_inactive`, `test_foreign_application_inactive`,
  `test_logout_ends_on_signed_out_page`.
- [ ] **T304 Oathkeeper cache.** `cache: {enabled: true, ttl: 30s}`.
  *Check*: compose login works with the Keycloak profile and with the
  GitLab profile against the fake GitLab service; today's Keycloak and
  Dex CI stacks pass unchanged.

## F4 login-relay storage endpoints (PR 6)

- [ ] **T401 Exchange.** `/token/exchange` (RFC 8693 shape), ES256
  signing from `SIGNING_KEY_FILE`, `/.well-known/jwks.json` and
  discovery; a one-shot service in the test compose generates the
  key set. Tests: `test_exchange_issues_5_minute_jwt`,
  `test_exchange_without_key_is_503`, `test_exchange_provider_down_is_502`,
  `test_exchange_refuses_unlisted_user`,
  `test_exchange_refuses_login_client_token`, `test_jwks_lists_two_keys`.
- [ ] **T402 Bounce.** `/login-relay/oauth/<provider>/callback`. Tests:
  `test_bounce_redirects_listed_user`, `test_bounce_refuses_unlisted`,
  `test_bounce_rejects_malformed_state`, `test_bounce_never_redeems`,
  `test_bounce_username_with_dot`, `test_bounce_unknown_provider`.
- [ ] **T403 Exposure.** *Check*: `/token/*`, `/authz/*`,
  `/.well-known/*` unreachable through Traefik in the test compose.

## F5 Admin login core and front door (PR 7)

- [ ] **T501 HTTP, providers, PKCE, pending.** Tests:
  `test_pkce_rfc7636_vector`, `test_state_carries_username`,
  `test_state_single_use`, `test_state_expires_after_10_minutes`.
- [ ] **T502 Flow and owner check** for GitLab and Keycloak. Tests:
  `test_owner_mismatch_stores_nothing_and_names_both`,
  `test_redirect_uri_is_bounce`.
- [ ] **T503 Relay client and session check.** `relay.py`,
  `session.py`. Tests: `test_inactive_session_denied`,
  `test_other_user_denied`, `test_relay_down_denies`.
- [ ] **T504 Credentials.** Refresh, rotation, exchange, STS, age
  limit. Tests: `test_rotated_token_saved_before_use`,
  `test_exchange_denied_is_s3_auth_failed`,
  `test_old_connection_is_login_expired`,
  `test_lost_rotation_is_login_expired`,
  `test_round_isolates_connection_failure`.
- [ ] **T505 Servers and routes.** TCP on `127.0.0.1`, control socket
  in a `0700` directory, callbacks, `/_auth`, control routes. Tests:
  `test_socket_directory_mode_0700`, `test_shutdown_within_8_seconds`.
- [ ] **T506 Front door.** nginx `auth_request /_auth` except `/ping` and
  today's public read-only routes (`/services`, `/health`,
  `/storage/status`),
  enabled when `LOGIN_RELAY_INTERNAL_URL` is set. *Check*: from another
  workspace, Jupyter and VS Code answer 401.
- [ ] **T507 Integration** with login-relay, Keycloak and SeaweedFS:
  connect, sync, cross-user and `DeleteObjectVersion` denied, removal
  from `WORKSPACE_USERS` stops the next round.

## F6 CLI (PR 8)

- [ ] **T601 Client** over the control socket. Tests:
  `test_service_not_running_message`.
- [ ] **T602 Commands** `asset list|add|remove|allow-delete`,
  `connect`, `disconnect`, `status`; secrets only from stdin. Tests:
  `test_asset_add_offline`, `test_connect_prints_link_and_waits`.

## F7 GitLab connection for git (PR 9)

- [ ] **T701 Git uses the connection** when `GIT_AUTH = "gitlab"`;
  host must be `GITLAB_URL`'s; `git ls-remote` after connect. Tests:
  `test_gitlab_auth_header`, `test_repo_unreachable_keeps_connection`.
- [ ] **T702 Credentials via environment** (`GIT_CONFIG_COUNT`) for
  every git credential. Tests: `test_no_credential_in_git_argv`,
  `test_token_auth_unchanged`.

## F8 `/config` page (PR 10)

- [ ] **T801 Routes.** Tests: `test_every_route_requires_session`,
  `test_state_has_no_secrets`.
- [ ] **T802 CSRF and headers.** Tests:
  `test_post_without_csrf_denied`, `test_foreign_origin_denied`,
  `test_security_headers_present`.
- [ ] **T803 Static page.** Playwright: connect GitLab, add S3 and git
  assets, allow deletions, remove, disconnect, changed-on-disk
  warning, expired session.

## F9 DTaaS deployments on login-relay (PR 11)

Every deployment in F9 also gets `cap_drop: [NET_RAW]` on workspaces,
a generated signing key set for login-relay, and
`LOGIN_RELAY_INTERNAL_URL`, which turns on the nginx front door once
the workspace image includes F5.

- [ ] **T901 `server`**: Oathkeeper and login-relay, GitLab profile,
  `TLS=false`; documented as test-only.
- [ ] **T902 `secure-server`**: GitLab profile with TLS.
- [ ] **T903 `secure-server_with_integrated-gitlab`**:
  `GITLAB_INTERNAL_URL=http://gitlab/gitlab`; `dtaas-login`
  application created with the bundled GitLab.
- [ ] **T904 `workspace/keycloak/production`**: Keycloak profile,
  `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true`.
  *Check* for each: login, own workspace only, logout; DTaaS docs
  updated; rollback documented.

## F10 Storage in DTaaS deployments (PR 12)

- [ ] **T1001 Provisioning job.** Bucket, versioning, lifecycle
  (`NONCURRENT_VERSION_DAYS`, default 365), `s3.json`, `iam.json`
  trusting login-relay; Keycloak `workspace-backup` client (bounce
  redirect URI). *Check*: second run changes
  nothing; adding a user needs no change.
- [ ] **T1002 Integration.** SeaweedFS on `dtaas-users`; storage
  variables on workspaces; `workspace-backup` GitLab application
  registration documented.
- [ ] **T1003 Operator docs and end-to-end.** Enabling, connecting,
  restore runbook, revocation, monitoring, durability, host backups
  holding tokens, the `MAIN_USER` invariant; `mkdocs.yml` and
  `mkdocs-github.yml` together. *Check*: round trip in a throwaway
  stack of each kind; `mkdocs build` passes.

## Spike findings

To be filled in by S1 to S4.
