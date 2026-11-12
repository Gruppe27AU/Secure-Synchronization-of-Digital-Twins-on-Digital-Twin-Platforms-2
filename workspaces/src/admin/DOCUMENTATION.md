# Admin Service Documentation

The admin service is a FastAPI-based REST API that provides service discovery
and management capabilities for the DTaaS workspace.

## Overview

The service runs on port 8091 (configurable via `ADMIN_SERVER_PORT` environment
variable) and is proxied through nginx. It supports path prefixes for multi-user
deployments, allowing routes to be accessible at `/{path-prefix}/services`.

## Endpoints

### GET /services

Returns a JSON object containing information about all available workspace
services.

**Request**:

```bash
# Without path prefix
curl http://localhost:8080/services

# With path prefix
curl http://localhost:8080/{path-prefix}/services
```

**Response**: Status 200 OK

```json
{
  "desktop": {
    "name": "Desktop",
    "description": "Virtual Desktop Environment",
    "endpoint": "tools/vnc"
  },
  "vscode": {
    "name": "VS Code",
    "description": "VS Code IDE",
    "endpoint": "tools/vscode"
  },
  "notebook": {
    "name": "Jupyter Notebook",
    "description": "Jupyter Notebook",
    "endpoint": ""
  },
  "lab": {
    "name": "Jupyter Lab",
    "description": "Jupyter Lab IDE",
    "endpoint": "lab"
  }
}
```

**Notes**:
- Service endpoints are relative paths that should be appended to the base
  workspace URL
- When using path prefixes, the prefix is automatically prepended to all routes
- Empty string endpoints indicate the service is available at the root path

### GET /health

Health check endpoint for monitoring service availability.

**Request**:

```bash
# Without path prefix
curl http://localhost:8091/health

# With path prefix  
curl http://localhost:8091/{path-prefix}/health
```

**Response**: Status 200 OK

```json
{
  "status": "healthy"
}
```

### GET /storage/status

Reports where the git backup stands and how each repository's last backup
went. Before this endpoint existed, a failing backup only showed up as a
line in the service's log, which the workspace user never sees.

**Request**:

```bash
# Without path prefix
curl http://localhost:8091/storage/status

# With path prefix
curl http://localhost:8091/{path-prefix}/storage/status

# Through nginx, as the user's browser reaches it
curl http://localhost/{path-prefix}/storage/status
```

nginx proxies this exact path to the admin service, next to
`/{path-prefix}/services`. Other admin routes, such as `/health`, are not
reachable through nginx.

**Response**: Status 200 OK

```json
{
  "state": "running",
  "enabled": true,
  "repositories": {
    "private": {
      "state": "syncing",
      "last_attempt": "2026-10-09T12:05:00+00:00",
      "last_success": "2026-10-09T12:00:00+00:00",
      "last_error": {
        "kind": "auth_failed",
        "at": "2026-10-09T12:05:00+00:00"
      }
    },
    "common": {
      "state": "clone_failed",
      "last_attempt": "2026-10-09T12:05:00+00:00",
      "last_success": null,
      "last_error": {
        "kind": "network",
        "at": "2026-10-09T12:05:00+00:00"
      }
    }
  }
}
```

`state` says where the backup as a whole stands:

| State | Meaning |
| --- | --- |
| `disabled` | There is no `config.toml` and no template to create one from, as when the service runs outside the image |
| `needs_setup` | `config.toml` was just created from the template, or still holds its placeholder values. Fill it in and restart |
| `config_error` | `config.toml` cannot be used; the log names the file, section and key at fault |
| `starting` | The config is fine and the repositories are being cloned |
| `running` | The repositories are backed up on every interval |

`enabled` is `true` only while `running`. It is kept for clients written
before `state` existed.

Every repository in `config.toml` is listed as soon as the file has been
read, with a `state` of its own:

| State | Meaning |
| --- | --- |
| `cloning` | Not cloned yet |
| `clone_failed` | The last clone failed, for the reason in `last_error.kind`. It is tried again on every interval |
| `syncing` | Cloned, and backed up on every interval |

- `last_error` is kept after a later success. If `last_success` is newer
  than `last_error.at`, the error has since cleared
- Every timestamp is `null` until the first attempt. The first backup runs
  one interval after startup
- Timestamps are UTC, in ISO 8601

`last_error.kind` is one of a fixed set of values:

