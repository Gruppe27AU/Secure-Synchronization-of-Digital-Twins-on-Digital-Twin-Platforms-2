# workspace-admin design

Design of the `workspace-admin` service that runs in every workspace:
its requirements, how users fill in and manage its configuration from
a web page or a terminal, and how it signs in to GitLab or Keycloak on
its own with PKCE. Code: `workspaces/src/admin`. Issue
[workspace#102](https://github.com/INTO-CPS-Association/workspace/issues/102).

## Reading order

| Order | Document | Read it for |
| :--- | :--- | :--- |
| 1 | [Requirements](REQUIREMENTS.md) | What the package must do, for whom, and under which constraints |
| 2 | [Architecture](ARCHITECTURE.md) | Packages, listeners, files, threads; start, round and stop |
| 3 | [`config.toml`](CONFIG_FILE.md) | File format, rules, compatibility, safe writing, live reload |
| 4 | [The `/config` page](CONFIG_PAGE.md) | Layout, flows, routes with examples, protection |
| 5 | [Direct PKCE sign-in](SIGN_IN.md) | Signing in from the page or the CLI, with diagrams, examples and failures |
| 6 | [CLI](CLI.md) | Subcommands matching the page, examples, control socket |
| 7 | [Login core](LOGIN_CORE.md) | Modules, session checks, credentials per round, error kinds |

## Related

- [Storage design](../storage/README.md): storage schemes, connections,
  threat model and system design that workspace-admin implements.
- [LOGIN_RELAY](../auth/LOGIN_RELAY.md): the service that logs users in
  and issues storage passes.
- [Feature roadmap](../../FEATURE_ROADMAP.md): when each part is built
  (F1, F2, F5 to F8).
- [Spec](../../specs/102-s3-backup.md),
  [plan](../../plans/102-s3-backup.md) and
  [tasks](../../tasks/102-s3-backup.md).
- As built today: `workspaces/src/admin/README.md` and
  `DOCUMENTATION.md`.
