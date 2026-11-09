# `config.toml`

Part of the [workspace-admin design](README.md). The file that lists a
workspace's assets. Users edit it by hand, through the
[`/config` page](CONFIG_PAGE.md) or with the [CLI](CLI.md); all three
go through one validator and one writer in the new package
`admin/assets/` (the existing `admin/config/` folder holds data files
and is left alone).

## Location and contents

`$PERSISTENT_DIR/.config/workspace-admin/config.toml`, mode `0600`, as
today. Deployment-wide settings are not in it; they come from the
environment
([settings](../storage/CONNECT_BACKENDS.md#deployment-wide-settings)).
Connections are not in it either; they live in `tokens/`
([Sign-in](SIGN_IN.md#after-sign-in)).

```toml
SYNC_INTERVAL_SECONDS = 300

# S3 asset: no other keys. Folder assets/models -> s3://<bucket>/<user>/models/
[assets.models]
BACKUP_BACKEND = "s3"

# Git asset using the GitLab connection.
[assets.thesis]
BACKUP_BACKEND = "git"
GIT_REPO_URL = "https://gitlab.example.org/alice/thesis.git"
GIT_REPO_BRANCH = "main"
GIT_AUTH = "gitlab"

# Git asset with a pasted token, exactly as today.
[assets.private]
GIT_REPO_URL = "https://gitlab.com/alice/private.git"
GIT_REPO_BRANCH = "main"
GIT_REPO_TOKEN = "glpat-..."
```

## Rules

| Key | Rule |
| :--- | :--- |
| Section name | `^[a-z0-9]{1,63}$`; unique; no special names |
| `BACKUP_BACKEND` | `git` (default) or `s3` |
| `GIT_AUTH` | `token` (default) or `gitlab` |
| `GIT_REPO_URL`, `GIT_REPO_BRANCH` | Required for git; URL HTTPS; with `GIT_AUTH = "gitlab"` its host must be `GITLAB_URL`'s; existing branch checks |
| `GIT_REPO_TOKEN` | Required when `GIT_AUTH = "token"`, refused otherwise; placeholders refused |
| S3 sections | No keys besides `BACKUP_BACKEND` |

Validation is per section. A section that fails is reported by name
and skipped; the others run. An S3 section while `S3_ENDPOINT` is
unset is not an error in the file: the asset reports
`s3_not_configured` each round.

## Compatibility

- Files with `[assets.private]` and `[assets.common]` written for
  today's version load with the same meaning: no `BACKUP_BACKEND` means
  git, no `GIT_AUTH` means the pasted token.
- **Change:** today's loader reads only `private` and `common` and
  ignores every other `[assets.*]` section. From F1 on, other sections
  are read. A valid one starts being backed up; an invalid one (for
  example `[assets.my-data]`) is logged and skipped. The release notes
  say so.

## Writing safely

`admin/assets/writer.py` serves the page and the CLI:

1. `read_snapshot(path)` returns the parsed assets, the interval and
   the file's SHA-256 together (`ConfigSnapshot`).
2. Apply the change in memory (`add_asset`, `remove_asset`).
3. Validate the whole result with the loader's code.
4. Refuse with `config_changed` if the file's SHA-256 differs from the
   snapshot's: someone edited it in the meantime.
5. Write to a temporary file in the same folder, `fsync`, `0600`,
   rename over the original.
6. Ask the scheduler to reload.

The writer emits TOML itself (flat sections, strings and integers), so
no TOML-writing dependency is added. **Trade-off:** comments in a
hand-edited file are lost on the first page or CLI save; the page and
CLI warn before that first save.

## Live reload

The scheduler reloads when the writer asks, and when the file's
modification time changes (checked each round), so hand edits apply
without restart. Reload waits for the current round, rebuilds the
backends, clones new git assets, and keeps the status of assets that
still exist. A file that cannot be parsed at all is logged and the
previous configuration keeps running.

## Adding and removing

| Action | Effect |
| :--- | :--- |
| Add a git asset | Section written; next reload clones it into `assets/<name>` |
| Add an S3 asset | Section written; next round mirrors `assets/<name>` if it exists and is not empty |
| Remove an asset | Section deleted; backup stops. The folder, the git directory and stored copies stay |
| Rename | Not offered: it would move data across folders, prefixes and repositories |

## Operator pre-seeding

`provision.py` copies `WORKSPACE_CONFIG_TEMPLATE` when no
`config.toml` exists, as today. Operators can ship a template with
assets already declared, for example one S3 asset `files`, so users
only need to sign in.