| Kind | Meaning |
| --- | --- |
| `auth_failed` | The remote rejected the credentials. Check `GIT_REPO_TOKEN` |
| `network` | The remote could not be reached: unknown host, connection refused or timed out |
| `timeout` | A git command ran out of time and was stopped (see [Time limits and prompts](#time-limits-and-prompts)) |
| `pull_failed` | The configured branch cannot be pulled, for example after its history was rewritten or it was renamed on the forge. The work is on the backup branch (see [The branch cannot be pulled](#the-branch-cannot-be-pulled)) |
| `merge_conflict` | The workspace and the remote changed the same file. Nothing was merged or pushed to the configured branch; the work is on the backup branch (see [Conflicts](#the-remote-changed-the-same-file)) |
| `local_changes` | A local file the backup does not commit is in the way of the remote's changes, for example an edit in a pull-only repository (see [Pull-only repositories](#pull-only-repositories)) |
| `operation_in_progress` | A merge, rebase, cherry-pick or revert is unfinished (see [Conflicts](#a-merge-or-rebase-you-started-is-unfinished)) |
| `unresolved_conflicts` | Git still lists files as conflicted (see [Conflicts](#git-still-lists-files-as-conflicted)) |
| `conflict_markers` | A file holds conflict markers and was left out of the commit; the rest was backed up (see [Conflicts](#a-file-holds-conflict-markers)) |
| `file_too_large` | A file over 50 MiB was left out of the commit; the rest was backed up (see [What is never backed up](#what-is-never-backed-up)) |
| `wrong_branch` | Another branch than `GIT_REPO_BRANCH` is checked out (see [Conflicts](#another-branch-is-checked-out)) |
| `detached_head` | No branch is checked out, for example during `git bisect` (see [Conflicts](#another-branch-is-checked-out)) |
| `index_locked` | Another git command was running in the repository, or an old `index.lock` could not be removed (see [Conflicts](#another-git-command-is-running)) |
| `mass_deletion` | More than half of the tracked files are missing from the working tree (see [Conflicts](#most-files-are-missing)) |
| `git_failed` | Any other failing git command |
| `unexpected` | An error that is not a git failure, for example `git` missing from the image |

The endpoint never returns an error message, a file name, a URL, a branch
or a username. The admin API has no authentication and listens on every
interface, so anyone who can reach the port can read this response. The
full reason is in the service's log, next to the same timestamp.

The endpoint is read-only. There is deliberately no route that starts a
backup: without authentication, anyone could trigger pushes.

### GET /

Root endpoint providing service metadata and available endpoints.

**Request**:

```bash
# Without path prefix
curl http://localhost:8091/

# With path prefix
curl http://localhost:8091/{path-prefix}
```

**Response**: Status 200 OK

```json
{
  "service": "Workspace Admin Service",
  "version": "0.1.1",
  "endpoints": {
    "/services": "Get list of available workspace services",
    "/health": "Health check endpoint",
    "/storage/status": "Outcome of the most recent git backups"
  }
}
```

## Configuration

The git backup reads one TOML file. `admin.git.setup.locate.discover_config()`
chooses it once, on startup, checking in order:

1. `$WORKSPACE_ADMIN_CONFIG`, when it is set and not empty. A relative path
   is resolved against the directory the service is started in, at that
   moment.
2. `~/.config/workspace-admin/config.toml`.

A file named by `$WORKSPACE_ADMIN_CONFIG` is used even when it is missing;
the default directory is then not looked at. The chosen path is passed to
every step of the backup, so changing the variable while the service runs
has no effect, and its absolute form is logged on every start. Where the
file is does not move the repositories: they stay below `$PERSISTENT_DIR`.
In the workspace image `custom_startup.sh` sets `$WORKSPACE_ADMIN_CONFIG`
to `$PERSISTENT_DIR/.config/workspace-admin/config.toml`, so the file is
`/workspace/.config/workspace-admin/config.toml`; with the bundled compose
files, `files/<username>/.config/workspace-admin/config.toml` on the host.

It is parsed with `tomllib`, so quoting and section syntax follow TOML
rules. The template is `workspaces/src/templates/config.toml.example`.

- **No file, template available** (the workspace image, where the
  Dockerfile sets `WORKSPACE_CONFIG_TEMPLATE`): the template is copied into
  place with mode `0600`, an existing file is never overwritten, and the
  directory gets a `.gitignore` holding `*`, so a repository further up
  never commits the token. Its placeholder values are refused, so nothing
  is cloned until the user fills it in and restarts. The status reports
  `needs_setup`.
- **No file, no template** (outside the image): the backup is disabled, the
  log shows `<path> not found; git backup is disabled`, and the status
  reports `disabled`.

### Top-level keys

| Key | Meaning |
| --- | ------- |
| `SYNC_INTERVAL_SECONDS` | Optional. Seconds between two backups, a whole number from 1 to 86400 (one day). Defaults to 300 |

Top-level keys must come before the first `[assets.*]` section. In TOML,
a key written below a section header belongs to that section, so
`SYNC_INTERVAL_SECONDS` appended at the end of the file would land in the
last `[assets]` section, where it has no effect. The backup then logs a
warning naming the section and uses 300 seconds, rather than ignoring the
key without a word.

A value that cannot be used never stops the backup. One that is not a
whole number (`300.0`, `"300"`, `true`), or is outside 1 to 86400, is
reported as a warning and the default of 300 seconds is used instead:

```text
WARNING admin.git.setup.load /workspace/.config/workspace-admin/config.toml: 'SYNC_INTERVAL_SECONDS' must be between 1 and 86400 seconds, got 0; using the default of 300 seconds
```

The upper limit keeps the backup frequent enough to be useful, and keeps
the wait well below what Python's `threading` can handle
(`threading.TIMEOUT_MAX` is about 49 days on Windows; past it the wait
raises an error that would end the backup thread).

The `--sync-interval` command-line option, when given, overrides the file.
It accepts the same range. The workspace image does not pass it, so there
the file decides.

### Repository sections

Every repository is a `[assets.<name>]` table. Two names are recognised,
`private` and `common`, and at least one of them has to be there.

| Key | Meaning |
| --- | ------- |
| `GIT_REPO_URL` | Required. HTTPS URL of the remote. Must start with `https://` and must not contain a username or token (`user:token@`) |
| `GIT_REPO_BRANCH` | Required. The one branch that is cloned and kept in step. Must be a valid git branch name, as checked by `git check-ref-format --branch` |
| `GIT_REPO_TOKEN` | Access token, sent with HTTP Basic authentication. Required for `"sync"`; a `"pull"` repository may leave it out or empty, for a public remote |
| `MODE` | Optional. `"sync"` commits, pulls and pushes; `"pull"` only takes the remote's changes. Defaults to `"sync"` for `private` and `"pull"` for `common` (see [Pull-only repositories](#pull-only-repositories)) |

HTTP Basic needs a username as well, but the forge identifies the user by
the token alone, so `auth.py` always sends the fixed username `oauth2`, as
GitLab documents.

`private` and `common` must use different remotes, or at least different
branches of one. Two spellings of one remote count as the same: the scheme
and host are compared without regard to case, and a trailing `/` or `.git`
is ignored, so `https://gitlab.com/me/priv.git` and
`https://GitLab.com/me/priv` are refused together on the same branch.

`GIT_REPO_TOKEN` is kept out of `RepoConfig.__repr__`, so printing or
logging a configuration cannot leak it.

### Keys that have no effect

Any other key is reported with a warning and otherwise ignored, so a key
from an older version of the file, or a misspelt one, does not silently do
nothing:

```text
WARNING admin.git.setup.validate <path>: 'SYNC_INTERVAL' in the top level is not a known setting and has no effect; expected only SYNC_INTERVAL_SECONDS, assets
```

This covers the keys earlier versions read: `HOME_DIR`, `WORKSPACE_DIR`,
`WORKSPACE_APP_DIR`, and a per-section `GIT_DIR`, `GIT_WORK_TREE` or
`GIT_REPO_USERNAME`. The paths now follow from `$PERSISTENT_DIR` and the
section name alone, and the username is always `oauth2`. An unknown
section such as `[assets.shared]` is reported the same way.

### Example

```toml
SYNC_INTERVAL_SECONDS = 300

[assets.private]
GIT_REPO_URL = "https://gitlab.com/me/thesis.git"
GIT_REPO_BRANCH = "main"
GIT_REPO_TOKEN = "glpat-..."

[assets.common]
GIT_REPO_URL = "https://gitlab.com/lab/shared-models.git"
GIT_REPO_BRANCH = "main"
GIT_REPO_TOKEN = "glpat-..."
MODE = "pull"
```

### Who can read the file

The file holds tokens. One created from the template is readable only by
its owner (`0600`). For a file the user or operator supplies, the service
checks the same thing: if anyone but the owner can read or change it, a
warning gives the command to fix it. It is only a warning, because the
permissions on a mounted volume are not always the user's to change.

```text
WARNING admin.git.setup.validate /workspace/.config/workspace-admin/config.toml holds tokens but can be read or changed by other users (mode 644); restrict it with: chmod 600 /workspace/.config/workspace-admin/config.toml
```

### When the file is wrong

Anything that makes the configuration unusable raises `ConfigError`, and the
message names the file, the section and the key at fault so the file can be
fixed without reading any code. The cases are: the file cannot be read, it
is not valid TOML, a required key is missing, a key holds something other
than a quoted string, a key is present but empty, `MODE` is neither `pull`
nor `sync`, there is no `[assets]` section, `[assets]` holds neither
`private` nor `common`, two sections share a remote branch, or a work tree
contains the config file. A placeholder value from the template raises
`PlaceholderError`, a kind of `ConfigError` that the status reports as
`needs_setup` rather than `config_error`.

Two values are checked more strictly, because they end up on git's command
line:

- `GIT_REPO_BRANCH` must be a name `git check-ref-format --branch` accepts
  and returns unchanged. This refuses a value starting with `-`, which git
  would otherwise read as an option: `--upload-pack=<command>` runs a
  command. It also refuses shorthands such as `@{-1}`.
- `GIT_REPO_URL` must start with `https://`, have a host, contain no spaces
  or control characters, and hold no `user:token@` part. Other transports
  are where options like `--upload-pack` take effect, and a token in the
  URL would be written into the clone's `.git/config` and into the log.
  The URL is left out of the error message, since it may hold a token.

As a second layer, every git command that contacts the remote runs with
`-c protocol.allow=never -c protocol.https.allow=always`, and names the
branch only as a full refspec after `--`. For example:
`git fetch -- origin +refs/heads/<branch>:refs/remotes/origin/<branch>`.

`ConfigError` is not fatal to the service. `bootstrap.py` logs it and
carries on, so a broken git configuration costs the workspace its backup but
still leaves the HTTP API running.

## Cloning the Git Assets

When the admin service starts, it clones the repositories described in
`config.toml` into the workspace, so the user's files are there before they
open Jupyter or VS Code. This section describes that process for the two
repositories the service knows about, `private` and `common`.

### Private and common

The two repositories are cloned in exactly the same way. They differ in
what they are for, and so in how they are kept in step afterwards:

- **`private`** (`[assets.private]`) holds the user's own files and points
  at a repository only that user works in. It is synchronized both ways.
- **`common`** (`[assets.common]`) holds files shared between users. Every
  workspace that configures the same `common` remote gets a clone of its
  own. By default it only receives (see
  [Pull-only repositories](#pull-only-repositories)).

Either one can be left out of `config.toml`, but at least one of them has to
be there. When both are configured, `private` is cloned first.

### Pull-only repositories

A repository in `MODE = "pull"`, as `common` is unless told otherwise, only
ever takes the remote's changes. Every interval it fetches and
fast-forwards; it never commits or pushes, so a read-only token is enough.

This matters for a repository several users share. If every workspace
committed and pushed `common`, and kept its own version of any file both
sides changed, whatever one user pushed would end up in every other
workspace, and a colleague's change would be dropped whenever it conflicted
with one's own.

Local edits in a pull-only repository stay as they are, but are never
backed up. When the remote changes a file that was also edited locally,
nothing is changed and the status reports `local_changes`: discard the
local edit (`git checkout -- <file>`) or move it to `private`, and the next
interval brings the remote's version. Only set `MODE = "sync"` for
`common` when a single user writes to it.

### When cloning happens

`admin.main.cli()` calls `start_git_backup()` once on startup. That starts a
background thread, `git-bootstrap`, which runs `clone_configured_repos()` and
then `start_git_sync()`, while the HTTP server starts straight away. A slow or
unreachable remote therefore delays the backup, never `/health` or the rest
of the API. A repository whose clone failed is cloned again on every
interval, so a fixed token or a remote that is back needs no restart. After
adding a repository to `config.toml` or changing one, restart the service
(in the workspace container: restart the container).

### Where each repository ends up

Every repository is split in two, both below `$PERSISTENT_DIR`:

- the **git directory** holds git's own data (history, objects, refs, the
  remote URL) at `.local/share/workspace-admin/<name>`
- the **working tree** holds the checked-out files the user edits, at
  `assets/<name>`

In the workspace image the result is:

```text
/workspace/                                          # $PERSISTENT_DIR
├── .config/workspace-admin/
│   ├── config.toml
│   └── .gitignore                                   # "*"
├── .local/share/workspace-admin/
│   ├── private/                                     # git directory of private
│   └── common/                                      # git directory of common
└── assets/
    ├── private/                                     # working tree of private
    │   └── .git     # file: "gitdir: /workspace/.local/share/workspace-admin/private"
    └── common/                                      # working tree of common
        └── .git     # file: "gitdir: /workspace/.local/share/workspace-admin/common"
```

The working tree holds no `.git` directory, only a one-line `.git` file
pointing at the git directory, so the user's own git commands in the folder
find the repository. The backup itself does not rely on that file: every
command it runs names both directories (`--git-dir` and `--work-tree`).
See [The `.git` file is missing](#the-git-file-is-missing).

### What a clone does, step by step

`clone_asset()` in `clone.py` does the following for each repository:

1. **Check whether it is already cloned.** If the git directory already
   contains a `HEAD` file, the repository counts as cloned, the clone is
   skipped and `Repository 'private' already cloned at <git dir>; skipping`
   is logged. This is what happens on every start after the first. Only the
   git directory is checked, not the working tree.
2. **Create the directories.** The parent of the git directory and the
   working tree itself are created if they do not exist.
3. **Run `git clone`:**

   ```bash
   git -c protocol.allow=never -c protocol.https.allow=always \
       clone --branch <GIT_REPO_BRANCH> --single-branch \
       --separate-git-dir=<git dir> -- <GIT_REPO_URL> <working tree>
   ```

   - `--branch` and `--single-branch` fetch only the configured branch, so
     the clone never contains the remote's other branches.
   - `--separate-git-dir` is what puts the git directory and the working
     tree in the two places described above.
   - The command also gets `-c credential.helper=` and
     `-c http.lowSpeedLimit=1 -c http.lowSpeedTime=60` (left out above for
     readability), and runs without prompts and within a time limit. See
     [Time limits and prompts](#time-limits-and-prompts).
   - The protocol options make git refuse every transport except https,
     and `--` stops the URL and path from being read as options.
   - The token is not on the command line at all; see
     [How the token is sent](#how-the-token-is-sent).
4. **Start on an empty remote.** A remote with no commits has no branch to
   clone, so `git clone` fails with `Remote branch <branch> not found in
   upstream origin`. If `git ls-remote --heads` then confirms the remote has
   no branches at all, the repository is started locally instead: `git init`
   with the same separate git directory and the configured branch, and an
   `origin` that fetches only that branch. The first sync pushes the user's
   files and creates the branch on the remote. A remote that has other
   branches but not the configured one is still an error.
5. **Report the result.** A successful clone logs
   `Cloned repository 'private' (<url>, branch <branch>) into <working tree>`.

### How the token is sent

`GIT_REPO_TOKEN` travels as an HTTP Basic `Authorization` header that git
adds to every request. The header is passed to git in its environment, as
`GIT_CONFIG_COUNT=1`, `GIT_CONFIG_KEY_0=http.<GIT_REPO_URL>.extraHeader`
and `GIT_CONFIG_VALUE_0=Authorization: Basic <base64 of oauth2:token>`:

- **Not on the command line.** A process's arguments can be read by every
  user of the machine (`/proc/<pid>/cmdline`); its environment only by its
  own user.
- **Only for the repository.** The header is scoped to `GIT_REPO_URL`
  itself, so another repository on the same forge never receives the
  token. git matches the scope against the remote URL once per command,
  so the header still follows a redirect, for example from `.../repo` to
  `.../repo.git/`.
- **Never stored.** It is not written into the git directory's config and
  not added to the remote URL.

### Requirements for a clone to succeed

- `GIT_REPO_URL` is an HTTPS URL. Any other kind of URL is refused when the
  configuration is loaded.
- The token is accepted by the remote and allows reading the repository
  (on GitLab: the `read_repository` scope; a `sync` repository also needs
  `write_repository` to push).
- Neither the git directory nor the working tree already exists with files
  in it. Git refuses to clone into a directory that is not empty, and fails
  with `already exists and is not an empty directory`. A directory that
  exists but is empty is fine.

### When a clone fails

A failed clone is logged, recorded in the status as `clone_failed` with the
reason as `last_error.kind`, and the service carries on:

```text
ERROR admin.git.service.bootstrap Failed to clone for repository 'common': <git's message>
```

- Each repository is cloned on its own, so a failure in `common` does not
  stop `private` from being cloned, and the other way round.
- If the failure looks like rejected credentials, the kind is `auth_failed`
  and the message ends with `(the configured GIT_REPO_TOKEN was rejected -
  it may be invalid or expired; check config.toml)`. That includes git's
  `could not read Username ...: terminal prompts disabled`, which is what an
  expired token produces when prompts are off. GitLab gives the same answer
  for a wrong token as for an expired one, so the message does not claim to
  know which it is. The token itself never appears in the log.
- An unreachable remote is `network`, and a clone that runs out of time is
  `timeout`.
- If `config.toml` cannot be used at all, nothing is cloned and the log shows
  `Cannot clone git assets: ...` with the reason (see
  [When the file is wrong](#when-the-file-is-wrong)).
- A failed clone is tried again on every interval. Git cleans up after a
  failed clone, so the git directory and working tree are left empty and do
  not block the next attempt. This includes a clone stopped for taking too
  long: it is sent `SIGTERM` first so git can clean up, and only killed if
  it has not stopped 5 seconds later.

### Time limits and prompts

Every git command goes through `run_git()` in `runner.py`, so none of them
can wait forever:

- **No prompts.** `GIT_TERMINAL_PROMPT=0` and an empty `GIT_ASKPASS` stop git
  from asking for a username or password, on a terminal or through an
  askpass helper such as the ones VS Code and desktop sessions set up. The
  empty `GIT_ASKPASS` also overrides `core.askPass` and `SSH_ASKPASS`.
  Credential helpers, such as Git Credential Manager, ignore both settings,
  so every command that talks to the remote also gets `-c credential.helper=`,
  which empties the list of helpers. Credentials only ever come from
  `GIT_REPO_TOKEN`. Standard input is empty as well.
- **English messages.** `LC_ALL=C` keeps git's messages in English
  whatever the locale, because a rejected token, an unreachable remote or
  an empty remote is told apart by their text.
- **Time limits.** Commands that talk to the remote (clone, fetch, push) get
  900 seconds (`REMOTE_TIMEOUT_SECONDS`), and everything else 300 seconds
  (`LOCAL_TIMEOUT_SECONDS`). A command that runs out of time is sent
  `SIGTERM`, then `SIGKILL` 5 seconds later if it is still running. It runs
  in its own process group, so helpers it started, such as
  `git-remote-https`, are stopped with it. The status reports `timeout`,
  and the next cycle tries again.
- **Stalled transfers.** `-c http.lowSpeedLimit=1 -c http.lowSpeedTime=60`
  make git give up by itself once a connection has moved less than 1 byte
  per second for 60 seconds. This does not cover a connection that stalls
  before it is set up, such as a TLS handshake that never completes. Only
  the time limit stops that.

In none of these cases does the admin service stop: the HTTP API starts as
normal.

### After cloning

`start_git_sync()` runs next and schedules the periodic backup for every
configured repository. One that is not cloned yet is cloned instead of
backed up, on every interval until that succeeds. See
[Git Backup Flow](#git-backup-flow) for what the backup does.

### The backup branch

Every cycle of a `sync` repository pushes the workspace's branch to a branch
of its own on the remote, `backup/<workspace-id>`, right after the commit and
before anything is pulled. Whatever happens to the pull afterwards, the
newest work has left the container:

```text
INFO admin.git.repo.push Backed up repository 'private' to branch backup/3f2a9c01d4e5
```

Only this workspace writes that branch, so the push is forced and is never
refused. The id is made up once and kept in the git directory
(`workspace-admin-id`), so it stays the same across restarts and differs
between workspaces backing up to the same remote. The branch is not deleted
automatically. A failed backup push is logged and the cycle goes on.

### The branch cannot be pulled

Some pull failures do not go away by themselves:

- the remote's history was rewritten, for example after the project was
  deleted and recreated (`refusing to merge unrelated histories`)
- the configured branch was renamed or deleted on the forge
  (`couldn't find remote ref`)

Then nothing is pushed to the configured branch, and the status reports
`pull_failed`. The work is on the backup branch, which the log names:

```text
ERROR admin.git.service.scheduler Failed to merge the remote's changes for repository 'private': fatal: refusing to merge unrelated histories; the local work is on branch backup/3f2a9c01d4e5
```

To recover, fix the branch on the forge (or point `GIT_REPO_BRANCH` at the
new name) and merge `backup/<id>` into it.

A rejected token, an unreachable remote, a time-out or a branch switch is
reported as it is: the next cycle simply tries again.

A branch the workspace has never seen on the remote, as on an empty remote
the repository was started on, is not a failure: there is nothing to pull,
and the next push creates it.

## Conflicts: What Is Automatic and What Is Manual

Most of the time the backup needs nothing from the user. A few situations
cannot be decided safely by a program, and there the backup stops for that
repository, logs why, and tries again every interval until a person has
sorted it out. It never guesses: nothing half finished is committed or
pushed. While it waits, [`/storage/status`](#get-storagestatus) shows the
repository's `last_error.kind`, so the user can tell that something needs
doing without reading the log.

| Situation | Handled by | What the backup does | To get sync going again |
| --- | --- | --- | --- |
| The remote changed the same file, deleted it, or put a directory where it is | You | Aborts the merge; keeps backing up to the backup branch, pushes nothing to the configured branch | Merge `origin/<branch>` by hand |
| A merge, rebase, cherry-pick or revert you have started is unfinished | You | Waits: no commit, no pull, no push | Finish or abort the changes |
| Git still lists files as conflicted | You | Waits: no commit, no pull, no push | Fix the files, then `git add` |
| A file holds conflict markers | You | Leaves that file out, commits and pushes the rest | Remove the markers |
| A file is over 50 MiB | You | Leaves that file out, commits and pushes the rest | Move or shrink it, or ignore it |
| Most tracked files are missing | You | Waits: no commit | Restore them, or commit the deletion by hand |

Every command below is run in the repository's working tree, which with the
workspace image is `/workspace/assets/<name>`. The service can
keep running while you work: as long as your merge is unfinished, the
backup waits for it instead of interfering.

If `git commit` asks who you are, add `-c user.name="Your Name" -c
user.email="you@example.com"` right after `git`. The clone does not store an
identity, so that nothing is written into the repository on your behalf.

### The remote changed the same file

A collaborator, or another workspace, pushed a change to a file the user
also changed. The same applies when one side deleted a file the other
changed, or put a directory where the other has a file. Git cannot combine
the two, so the backup aborts the merge and leaves everything as it was:

```text
ERROR admin.git.service.scheduler Repository 'private': the remote changed notes.md as well; merge them by hand; the local work is on branch backup/3f2a9c01d4e5
```

While the conflict lasts:

- the configured branch on the remote keeps the collaborator's version
- the working tree keeps the user's version, and new work is still
  committed every interval
- every interval pushes that work to the [backup branch](#the-backup-branch),
  so it is never only in the container
- nothing from the remote is merged in, not even files without a conflict,
  and nothing is pushed to the configured branch
- the status reports `merge_conflict`

#### Why the backup does not pick a side

Keeping either version automatically would throw the other away without
anyone deciding to. Keeping the workspace's version replaces the
collaborator's work on the remote, and they are never told: the only trace
is a line in the log of a container they cannot see. Keeping the remote's
version does the same to the user. Which edit matters, or how the two
should be combined, is only known to the people who made them.

The backup can leave that decision to them because the user's work never
depends on the merge to be safe. It reaches the backup branch before the
pull is even tried. A conflict therefore costs time, not data: the remote
holds one version, the backup branch the other, and the user merges them
once.

#### To get sync going again

Merge the remote's branch by hand in the working tree. The backup fetches
it every interval as `origin/<branch>`, so no credentials are needed:

```bash
cd /workspace/assets/private
git merge origin/main         # stops on the conflict
```

Open each file git names and keep the content you want. Between `<<<<<<<`
and `=======` is your version, between `=======` and `>>>>>>>` the
remote's; delete the three marker lines too. To keep one side of a file
whole instead:

```bash
git checkout --ours -- notes.md     # your version
git checkout --theirs -- notes.md   # the remote's version
```

Then mark the files resolved and conclude the merge:

```bash
git add notes.md
git commit --no-edit
```

The backup waits while the merge is unfinished (see
[A merge or rebase you started is unfinished](#a-merge-or-rebase-you-started-is-unfinished)),
and the next interval after the commit pushes the result.

### A merge or rebase you started is unfinished

If you started a merge, rebase, cherry-pick or revert by hand and it stopped
on a conflict, the backup leaves it alone:

```text
ERROR admin.git.service.scheduler Not synchronizing repository 'common': a git operation is in progress (MERGE_HEAD); finish or abort it first
```

Committing at this point would conclude your merge for you, conflict and
all, and push it.

**To get sync going again**, either finish what you started, after fixing
the conflicted files and running `git add` on them:

```bash
git commit                  # a merge, cherry-pick or revert
git rebase --continue       # a rebase
```

or give up on it, which puts everything back as it was before you started:

```bash
git merge --abort           # or: git rebase --abort, git cherry-pick --abort, git revert --abort
```

The name in brackets in the log (`MERGE_HEAD`, `rebase-merge`, ...) tells
you which one you are in. The next interval synchronizes as normal.

### Git still lists files as conflicted

The same protection applies when git holds unresolved files without an
operation in progress, for example after a `git stash pop` that conflicted:

```text
ERROR admin.git.service.scheduler Not synchronizing repository 'common': unresolved conflicts in notes.md
```

**To get sync going again**, open each file the log names, keep the content
you want, and mark it resolved:

```bash
git add notes.md
```

The next interval commits and pushes it.

### A file holds conflict markers

A file can be marked resolved while it still contains the lines git writes
around a conflict (`<<<<<<<`, `=======`, `>>>>>>>`). The backup checks what
it is about to commit and leaves such a file out:

```text
WARNING admin.git.repo.commit Not committing some files in repository 'private': conflict markers left in notes.md
```

Every other change is still committed, pulled and pushed, so one file
cannot hold up the rest of your work. The file is unstaged again, so it is
as you left it, and the status reports `conflict_markers` until it is
fixed.

If the remote changed the same file, the merge would overwrite it, so
nothing is merged until it is fixed, and the log says so:

```text
WARNING admin.git.repo.sync Repository 'private': notes.md is held back (conflict markers left in notes.md), remove the conflict markers in order to merge.
```

**To get sync going again**, open each file the log names. Between
`<<<<<<<` and `=======` is one version, between `=======` and `>>>>>>>` the
other. Keep the lines you want and delete the rest, the three marker lines
included. Save the file, or delete it if you do not need it. No git command
is needed: the next interval commits as normal.

Only lines starting with `<<<<<<<` or `>>>>>>>` count. A line holding just
`=======` is also how Markdown underlines a heading, so on its own it is
committed as normal. A file that holds a line starting with `<<<<<<<` on
purpose, such as notes about git, is held back all the same, unless git is
told that conflict markers in it are longer. Seven characters is the
default; with a `.gitattributes` in the working tree such as

```text
docs/** conflict-marker-size=32
```

a seven-character marker line in `docs/` is no longer a marker, and the
file is backed up as normal.

### Another branch is checked out

The backup commits to, merges into and pushes `GIT_REPO_BRANCH` only. When
you check out another branch, or no branch at all (a tag, a single commit,
or a `git bisect` session), the backup waits:

```text
ERROR admin.git.service.scheduler Not synchronizing repository 'common': 'experiment' is checked out, not 'main'; check out 'main' to resume the backup
ERROR admin.git.service.scheduler Not synchronizing repository 'common': HEAD is detached; check out branch 'main' to resume the backup
```

Without this check the backup commits would land on your branch, while the
push sends `GIT_REPO_BRANCH`, so they would never reach the remote.

**To get sync going again**, switch back, finishing a bisect first if one is
running:

```bash
git bisect reset      # only during a bisect
git switch main       # the branch in GIT_REPO_BRANCH
```

The check runs again just before the merge, because fetching can take long
enough for you to switch branches in between.

### Another git command is running

Every git command that changes the staging area first creates
`index.lock` in the git directory and deletes it when it is done. While the
file exists, the backup leaves the repository alone for that interval:

```text
ERROR admin.git.service.scheduler Not synchronizing repository 'common': another git process holds /workspace/common/index.lock; trying again next cycle
```

Usually there is nothing to do: your command finishes and the next
interval goes ahead. Note that `git commit` without `-m` holds the lock for
as long as its editor is open.

A git process that is killed, for example when the container stops,
leaves the file behind, and git refuses to run until it is gone. The backup
therefore removes an `index.lock` that is more than an hour old and goes
ahead, logging a warning. An hour, rather than a few minutes, so that a
`git commit` editor left open over a break is not mistaken for a crash. A
running git command never holds the lock that long in practice.

### Most files are missing

When every tracked file is missing from the working tree, or more than half
of them and at least ten, the backup does not commit, so the files are not
deleted on the remote either:

```text
ERROR admin.git.service.scheduler Not committing in repository 'private': 40 of 52 tracked files are missing from /workspace/assets/private. If that is intended, commit the deletion there by hand to resume the backup
```

A folder that has been emptied is far more often an accident, such as a
move, a clean-up that went too far or a restore that has not finished, than
a decision. If you did mean to delete the files, commit the deletion
yourself and the next interval pushes it:

```bash
cd /workspace/assets/private
git add -A && git commit -m "Remove old results"
```

That is also how the last file of a repository is deleted.

### The working tree is deleted

When the folder `/workspace/assets/<name>` itself is gone, it is restored
from the git directory on startup and before each cycle, as of the last
backup, with a warning in the log:

```text
WARNING admin.git.repo.clone Repository 'private' had lost /workspace/assets/private; restored from its git directory
```

Only a missing folder is restored. Files deleted inside a folder that still
exists are your decision, within the limits of
[Most files are missing](#most-files-are-missing).

### The `.git` file is missing

Every backup command names its git directory and working tree explicitly,
so a lost `.git` file in the working tree can never make the backup commit
into, or push from, a repository further up, such as one you keep at
`/workspace`. Before each cycle the backup also writes the file back, with
a warning in the log, so your own git commands in the folder find the
backup repository again. An existing `.git` file or directory is never
overwritten.

### What is never backed up

The working tree is a visible folder, `/workspace/assets/<name>`, which is
where virtual environments, notebook checkpoints and simulation output
tend to end up. Backing those up would fill the repository, and a large
enough tree would make staging hit its time limit on every cycle. So:

- **Folders that are rebuilt rather than kept are never staged.** The backup
  keeps these patterns in the git directory's `info/exclude`, which works
  like a `.gitignore` but is neither committed nor visible in the folder:
  `.venv/`, `venv/`, `__pycache__/`, `*.py[cod]`, `.ipynb_checkpoints/`,
  `.pytest_cache/`, `.mypy_cache/`, `node_modules/`,
  `.DS_Store`, `Thumbs.db` and `*.swp`. They sit between two marker lines
  the backup rewrites; your own lines in that file are kept. A file that
  was committed before stays tracked.
- **A file over 50 MiB is left out.** Forges refuse files around 100 MB,
  and a large binary bloats every clone of the repository for good. The
  file stays in the folder, everything else is committed and pushed, and
  the status reports `file_too_large`:

  ```text
  WARNING admin.git.repo.commit Not committing some files in repository 'private': larger than 50 MiB: results/run1.h5
  ```

  Move it out of the folder, make it smaller, or add it to a `.gitignore`
  in the folder, and the warning stops. As with conflict markers, a held
  back file the remote also changed stops the merge until it is dealt with.

### What the backup does not check

The checks cover the commits the backup makes itself. A commit you make by
hand is pushed as it is, conflict markers included, because it is your
decision what goes into it.

## Architecture

### Service Discovery Flow

1. User accesses `http://{domain}/{path-prefix}/services` (or `/services` without prefix)
2. nginx receives the request and routes it to the admin service on port 8091
3. Admin service reads the `config/services_template.json` file
4. JSON response is returned to the client
5. Path prefix is configured via CLI argument when starting the service

### Git Backup Flow

1. On startup, `admin.main.cli()` calls `start_git_backup()`, which, on a
   background thread, clones every configured git asset
   (`clone_configured_repos()`, one clone per `[assets.private]` and/or
   `[assets.common]` entry in `config.toml`) and then starts the backup
   scheduler (`start_git_sync()`). The HTTP server does not wait for either
2. The scheduler runs on a background daemon thread, so it never blocks the
   HTTP server
3. Every `SYNC_INTERVAL_SECONDS` (5 minutes unless the config file says
   otherwise) a repository that is not cloned yet is cloned, a pull-only
   repository is fast-forwarded (see
   [Pull-only repositories](#pull-only-repositories)), and every other
   working tree goes through the same four steps, in this order:
   1. **Commit**: the repository is first checked for a running git
      command (`index.lock`), an unfinished merge, rebase, cherry-pick or
      revert, another branch or a detached `HEAD`, and unresolved files,
      and the whole cycle stops if it finds one. Then `git status --porcelain`
      decides whether there is anything to save. A clean tree is skipped. A
      tree that has lost most of its files stops the cycle. Otherwise
      everything is staged except files over 50 MiB and files holding
      conflict markers, which are held back, and committed with a
      timestamped message (see
      [Conflicts](#conflicts-what-is-automatic-and-what-is-manual))
   2. **Back up**: the branch is pushed to `backup/<workspace-id>`, so the
      work has left the container whatever the pull does (see
      [The backup branch](#the-backup-branch))
   3. **Pull**: the remote is fetched and merged in. Before merging, the
      branch is checked again. A conflict aborts the merge and ends the
      cycle (see [Conflicts](#the-remote-changed-the-same-file)), and so
      does a pull that cannot succeed (see
      [The branch cannot be pulled](#the-branch-cannot-be-pulled))
   4. **Push**: the branch is pushed only when it actually holds commits
      the remote does not. Both the count and the push name the branch, not
      `HEAD`
4. Committing happens *before* merging on purpose: git refuses to merge over
   modified files, so an uncommitted working tree would make every pull fail
   as soon as the remote had something to deliver
5. A repository that fails is logged and skipped, so one broken remote does
   not stop the others from being backed up. Unexpected errors are caught
   too: an exception escaping the background thread would end the backup
   silently while the service carried on serving requests
6. The outcome of every repository is recorded in a `SyncStatus` and
   served by [`/storage/status`](#get-storagestatus): the time, and on failure
   only the error's kind. The full error goes to the log
7. When the service stops on SIGTERM or Ctrl+C, the FastAPI shutdown step
   calls `stop_git_backup()`. No new git command is started after that, and
   the one already running is waited for, for up to 8 seconds. A commit made
   but not yet pushed is pushed by the first interval after the next start

Saving a file in Jupyter or VS Code is enough for it to reach the remote
within one interval. The user only runs git to settle a conflict.

### About the Implementation Reports

The reports below record why each change was made, as it was at the time.
They are kept as history and are not updated afterwards, so some describe
an older layout: a `config.env`, `HOME_DIR`, `WORKSPACE_DIR` and
`WORKSPACE_APP_DIR`, the token passed with `-c http.extraHeader`, or
`sync.py` holding every check. Where they disagree with
[Configuration](#configuration), [Cloning the Git Assets](#cloning-the-git-assets)
or [Components](#components), those sections are right.

### Implementation Report: Cloning the Private Repository

Previously only `[assets.common]` was cloned on startup; `[assets.private]`
was parsed by `config.py` and understood by `clone.py`/`sync.py`, but
nothing ever called `clone_asset()` for it. `bootstrap.py` is now
repository-agnostic: `clone_common_repo()` was replaced with
`clone_configured_repos()`, which clones every repository `load_config()`
returns instead of looking up `common` by name.

No changes were needed in `config.py`, `clone.py` or `sync.py` - the
branch checkout (`GIT_REPO_BRANCH`), the git directory
(`$WORKSPACE_DIR/private`) and working tree
(`$WORKSPACE_APP_DIR/assets/private`) placement, the skip-if-already-cloned
check, and error logging on failure were already generic per `RepoConfig`.
Authenticating the clone (using `GIT_REPO_USERNAME`/`GIT_REPO_TOKEN` from
config, handling invalid/expired tokens gracefully) was tracked as a
separate issue and out of scope here (see the report below). A repository
cannot be cloned anonymously: `config.py` rejects a section whose
`GIT_REPO_USERNAME` or `GIT_REPO_TOKEN` is missing or empty, so every clone
is authenticated.

### Implementation Report: Authenticating to Git Repositories

`clone.py` and `sync.py` already authenticated `git clone` and `git push`
with `GIT_REPO_USERNAME`/`GIT_REPO_TOKEN` from `config.env` before this
change - each built its own one-off `http.extraHeader` carrying an HTTP
Basic `Authorization` header, so credentials never touch a repository's
own `.git/config` or its remote URL, and `RepoConfig.token` was already
excluded from `__repr__` so it cannot leak via accidental logging. That
covered "uses the configured credentials" and "authenticates both
`private` and `common`" (the same code path handles any `RepoConfig`,
and both are now actually cloned on startup, per the private-clone report
above). Two gaps remained:

1. The header-building logic was duplicated between `clone.py` and
   `sync.py`, with a comment in `sync.py` calling out this issue by
   number as the place to fix it.
2. A rejected token surfaced as a raw, undifferentiated `git` failure
   message - correct, but not a "clear error message" pointing at the
   actual cause.

Both are addressed by a new `admin/git/auth.py` module:

- `auth_header_options()` is now the single place that builds the
  Basic-auth header; `clone.py` and `sync.py` both call it instead of
  keeping their own copies.
- `is_auth_failure()` inspects a failed command's stderr for GitLab/git's
  own rejection text (`Authentication failed`, `HTTP Basic: Access
  denied`, `returned error: 401`/`403`) using specific phrases rather
  than bare status-code numbers, so an unrelated error containing "401"
  is not misclassified.
- When a clone or push fails, has credentials configured, and
  `is_auth_failure()` matches, `CloneError`/`SyncError` append a
  token-free hint that `GIT_REPO_USERNAME`/`GIT_REPO_TOKEN` may be
  invalid or expired. GitLab returns the same rejection for a wrong
  token as for an expired one, so the two cannot be, and are not claimed
  to be, distinguished from each other - the tests exercise both
  scenarios against that identical failure text.
- Without configured credentials, or for a failure that doesn't match
  (network error, repository not found, ...), the message is unchanged
  from before: the plain `git` failure text, still without ever
  including the token.

### Implementation Report: Parsing and Validating `config.env`

This was done in two steps.

The first step settled the contract. Before it there was no configuration
file at all: the repository URL, the branch and the two paths had nowhere to
live. `config.env.example` was written to show the shape, and `config.py`
learned to read it with `tomllib` and hand back a `RepoConfig` per
`[assets.<name>]` table. TOML was chosen over a plain shell-style `.env`
because the file needs sections, one repository per table, and `tomllib`
is in the standard library, so it costs no dependency. The file kept the
`.env` name so it stays recognisable next to the rest of the workspace
configuration.

Two decisions from this step shape everything downstream. `GIT_DIR` and
`GIT_WORK_TREE` are stored as fragments and resolved by `load_config()`
against `WORKSPACE_DIR` and `WORKSPACE_APP_DIR`, so callers receive absolute
paths and never join paths themselves. And `RepoConfig.token` is declared
with `field(repr=False)`, so the token cannot reach a log line through an
accidental `print(repo)`.

The second step added the validation. The first version trusted the file:
a missing key raised `KeyError` somewhere far from the cause, and a key
holding a number instead of a string failed even later, inside `subprocess`.
Both are now caught while reading, by a single `_require()` helper that
every field goes through. It checks three things: the key is present, it
holds a string, and the string is not blank. When one of them fails it
raises `ConfigError` with the file, the section and the key in the message.

Three checks sit outside `_require()` because they are about the file as a
whole rather than one key: the file has to parse as TOML, it has to have an
`[assets]` section, and that section has to hold at least one of `private`
and `common`. An `[assets]` table with only unknown names is refused rather
than silently ignored, since a typo like `[assets.privat]` would otherwise
look like a working config that backs nothing up.

`load_config()` originally fell back to the bundled example when the user
had no config file. That was removed: with no `config.env` in the image, it
made every unconfigured workspace clone
`https://gitlab.com/username/repository.git` with the placeholder
credentials, twice, and back up to it from then on. Whoever controlled that
namespace would have had their content land in every such workspace. A
missing file now disables the backup in `bootstrap.py` instead, the
container still starts, and placeholder values are refused outright.

### Implementation Report: Committing, Pulling and Resolving Conflicts

The first half made the workspace commit itself. `sync.py` gained
`commit_local_changes()`, which asks `git status --porcelain` whether there
is anything to save, stages everything with `git add -A` and commits with a
UTC timestamp in the message. `scheduler.py` runs it on a background daemon
thread so the HTTP server is never blocked, and `bootstrap.py` starts that
thread once from `admin.main.cli()`. The interval defaults to 300 seconds.
It was later moved into the config file as `SYNC_INTERVAL_SECONDS`, and
`--sync-interval` overrides it, which is useful for a demo where waiting
five minutes is not practical.

The commit identity is passed per command with `git -c user.name=... -c
user.email=...` rather than written into the repository. `git commit`
refuses to run without an identity, but the workspace's clones are the
user's own, and writing a service account into their `.git/config` would
show up in every commit they made by hand afterwards.

The second half made it a real synchronization. `pull_changes()` fetches the
remote and merges it in, and `push_if_ahead()` pushes only when
`git rev-list --count` says there is something the remote does not have,
which keeps a quiet workspace from producing a push every five minutes.

The order inside `sync_once()` is commit, then pull, then push. It has to be
that way: git refuses to merge over modified files, so pulling first would
fail on every interval as soon as the remote had anything to deliver.

Conflicts were the hard part. The rule we wanted is that local files win,
because the user is sitting in front of the file and did not ask for it to
be replaced. `git merge -X ours` does exactly that, but it resolves silently,
so there is no way to tell the user which files it decided for them. The
merge is therefore attempted twice. The first attempt is an ordinary merge
whose only job is to fail and name the conflicted files through
`git diff --name-only --diff-filter=U`. That attempt is thrown away with
`git merge --abort`, and the merge is redone with `-X ours`. The result is
the same tree `-X ours` would have produced on its own, but the conflicted
paths are known and get logged by name, and `pull_changes()` returns them to
its caller. Only the conflicted files keep the local version; everything
else the remote changed is merged in normally. `-X ours` was later
replaced, see
[Local Files Win the Whole File](#implementation-report-local-files-win-the-whole-file).

`sync_all()` catches every exception, not only `SyncError`. It runs on the
background thread, and an exception escaping there would end the thread and
stop all further backups while the service kept answering requests as if
nothing had happened.

### Implementation Report: Never Committing an Unfinished Conflict

A review found that a conflict `-X ours` cannot resolve, such as a file
changed on one side and deleted on the other, was committed and pushed one
interval later. The failing `-X ours` merge raised without aborting, so the
repository was left mid merge. The next `commit_local_changes()` saw the
unmerged file in `git status`, and `git add -A` plus `git commit` concluded
the merge as if someone had resolved it. The same path would have committed
a merge or rebase the user had started by hand, conflict markers and all.

The fix has two layers. The first removes the cause: `pull_changes()` now
runs `git merge --abort` when the `-X ours` merge fails, then re-raises, so
our own code never leaves a merge behind. The abort runs with
`check=False`, so if it fails too, the error reported is still the one
that explains the conflict.

The second layer guards the commit itself, whatever led to the state, and
lives in `commit_local_changes()`:

- Before anything else, `_ensure_nothing_in_progress()` looks for the
  entries git keeps while an operation is stopped (`MERGE_HEAD`,
  `CHERRY_PICK_HEAD`, `REVERT_HEAD`, `REBASE_HEAD`, `rebase-merge`,
  `rebase-apply`) and asks `git ls-files --unmerged` for unresolved files.
  Either stops the cycle with `SyncError`. It runs before `has_changes()`
  on purpose: with nothing to commit, the cycle would otherwise go on to
  merge, and the abort after a failed merge would throw away the merge the
  user was in the middle of.
- The git directory is asked of git with `git rev-parse
  --absolute-git-dir` instead of assumed to be `.git`. `clone.py` uses
  `--separate-git-dir`, so the working tree only holds a `.git` *file*, and
  a check against `.git/MERGE_HEAD` would never find anything.
- After `git add -A`, `_staged_conflict_markers()` runs `git diff --cached
  --check`. That command also reports trailing whitespace and a bare
  `=======`, which is a Markdown heading underline, so only its "leftover
  conflict marker" lines are kept, and only for files whose staged version
  has a line starting with `<<<<<<<` or `>>>>>>>`. Taking every warning
  would have stopped the backup for almost any user. When markers are
  found, `git reset` unstages again, so deleting the file by hand does not
  leave a staged addition that would fail the next commit.

Refusing raises `SyncError` rather than returning `False`, so the reason
reaches the log through the scheduler like any other failure instead of
reading as "nothing to commit".

Both layers were checked against real git as well as `FakeGit`: the
modify/delete case from the review, a hand-started merge left on a
conflict, markers in a file, and a Markdown heading.

### Implementation Report: Reporting Backup Failures

A review found that a failing backup was silent: the only trace was a log
line the user never sees, so a revoked token could stop the backup for
days without anyone noticing.

`/storage/status` now reports the last attempt, last success and last error of
each repository. The admin API has no authentication and binds to
`0.0.0.0`, so the response must not reveal anything an outsider could use,
and that drove the design:

- **Only an error kind leaves the process.** A `SyncError` message holds
  git's own stderr, which can contain a remote URL or a path, and the names
  of the user's files. Filtering that text was rejected: git can print
  something new at any time, and a filter only removes what it knows
  about. Instead `SyncError` carries a `SyncErrorKind` from a fixed list,
  chosen where the error is raised, and only that kind is stored. There is
  no field for a message in `RepoStatus`, so one cannot be added by
  accident.
- **A separate route rather than `/health`.** `/health` answers "is the
  process alive?". Health checks act on that answer, for example by
  restarting a container. A broken backup is no reason to restart a
  workspace the user is working in.
- **Read-only.** No route starts a backup or changes the status. A
  "sync now" button needs authentication first.
- **No global state.** `main.cli()` creates one `SyncStatus` and passes it
  to both `start_git_sync()` and `create_app()`. The sync thread writes and
  request handlers read, so every access goes through one lock, and
  `snapshot()` returns a copy.

An auth failure is now classified as `auth_failed` even when no
credentials are configured. The hint about an invalid or expired token is
still only added to the message when there is a token to blame.

**Not covered:** a repository that fails to *clone* on startup is never
scheduled, so it does not appear in `/storage/status`, and if no repository
was cloned the endpoint reports `enabled: false`. A wrong token on first
start is therefore still only visible in the log. This is left for a
separate issue.

### Implementation Report: Not Racing the User

The user works in the same repositories, in a terminal or in VS Code, while
the backup runs. A review found three ways the two could collide. The
first two make the cycle stop before it changes anything, as the existing
unfinished-merge check already did. Each has its own `SyncErrorKind`, so
`/storage/status` says why the backup is waiting.

- **The wrong branch, or none.** The push sends `refs/heads/<branch>`
  explicitly, but `git commit` and `git merge` act on whatever `HEAD` is.
  After `git switch experiment`, the backup committed to `experiment`,
  which was never pushed. On a detached `HEAD` (`git bisect`, a checked-out
  tag) the commits were on no branch at all.
  - `_ensure_on_branch()` runs `git symbolic-ref --quiet HEAD`. It exits
    with status 1 and prints nothing on a detached `HEAD`, otherwise it
    prints the full ref, which must equal `refs/heads/<branch>`. A real
    failure (status 128 with stderr) is reported as `git_failed`, not
    mistaken for a detached `HEAD`.
  - The check runs in the gate before the commit, and again before the
    merge, since a fetch can take minutes.
  - `push_if_ahead()` now counts `origin/<branch>..refs/heads/<branch>`
    instead of `..HEAD`, so it counts exactly what the push sends.
- **A leftover `index.lock`.** A git process killed with SIGKILL (by the
  runner's time limit after its grace period, the OOM killer, or a stopping
  container) leaves the lock behind. After that, every `git add` fails with
  `Unable to create '.../index.lock': File exists`, and the backup stops for
  good.
  - `_ensure_index_unlocked()` looks at the lock's modification time. A
    lock younger than `STALE_LOCK_SECONDS` (3600) means someone is using
    git right now, and the cycle is skipped.
  - An older lock is deleted, with a warning in the log, and the cycle goes
    ahead.
  - The hour is long on purpose: `git commit` holds the lock while its
    editor is open, and deleting a live lock would let the backup commit the
    user's staged changes under its own message.
- **Shutdown cutting a cycle off.** `start_git_sync()` created a stop event
  that `main.py` never kept, so nothing could stop the loop. On SIGTERM the
  process simply exited and the daemon thread died, whatever git command it
  was in.
  - `main.cli()` now creates the event, passes it to `start_git_backup()`,
    and hands `create_app()` an `on_shutdown` callback. `create_app()` runs
    that callback in a FastAPI lifespan after the server stops, off the
    event loop.
  - The callback, `stop_git_backup()`, sets the event and waits for the
    `git-bootstrap` thread, which now stays alive until the sync thread has
    ended.
  - `sync_all()` and `sync_once()` check the event between repositories
    and between commit, pull and push, raising `SyncStopped`. That is not a
    `SyncError`: a cycle cut short this way is neither a success nor a
    failure, and `/storage/status` records nothing for it. Each step leaves the
    repository consistent, so the next start carries on where this one
    stopped.
  - The wait is limited to `SHUTDOWN_TIMEOUT_SECONDS` (8), under the 10
    seconds `docker stop` allows. A fetch or push can take up to 900
    seconds, and a shutdown must not wait that long.

The checks were run against real git as well as `FakeGit`, with a separate
git directory as `clone.py` creates it: a switched branch, a detached
`HEAD`, a `git bisect` session, a fresh `index.lock` (left alone) and one
backdated by an hour (removed, and the commit went ahead). In the
workspace container, sending SIGTERM to `workspace-admin` produced
`Shutting down`, `Stopped the git backup` and `Application shutdown
complete`.

**Not covered:**

- **`docker stop` does not reach the admin service in the current image.**
  - PID 1 is Kasm's `vnc_startup.sh`. Its trap sends SIGTERM only to
    `custom_startup.sh` and exits straight away. When PID 1 exits, the
    kernel kills every other process in the container with SIGKILL.
  - `custom_startup.sh`, which would pass the signal on to
    `workspace-admin`, is in `sleep 3` at that moment, and bash runs a trap
    only after the current command finishes.
  - Observed: on `docker stop` the admin service logged nothing and the
    container was gone in under 3 seconds.
  - The shutdown path above therefore works when the service is run on
    its own or signalled directly. In the container, what protects the
    repository is the stale-lock handling, together with git's own
    atomic ref updates.
  - Changing the image's entrypoint is a separate decision.
- **Brief races remain.**
  - Between the branch check and the merge, a branch switch can still slip
    in. The window is now milliseconds rather than the length of a fetch.
  - A user's git command can take `index.lock` while the cycle is running.
    That command, or the backup's, then fails once, with `git_failed`, and
    the next interval tries again.
- **Only `index.lock` is handled.** A stale ref lock, such as
  `refs/heads/main.lock` from a process killed during the few microseconds
  it holds one, would still need removing by hand.
- **A git command still running when the wait gives up keeps running.**
  The runner starts each git command in its own session, so it is not
  killed when the service exits. It is no longer watched, and ends with the
  container.

### Implementation Report: Local Files Win the Whole File

A review found two problems with `git merge -X ours`:

- **It cannot resolve modify/delete.** When one side changed a file and the
  other deleted it, the merge failed on every interval, so the push never
  ran and the user's work never left the container.
- **It works per hunk, not per file.** Lines from the remote that did not
  conflict were merged into the "kept" file, so a notebook could end up
  mixed or unparsable.

The review proposed dropping automatic resolution. We kept the rule that
local files win, but per whole file. `pull_changes()` runs an ordinary
merge, and when it stops on a conflict, `git ls-files --unmerged -z` lists
the versions git holds of each conflicted file. If the workspace's version
(stage 2) exists, `git checkout --ours` takes it whole; if not, the
workspace deleted the file and `git rm` keeps it deleted. `git commit
--no-edit` then concludes the merge. Any failure on the way aborts the
merge, as before.

The old `_conflicted_files()` split git's output on whitespace, so a file
name with a space would have been read as two files. `-z` avoids that.

`tests/integration/test_git_sync_conflicts.py` runs this against real git,
with a local bare repository as the remote. Four of its five tests fail on
the old `-X ours` code.

### Implementation Report: Conflicts Are Left to the User

Keeping the local file whole fixed how a conflict was resolved, but not
that it was resolved at all. The review kept pointing out that the
collaborator's change was still replaced on the remote without anyone
being told, and that the specification asked for something else: an
ordinary merge, aborted on a conflict with its own error kind, and the
work pushed to a per-workspace backup branch every interval.

The reason for resolving automatically had been that a conflict must not
keep the user's work in the container. The backup branch removes that
reason, so the cycle was reordered:

- `sync_once()` pushes to `backup/<workspace-id>` right after the commit,
  every cycle, before the pull. Until then the branch was only pushed when
  a pull failed for good
- `pull_changes()` runs an ordinary merge. When it stops, `_merge_failure()`
  lists the conflicted files with `git ls-files --unmerged -z`, the merge is
  aborted, and the error has the new kind `merge_conflict`. It returns
  whether anything was merged, not a list of files
- `_take_local_side()` and `_local_side_of_conflicts()` are gone. So is
  `renamed_on_conflict`: git only renames a file aside inside a merge that
  is now always aborted
- `pull_failed` stays for a branch that cannot be pulled at all, and its
  message names the backup branch instead of pushing to it

The cost is that one conflicting file holds back every other change from
the remote until the user merges by hand. The documentation explains how,
using `origin/<branch>`, which the backup fetches, so the user needs no
credentials of their own.

`tests/integration/test_git_sync_conflicts.py` was rewritten for the new
behaviour: all four kinds of conflict leave both sides and the remote
untouched, and a merge finished by hand is pushed by the next cycle.

### Package Layout

The package is split by layer, so that transport concerns stay separate from
the logic they expose:

```text
src/admin/
├── __init__.py                   # Package marker
├── api.py                        # FastAPI app factory and HTTP routes
├── services.py                   # Workspace service discovery
├── main.py                       # Command-line entry point
├── config/
│   └── services_template.json    # Service catalogue (data, not code)
└── git/                          # Git backup of workspace directories
    ├── setup/                    # What to back up
    │   ├── locate.py             # Chooses which config.toml to read
    │   ├── provision.py          # Creates a missing config.toml from the template
    │   ├── config.py             # RepoConfig, SyncMode, ConfigError and the settings
    │   ├── validate.py           # Checks the values of config.toml
    │   └── load.py               # Reads config.toml into RepoConfig objects
    ├── run/                      # Running git
    │   ├── runner.py             # Runs git without prompts, within a time limit
    │   ├── command.py            # The one runner for every git command
    │   ├── auth.py               # HTTP auth header, https-only options, rejected tokens
    │   └── errors.py             # SyncErrorKind, SyncError, sorting failures by kind
    ├── repo/                     # Working on one repository
    │   ├── clone.py              # Clones a single RepoConfig
    │   ├── sync.py               # One cycle: commit, back up, pull, push
    │   ├── commit.py             # Commits the user's changes
    │   ├── pull.py               # Fetches and merges, or fast-forwards
    │   ├── push.py               # Pushes, and backs up to backup/<id>
    │   ├── refs.py               # Ref names and refspecs of the branch
    │   ├── busy.py               # Whether the user is busy with the repository
    │   └── checks.py             # What is checked in the files to commit
    └── service/                  # Running the backup in the service
        ├── bootstrap.py          # Starts and stops the backup
        ├── scheduler.py          # Runs clone and sync on a timer, in the background
        └── status.py             # Where the backup stands, for /storage/status
```

Dependencies point one way: `main` → `api` → `services`, and within `git/`:
`service` → `repo` → `run`/`setup`. Inside `repo`: `sync` → `pull`/`push`
→ `commit` → `busy`/`checks`. `setup` and `run` only share their bottom
modules, `setup/config.py` and `run/runner.py`, which import nothing from
the package. The tests mirror this layout under `tests/unit/git/` and
`tests/integration/git/`.

### Components

- **HTTP API** (`src/admin/api.py`): FastAPI application factory
  (`create_app`), the router holding the `/`, `/services`, `/health` and
  `/storage/status` routes, path prefix normalization, `APP_VERSION`, and a
  lifespan that runs an optional `on_shutdown` callback when the server
  stops. Contains transport logic only
- **Service Discovery** (`src/admin/services.py`): `load_services()`, which
  reads the service catalogue from the JSON template
- **CLI Entry Point** (`src/admin/main.py`): Argument parsing
  (`build_parser`) and the `workspace-admin` command (`cli`), which either
  prints the catalogue or serves the API with uvicorn
- **Git Backup** (`src/admin/git/`): Configuration, cloning, authentication
  and working tree synchronization for shared and private git assets
  - `run/runner.py`: `run_git()` runs every git command of the backup with
    prompts switched off, empty standard input and a time limit, and stops
    a command that overruns it together with the processes it started,
    raising `GitTimeoutError`. Extra environment, such as the credentials,
    is passed per command. See
    [Time limits and prompts](#time-limits-and-prompts)
  - `setup/locate.py`: `discover_config()` chooses the config file,
    `$WORKSPACE_ADMIN_CONFIG` before
    `~/.config/workspace-admin/config.toml`, and returns a
    `ConfigLocation` holding the absolute path and the `ConfigRule` that
    chose it. See [Configuration](#configuration)
  - `setup/config.py`: the types `RepoConfig`, `SyncMode`, `ConfigError` and
    `PlaceholderError`, and the names and limits of the settings
  - `setup/validate.py`: checks each value (https URL, branch, `MODE`, interval,
    placeholders) and warns about unknown keys and a file others can read
  - `setup/load.py`: `load_config()` reads the chosen file and returns a
    `RepoConfig` per `[assets.<name>]` table (`private` and `common`), with
    both paths derived from the name and the root, and its `SyncMode`.
    `load_sync_interval()` reads `SYNC_INTERVAL_SECONDS`
  - `run/auth.py`: `auth_environment()` builds the `GIT_CONFIG_COUNT`
    variables that carry `GIT_REPO_TOKEN`, with the fixed username
    `oauth2`, as an HTTP Basic header scoped to the repository's URL (see
    [How the token is sent](#how-the-token-is-sent)). `remote_options()`
    limits a command to https, switches off credential helpers and
    abandons stalled transfers. `is_auth_failure()` recognizes git's and
    GitLab's rejection texts, including `could not read Username`
  - `run/errors.py`: `SyncErrorKind`, the reasons `/storage/status` may report,
    and `SyncError`. `failure_kind()` sorts a failed command's output into
    `auth_failed`, `network` or `git_failed`
  - `run/command.py`: `run()` runs every git command of the backup and the
    clone, and turns a failure or time-out into a `SyncError` of the right
    kind; the caller logs it. `run_local()` and `run_remote()` add
    `--git-dir` and `--work-tree` for a cloned repository
  - `repo/busy.py`: `ensure_safe_to_sync()` stops a cycle while the user is
    busy with the repository (index lock, unfinished operation, branch,
    unmerged files); `ensure_on_branch()` is checked again before a merge
  - `repo/checks.py`: what is checked in the files about to be committed:
    `ensure_no_mass_deletion()`, `large_files()`,
    `staged_conflict_markers()` and `ensure_default_excludes()`
  - `repo/clone.py`: `clone_asset()` clones one `RepoConfig` with a separated
    git directory and working tree (`git clone --separate-git-dir`),
    skipping repositories that are already cloned, starting one locally on
    an empty remote, and raising `CloneError`, a `SyncError`, on failure.
    `restore_git_file()` writes back a lost `.git` file
  - `repo/refs.py`: the configured branch's ref names and refspecs
  - `repo/commit.py`: `commit_local_changes()` commits the user's changes,
    holding back large and marked files (`HeldBack`). The commit identity
    is passed per command with `git -c`, never written to `.git/config`
  - `repo/pull.py`: `pull_changes()` fetches and merges, aborting a conflicted
    merge; `pull_only()` fast-forwards a `pull` repository
  - `repo/push.py`: `push_if_ahead()` pushes the branch;
    `push_to_backup_branch()` force-pushes it to `backup/<workspace-id>`
  - `repo/sync.py`: `sync_once()` runs one cycle. For a `sync` repository:
    commit, back up, pull, push. For a `pull` repository: `pull_only()`.
    Held-back files are reported once the rest of the cycle is done
  - `service/scheduler.py`: `start_sync_scheduler()` runs `sync_all()` on a
    background daemon thread, every `SYNC_INTERVAL_SECONDS` from the
    config file (300 when it is not set), unless `--sync-interval`
    overrides it. A repository that is not cloned yet is cloned instead,
    by `clone_and_record()`, which `bootstrap.py` uses on startup too.
    A failing repository is logged and skipped rather than stopping the
    loop, and every exception is caught, not just `SyncError`, because the
    thread is the only thing keeping the backup alive. Each outcome is
    recorded in the `SyncStatus` it is given
  - `service/status.py`: `SyncStatus` holds the `BackupState`, and per repository
    its `RepoState`, last attempt, success and error kind, behind one
    lock. It stores a `SyncErrorKind`, never an error message
  - `setup/provision.py`: `provision_config()` copies the template into place
    when `config.toml` is missing (mode `0600`, never overwriting) and adds
    a `.gitignore` holding `*` to its directory
  - `service/bootstrap.py`: `start_git_backup()` is called once from
    `admin.main.cli()` on startup and returns at once, running
    `clone_configured_repos()` and then `start_git_sync()` on the
    `git-bootstrap` daemon thread, so the HTTP API never waits for a
    remote. The thread then waits for the sync thread, so
    `stop_git_backup()`, called on shutdown, has one thread to wait for.
    It sets the status to `needs_setup`, `config_error`, `starting` or
    `running` as it goes. Errors are logged, never raised, so a missing or
    broken git configuration does not prevent the admin service from
    starting
- **Services Template** (`src/admin/config/services_template.json`): JSON
  template defining available services
- **nginx Configuration** (`startup/nginx.conf`): Reverse proxy routing
- **Startup Script** (`startup/custom_startup.sh`): Service bootstrap and
  monitoring

## Environment Variables

- `ADMIN_SERVER_PORT`: Port for the admin service (default: `8091`)
- `PATH_PREFIX`: Optional path prefix for API routes (can also be set via CLI `--path-prefix` argument)
- `PERSISTENT_DIR`: Root of the git backup. Every repository is placed
  below it, wherever `config.toml` is (default: the home directory;
  `/workspace` in the workspace image)
- `WORKSPACE_ADMIN_CONFIG`: The config file of the git backup. When unset
  or empty, `~/.config/workspace-admin/config.toml` is used. A relative
  path is resolved against the directory the service is started in. The
  workspace image sets it to
  `$PERSISTENT_DIR/.config/workspace-admin/config.toml` when it starts
  `workspace-admin`, and only for that process: Jupyter and VS Code do
  not see it
- `WORKSPACE_CONFIG_TEMPLATE`: Template copied to `config.toml` when the
  file is missing. Set by the Dockerfile; unset outside the image, where a
  missing file leaves the backup disabled

## Development

### Running Tests

```bash
cd workspaces/src/admin
poetry install
poetry run pytest --cov=admin --cov-report=html --cov-report=term-missing
```

The test files mirror the package layout, so a change to a module has one
obvious test file:

| Test file             | Module under test  | Covers                                     |
| --------------------- | ------------------ | ------------------------------------------ |
| `tests/unit/test_api.py`   | `admin/api.py`     | Routes, responses, path prefix handling, `/storage/status` serving the live status and refusing writes, the shutdown hook running once when the server stops |
| `tests/unit/test_services.py` | `admin/services.py` | Catalogue loading and template integrity |
| `tests/unit/test_main.py`  | `admin/main.py`    | Argument parsing, CLI flags, git backup handed off before serving with the same status object the API reports, and stopped by the server's shutdown hook, no interval passed on without `--sync-interval`, which is range-checked like the config file |
| `tests/unit/git/run/test_auth.py` | `admin/git/run/auth.py` | Auth header built/omitted based on credentials, https-only protocol options, credential helpers reset and stalled transfers abandoned, token never in the header text, recognizing (and not mis-recognizing) auth-failure stderr |
| `tests/unit/git/setup/test_config.py` | `admin/git/setup/load.py`, `validate.py` | Parsing, path resolution, missing/empty/mistyped keys, template fallback, token kept out of `repr`, branch names checked with `git check-ref-format` (and a hung check reported), https-only URLs without credentials, `SYNC_INTERVAL_SECONDS` read and defaulted, an out-of-range, mistyped or misplaced value falling back to 300 with a warning while the repositories still load |
| `tests/unit/git/repo/test_clone.py` | `admin/git/repo/clone.py` | Successful clone, already-cloned skip, failed clone, timed-out clone and its time limit, https-only transport, `--` before the URL, credential handling, clear error on invalid/expired token with the token never in the message |
| `tests/unit/git/repo/test_checks.py` | `admin/git/repo/checks.py` | Mass deletion limits (an emptied tree of any size refused), large files, real conflict markers only, an outdated exclude block replaced, an unwritable exclude file logged |
| `tests/unit/git/repo/test_sync.py` | `admin/git/repo/commit.py`, `pull.py`, `push.py`, `sync.py` | Changes detected, no changes, clean merges, conflict detection and resolution, failed fetch/commit/merge/push, commit identity, timestamped message, explicit refspecs after `--`, time limits per command (long for fetch/push, short for local) and a hung command ending the cycle, https-only transport, an option-shaped branch never reaching git as an option, credential handling, clear error on invalid/expired token with the token never in the message, wrong branch and detached `HEAD` refused (also after the fetch), outgoing commits counted by branch name, a fresh `index.lock` refused and a stale one removed, stopping between steps |
| `tests/unit/git/service/test_scheduler.py` | `admin/git/service/scheduler.py` | Syncing every repository, surviving one that fails, running until stopped, recording each outcome without the error message, the stop event reaching a running round, which records nothing for a cycle cut short |
| `tests/unit/git/service/test_status.py` | `admin/git/service/status.py` | Disabled until enabled, success and failure timestamps, last error kept after a success, snapshots are copies, concurrent writers |
| `tests/unit/git/run/test_runner.py` | `admin/git/run/runner.py` | Prompts off, empty stdin, failures returned, overrunning commands stopped (SIGTERM first, then SIGKILL, including the processes they started) |
| `tests/unit/git/service/test_bootstrap.py` | `admin/git/service/bootstrap.py` | Startup wiring: the clone running on a background thread without holding up the caller, cloning every configured repository (`private` and `common`), one repository's clone failure not blocking another's, scheduling only cloned repositories, and graceful handling of config/clone failures, the caller's stop event used, no sync started after a stop during the clone, and a shutdown wait that gives up after its limit, the interval read from the config file unless one is given, and no sync started when the file can no longer be read |
| `tests/integration/git/repo/test_sync_conflicts.py` | `admin/git/repo/pull.py`, `sync.py` | Conflicts against real git: every kind aborts the merge and keeps both sides, the backup branch is pushed every cycle, a merge finished by hand is pushed |
| `tests/unit/git/run/test_errors.py` | `admin/git/run/errors.py` | Sorting a failed command into `auth_failed`, `network` or `git_failed` |
| `tests/unit/git/run/test_command.py` | `admin/git/run/command.py` | Repository options before the subcommand, remote options, credentials and time limits, failures by kind with the token hint only when a token was sent, unchecked failures, time-outs, reading git's output |
| `tests/integration/git/repo/test_sync_repository.py` | `admin/git/repo/sync.py`, `clone.py` | A lost `.git` file never redirects the backup to an enclosing repository; an emptied working tree is not committed; a deleted working tree is restored on startup and before a cycle |
| `tests/integration/git/repo/test_pull_only.py` | `admin/git/repo/pull.py` | Pull-only repositories: the remote's changes arrive, local edits are never pushed, an edit in the way is reported and kept |
| `tests/integration/git/repo/test_remote_changes.py` | `admin/git/repo/sync.py`, `clone.py` | A rewritten history or renamed branch is a `pull_failed` with the work on the backup branch; an empty remote is started and filled |
| `tests/integration/git/repo/test_auth_failure.py` | `admin/git/repo/clone.py` | A real 401 from a local HTTP server is reported as `auth_failed`; the token reaches only the repository's URL, not another repository on the host |
| `tests/integration/git/repo/test_backup_hygiene.py` | `admin/git/repo/commit.py`, `checks.py` | Files held back for conflict markers or size while the rest is committed, and named when the remote changed them too; default excludes |

`tests/helpers/repo_factory.py` is a shared helper rather than a test file:
it builds the `RepoConfig` objects the git tests need, so no test module has
to repeat the full seven-field construction.

### Code Quality and Coverage

**Run pylint for code quality analysis:**

```bash
cd workspaces/src/admin
poetry run pylint src/admin tests
```

**Run tests with coverage analysis:**

```bash
cd workspaces/src/admin
poetry run pytest --cov=admin --cov-report=html --cov-report=term
```

This will generate a coverage report in `htmlcov/index.html` and display a
summary in the terminal.

### Running Locally

**As a service:**

```bash
cd workspaces/src/admin
export ADMIN_SERVER_PORT=8091
poetry run workspace-admin --path-prefix dtaas-user
```

**As a CLI utility:**

```bash
cd workspaces/src/admin
poetry install

# Run the service
poetry run workspace-admin

# Run with custom host and port
poetry run workspace-admin --host 127.0.0.1 --port 9000

# Run with path prefix for multi-user deployments
poetry run workspace-admin --path-prefix dtaas-user

# List services without starting the server
poetry run workspace-admin --list-services

# Run with auto-reload for development
poetry run workspace-admin --reload

# Back up the workspace every 30 seconds, whatever SYNC_INTERVAL_SECONDS
# in config.toml says. Values outside 1 to 86400 seconds are refused.
poetry run workspace-admin --sync-interval 30

# Show help
poetry run workspace-admin --help
```

The CLI interface makes the admin service work like a system utility similar to
glances, allowing easy command-line operation and service listing.

### Adding New Services

To add a new service to the workspace:

1. Update `config/services_template.json` with the new service definition:

```json
{
  "new_service": {
    "name": "New Service Name",
    "description": "Description of the service",
    "endpoint": "path/to/service"
  }
}
```

2. The service automatically reads and processes the template - no code changes required

## Integration with DTaaS

The `/services` endpoint enables the DTaaS frontend to:

1. Dynamically discover available workspace services
2. Display service shortcuts to users
3. Support different workspace configurations without hardcoded service lists
4. Handle multi-user deployments where each user has different available
   services

This replaces the previous approach of hardcoding service endpoints in the
frontend configuration, enabling more flexible workspace deployments.

## Future Enhancements

Potential future enhancements include:

- Service registry for dynamic service registration
- Authentication and authorization integration
- Service health monitoring and status reporting
- Custom service definitions per workspace type
- Web application firewall integration for zero-trust security
