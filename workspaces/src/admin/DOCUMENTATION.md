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
    "/health": "Health check endpoint"
  }
}
```

## Configuration

The git backup reads one file, `config.env`, from `$WORKSPACE_APP_DIR`. The
name ends in `.env`, but the contents are TOML: the file is parsed with
`tomllib`, so quoting and section syntax follow TOML rules rather than shell
rules. A file to copy and edit ships with the package as
`src/admin/config/config.env.example`.

If `$WORKSPACE_APP_DIR/config.env` does not exist, the git backup is
disabled: nothing is cloned, nothing is synchronized, and the log shows
`<path>/config.env not found; git backup is disabled`. The bundled example
is never read at runtime. Its `GIT_REPO_URL`, `GIT_REPO_USERNAME` and
`GIT_REPO_TOKEN` are placeholders, and `load_config()` refuses any
repository still carrying one, so an unedited copy cannot send a request to
a remote. `private` and `common` must also use different remotes (or at
least different branches of one), since both would otherwise push to the
same branch.

In the workspace image, `custom_startup.sh` sets `WORKSPACE_APP_DIR` to
`$PERSISTENT_DIR` (`/workspace`) unless it is already set, so the operator
enables the backup by placing `config.env` in the user's persistent volume
(with the bundled compose files, `files/<username>/config.env` on the
host).

### Top-level keys

All three are required.

| Key | Meaning |
| --- | ------- |
| `HOME_DIR` | The user's home directory inside the container |
| `WORKSPACE_DIR` | Root for the git directories. |
| `WORKSPACE_APP_DIR` | Root for the working trees. A value starting with `/` is used as it is, anything else is resolved against `HOME_DIR` |

### Repository sections

Every repository is a `[assets.<name>]` table. Two names are recognised,
`private` and `common`, and at least one of them has to be there. All six
keys are required in each section that is present.

| Key | Meaning |
| --- | ------- |
| `GIT_REPO_URL` | HTTPS URL of the remote. Must start with `https://` and must not contain a username or token (`user:token@`) |
| `GIT_REPO_BRANCH` | The one branch that is cloned and kept in sync. Must be a valid git branch name, as checked by `git check-ref-format --branch` |
| `GIT_REPO_USERNAME` | Username for HTTP Basic authentication |
| `GIT_REPO_TOKEN` | Token for HTTP Basic authentication |
| `GIT_DIR` | Git directory, relative to `WORKSPACE_DIR` |
| `GIT_WORK_TREE` | Working tree, relative to `WORKSPACE_APP_DIR` |

The two path keys are fragments, not whole paths. `load_config()` joins each
one to its own root and returns absolute paths, so no other module has to
remember which fragment belongs where. Leading `/` is treated as absolute on
every platform, because the paths describe the container filesystem and not
the machine the code happens to run on.

`GIT_REPO_TOKEN` is kept out of `RepoConfig.__repr__`, so printing or
logging a configuration cannot leak it.

### Example

```toml
HOME_DIR = "/home/dtaas-user"
WORKSPACE_DIR = "/workspace"
WORKSPACE_APP_DIR = ".workspace"

[assets.common]
GIT_REPO_URL = "https://gitlab.com/username/common.git"
GIT_REPO_BRANCH = "main"
GIT_REPO_USERNAME = "gitlab-username"
GIT_REPO_TOKEN = "gitlab-api-token"
GIT_DIR = "common"
GIT_WORK_TREE = "assets/common"
```

With the values above, the `common` repository gets its git directory at
`/workspace/common` and its working tree at
`/home/dtaas-user/.workspace/assets/common`.

### When the file is wrong

Anything that makes the configuration unusable raises `ConfigError`, and the
message names the file, the section and the key at fault so the file can be
fixed without reading any code. The cases are: the file cannot be read, it
is not valid TOML, a required key is missing, a key holds something other
than a quoted string, a key is present but empty, there is no `[assets]`
section, or `[assets]` exists but holds neither `private` nor `common`.

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
`config.env` into the workspace, so the user's files are there before they
open Jupyter or VS Code. This section describes that process for the two
repositories the service knows about, `private` and `common`.

### Private and common

The two repositories are cloned in exactly the same way. They differ only in
what they are for:

- **`private`** (`[assets.private]`) holds the user's own files and points
  at a repository only that user works in.
- **`common`** (`[assets.common]`) holds files shared between users. Every
  workspace that configures the same `common` remote gets a clone of its
  own, and the remote is what the clones share.

Either one can be left out of `config.env`, but at least one of them has to
be there. When both are configured, `private` is cloned first.

### When cloning happens

`admin.main.cli()` calls `clone_configured_repos()` once on startup, before
the HTTP server starts. There is no endpoint or command that clones later,
so after adding a repository to `config.env` or changing one, restart the
service (in the workspace container: restart the container).

The configuration is read from `$WORKSPACE_APP_DIR/config.env`, where
`WORKSPACE_APP_DIR` is the environment variable, or from `config.env` in the
directory the service was started in if the variable is not set. If there is
no file there, nothing is cloned (see [Configuration](#configuration)).

### Where each repository ends up

Every repository is split in two:

- the **git directory** holds git's own data (history, objects, refs, the
  remote URL). It is placed at `WORKSPACE_DIR` + `GIT_DIR`
- the **working tree** holds the checked-out files the user edits. It is
  placed at `WORKSPACE_APP_DIR` + `GIT_WORK_TREE`, where `WORKSPACE_APP_DIR`
  is the key in `config.env`, resolved against `HOME_DIR` unless it starts
  with `/`

With the values in `config.env.example`
(`HOME_DIR = "/home/username"`, `WORKSPACE_DIR = "/workspace"`,
`WORKSPACE_APP_DIR = ".workspace"`), the result is:

```text
/workspace/                              # WORKSPACE_DIR
├── private/                             # git directory of private
└── common/                              # git directory of common

/home/username/.workspace/               # HOME_DIR + WORKSPACE_APP_DIR
└── assets/
    ├── private/                         # working tree of private
    │   └── .git                         # file: "gitdir: /workspace/private"
    └── common/                          # working tree of common
        └── .git                         # file: "gitdir: /workspace/common"
```

The working tree does not contain a `.git` directory, only a one-line `.git`
file pointing at the git directory. That file is the only link between the
two: git commands run in the working tree find the git directory through it.

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
       -c http.extraHeader="Authorization: Basic <username:token, base64>" \
       clone --branch <GIT_REPO_BRANCH> --single-branch \
       --separate-git-dir=<git dir> -- <GIT_REPO_URL> <working tree>
   ```

   - `--branch` and `--single-branch` fetch only the configured branch, so
     the clone never contains the remote's other branches.
   - `--separate-git-dir` is what puts the git directory and the working
     tree in the two places described above.
   - The `-c http.extraHeader=...` option carries `GIT_REPO_USERNAME` and
     `GIT_REPO_TOKEN` as HTTP Basic credentials for this one command. They
     are not written into the git directory's config and not added to the
     remote URL, so the token is not stored anywhere in the clone.
   - The `protocol.*` options make git refuse every transport except
     https, and `--` stops the URL and path from being read as options.
4. **Report the result.** A successful clone logs
   `Cloned repository 'private' (<url>, branch <branch>) into <working tree>`.

### Requirements for a clone to succeed

- `GIT_REPO_URL` is an HTTPS URL. The credentials are sent as an HTTP
  header, so they are not used for an SSH URL. Any other kind of URL is
  refused when the configuration is loaded.
- The token is accepted by the remote and allows reading the repository
  (on GitLab: the `read_repository` scope; the later backup also needs
  `write_repository` to push).
- `GIT_REPO_BRANCH` exists on the remote. A new repository with no commits
  has no branches, so it has to be given a first commit (on GitLab:
  *Initialize repository with a README*) before it can be cloned.
- Neither the git directory nor the working tree already exists with files
  in it. Git refuses to clone into a directory that is not empty, and fails
  with `already exists and is not an empty directory`. A directory that
  exists but is empty is fine.

### When a clone fails

A failed clone is logged, and the service carries on:

```text
ERROR admin.git.clone Failed to clone repository 'common' (<url>, branch main) into <working tree>: <git's message>
ERROR admin.git.bootstrap git clone failed for repository 'common': <git's message>
```

- Each repository is cloned on its own, so a failure in `common` does not
  stop `private` from being cloned, and the other way round.
- If the failure looks like rejected credentials, the second message ends
  with `(the configured GIT_REPO_USERNAME/GIT_REPO_TOKEN was rejected - it
  may be invalid or expired; check config.env)`. GitLab gives the same answer
  for a wrong token as for an expired one, so the message does not claim to
  know which it is. The token itself never appears in the log.
- If `config.env` cannot be used at all, nothing is cloned and the log shows
  `Cannot clone git assets: ...` with the reason (see
  [When the file is wrong](#when-the-file-is-wrong)).
- A failed clone is not retried while the service runs. It is tried again
  the next time the service starts. Git cleans up after a failed clone, so
  the git directory and working tree are left empty and do not block the
  next attempt.

In none of these cases does the admin service stop: the HTTP API starts as
normal.

### After cloning

`start_git_sync()` runs next and schedules the periodic backup for every
repository whose git directory holds a clone. A repository that failed to
clone is not backed up. See [Git Backup Flow](#git-backup-flow) for what
the backup does.

## Conflicts: What Is Automatic and What Is Manual

Most of the time the backup needs nothing from the user. A few situations
cannot be decided safely by a program, and there the backup stops for that
repository, logs why, and tries again every interval until a person has
sorted it out. It never guesses: nothing half finished is committed or
pushed.

| Situation | Handled by | What the backup does | To get sync going again |
| --- | --- | --- | --- |
| Both sides changed the same file | Backup | Keeps the workspace's version, merges everything else | Nothing to do |
| One side changed a file the other deleted | You | Leaves the repository as it was and fails each interval | Merge, keep or delete the file, commit |
| A merge, rebase, cherry-pick or revert you have started is unfinished | You | Waits: no commit, no pull, no push | Finish or abort the changes |
| Git still lists files as conflicted | You | Waits: no commit, no pull, no push | Fix the files, then `git add` |
| A file holds conflict markers | You | Refuses to commit, unstages again | Remove the markers |

Every command below is run in the repository's working tree, which with the
example config is `/home/username/.workspace/assets/<name>`. The service can
keep running while you work: as long as your merge is unfinished, the
backup waits for it instead of interfering.

If `git commit` asks who you are, add `-c user.name="Your Name" -c
user.email="you@example.com"` right after `git`. The clone does not store an
identity, so that nothing is written into the repository on your behalf.

### Both sides changed the same file

Handled automatically. The log names the files whose local version was kept:

```text
WARNING admin.git.sync Merge conflict in repository 'common' affecting 1 file(s): notes.md
INFO admin.git.sync Resolved the conflict in repository 'common' by keeping the local version of: notes.md
```

The remote's version of those files is not lost: it is still in the
history, in the commit that was merged in.

### One side changed a file the other deleted

Keeping "the local version" means nothing when one side has no version, so
git cannot resolve this on its own. The backup aborts the merge, which
leaves your files exactly as they were, and logs on every interval:

```text
ERROR admin.git.sync Failed to merge the remote's changes keeping local files for repository 'common': CONFLICT (modify/delete): notes.md deleted in origin/main and modified in HEAD. ...
```

Until it is resolved, that repository is neither pulled nor pushed.

**To get sync going again**, start the merge yourself and decide whether
the file stays:

```bash
git merge origin/main
git add notes.md      # keep the file, or: git rm notes.md to accept the deletion
git commit -m "Resolve notes.md"
```

The next interval pushes the result, and the error stops.

### A merge or rebase you started is unfinished

If you started a merge, rebase, cherry-pick or revert by hand and it stopped
on a conflict, the backup leaves it alone:

```text
ERROR admin.git.scheduler Not synchronizing repository 'common': a git operation is in progress (MERGE_HEAD); finish or abort it first
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
ERROR admin.git.scheduler Not synchronizing repository 'common': unresolved conflicts in notes.md
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
it is about to commit and refuses:

```text
ERROR admin.git.scheduler Not committing in repository 'common': conflict markers left in notes.md
```

Nothing is committed, and the files are unstaged again, so your working tree
is as you left it.

**To get sync going again**, open each file the log names. Between
`<<<<<<<` and `=======` is one version, between `=======` and `>>>>>>>` the
other. Keep the lines you want and delete the rest, the three marker lines
included. Save the file, or delete it if you do not need it. No git command
is needed: the next interval commits as normal.

Only lines starting with `<<<<<<<` or `>>>>>>>` count. A line holding just
`=======` is also how Markdown underlines a heading, so on its own it is
committed as normal. A file that holds a line starting with `<<<<<<<` on
purpose, such as notes about git, is refused all the same.

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

1. On startup, `admin.main.cli()` clones every configured git asset
   (`clone_configured_repos()`, one clone per `[assets.private]` and/or
   `[assets.common]` entry in `config.env`) and then starts the backup
   scheduler (`start_git_sync()`)
2. The scheduler runs on a background daemon thread, so it never blocks the
   HTTP server
3. Every 5 minutes each cloned working tree goes through the same three
   steps, in this order:
   1. **Commit** - the repository is first checked for an unfinished
      merge, rebase, cherry-pick or revert and for unresolved files, and
      the whole cycle stops if it finds one. Then `git status --porcelain`
      decides whether there is anything to save. A clean tree is skipped; a
      dirty one is staged, checked for conflict markers, and committed with
      a timestamped message (see
      [Conflicts](#conflicts-what-is-automatic-and-what-is-manual))
   2. **Pull** - the remote is fetched and merged in. Local files win any
      conflict (see below)
   3. **Push** - the branch is pushed only when it actually holds commits
      the remote does not
4. Committing happens *before* merging on purpose: git refuses to merge over
   modified files, so an uncommitted working tree would make every pull fail
   as soon as the remote had something to deliver
5. A repository that fails is logged and skipped, so one broken remote does
   not stop the others from being backed up. Unexpected errors are caught
   too: an exception escaping the background thread would end the backup
   silently while the service carried on serving requests

The user never runs a git command: saving a file in Jupyter or VS Code is
enough for it to reach the remote within one interval.

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
thread once from `admin.main.cli()`. The interval defaults to 300 seconds
and can be shortened with `--sync-interval`, which is useful for a demo
where waiting five minutes is not practical.

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
else the remote changed is merged in normally.

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
│   ├── services_template.json    # Service catalogue (data, not code)
│   └── config.env.example        # Example git asset configuration
└── git/
    ├── __init__.py                # Git backup of workspace directories
    ├── config.py                  # Reads config.env into RepoConfig objects
    ├── auth.py                    # Shared HTTP auth header and failure detection
    ├── clone.py                   # Clones a single RepoConfig
    ├── sync.py                    # Commits and pushes one working tree
    ├── scheduler.py               # Runs the sync on a timer, in the background
    └── bootstrap.py               # Wires the above together on startup
```

Dependencies point in one direction only: `main` → `api` → `services`. No
module imports the layer above it, so each can be tested on its own.

### Components

- **HTTP API** (`src/admin/api.py`): FastAPI application factory
  (`create_app`), the router holding the `/`, `/services` and `/health`
  routes, path prefix normalization, and `APP_VERSION`. Contains transport
  logic only
- **Service Discovery** (`src/admin/services.py`): `load_services()`, which
  reads the service catalogue from the JSON template
- **CLI Entry Point** (`src/admin/main.py`): Argument parsing
  (`build_parser`) and the `workspace-admin` command (`cli`), which either
  prints the catalogue or serves the API with uvicorn
- **Git Backup** (`src/admin/git/`): Configuration, cloning, authentication
  and working tree synchronization for shared and private git assets
  - `config.py`: `load_config()` reads the TOML-formatted `config.env` file
    at `$WORKSPACE_APP_DIR/config.env` and returns a `RepoConfig` per
    `[assets.<name>]` table (currently `private` and `common`)
  - `auth.py`: `auth_header_options()` builds the one-off
    `http.extraHeader` carrying `GIT_REPO_USERNAME`/`GIT_REPO_TOKEN` as
    HTTP Basic credentials, shared by `clone.py` and `sync.py` so neither
    writes credentials into `.git/config` or a remote URL. `is_auth_failure()`
    recognizes GitLab/git's own rejection text (`Authentication failed`,
    `HTTP Basic: Access denied`, `returned error: 401`/`403`) in a failed
    command's stderr, without ever matching on the credentials themselves
  - `clone.py`: `clone_asset()` clones one `RepoConfig` with a separated
    git directory and working tree (`git clone --separate-git-dir`),
    skipping repositories that are already cloned and raising
    `CloneError` on failure. When the failure looks like a rejected
    credential (`is_auth_failure()`) and the repository has one
    configured, the error is extended with a clear, token-free hint that
    `GIT_REPO_USERNAME`/`GIT_REPO_TOKEN` may be invalid or expired
  - `sync.py`: `sync_once()` runs one full cycle for an already-cloned
    working tree - `commit_local_changes()`, then `pull_changes()`, then
    `push_if_ahead()` - returning `False` when the repository was already
    in sync and raising `SyncError` when a step fails, extended with the
    same invalid/expired-token hint as `clone.py` when the push is
    rejected for credentials. Commit messages carry a UTC timestamp.
    `pull_changes()` returns the files whose local version it kept, so a
    conflict can be logged by name. The commit
    identity and the credentials header are passed per command with
    `git -c`, because `clone.py` deliberately leaves both out of the
    repository's own `.git/config`
  - `scheduler.py`: `start_sync_scheduler()` runs `sync_once()` for every
    repository on a background daemon thread, every
    `DEFAULT_SYNC_INTERVAL_SECONDS` (300) seconds. A failing repository is
    logged and skipped rather than stopping the loop, and `sync_all()`
    deliberately catches every exception, not just `SyncError`, because
    the thread is the only thing keeping the backup alive
  - `bootstrap.py`: `clone_configured_repos()` and `start_git_sync()` wire
    the other modules together and are called once from `admin.main.cli()`
    on startup. `clone_configured_repos()` clones every repository
    `load_config()` returns - `private` and/or `common` - independently:
    one repository that fails to clone (bad credentials, unreachable
    remote, ...) is logged and skipped without blocking the others.
    `start_git_sync()` then schedules every repository that is actually
    cloned, so assets added later are picked up as soon as they exist on
    disk. Errors are logged, never raised, so a missing or broken git
    configuration does not prevent the admin service from starting
- **Services Template** (`src/admin/config/services_template.json`): JSON
  template defining available services
- **nginx Configuration** (`startup/nginx.conf`): Reverse proxy routing
- **Startup Script** (`startup/custom_startup.sh`): Service bootstrap and
  monitoring

## Environment Variables

- `ADMIN_SERVER_PORT`: Port for the admin service (default: `8091`)
- `PATH_PREFIX`: Optional path prefix for API routes (can also be set via CLI `--path-prefix` argument)
- `WORKSPACE_APP_DIR`: Directory that holds `config.env`, the git asset
  configuration file read by `admin.git.bootstrap` on startup, both to
  clone the assets and to schedule their backup (default: current
  directory; `$PERSISTENT_DIR` in the workspace image). Without that file
  the git backup is disabled

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
| `tests/unit/test_api.py`   | `admin/api.py`     | Routes, responses, path prefix handling     |
| `tests/unit/test_services.py` | `admin/services.py` | Catalogue loading and template integrity |
| `tests/unit/test_main.py`  | `admin/main.py`    | Argument parsing and CLI flags              |
| `tests/unit/test_git_auth.py` | `admin/git/auth.py` | Auth header built/omitted based on credentials, https-only protocol options, token never in the header text, recognizing (and not mis-recognizing) auth-failure stderr |
| `tests/unit/test_git_config.py` | `admin/git/config.py` | Parsing, path resolution, missing/empty/mistyped keys, template fallback, token kept out of `repr`, branch names checked with `git check-ref-format`, https-only URLs without credentials |
| `tests/unit/test_git_clone.py` | `admin/git/clone.py` | Successful clone, already-cloned skip, failed clone, https-only transport, `--` before the URL, credential handling, clear error on invalid/expired token with the token never in the message |
| `tests/unit/test_git_sync.py` | `admin/git/sync.py` | Changes detected, no changes, clean merges, conflict detection and resolution, failed fetch/commit/merge/push, commit identity, timestamped message, explicit refspecs after `--`, https-only transport, an option-shaped branch never reaching git as an option, credential handling, clear error on invalid/expired token with the token never in the message |
| `tests/unit/test_git_scheduler.py` | `admin/git/scheduler.py` | Syncing every repository, surviving one that fails, running until stopped |
| `tests/unit/test_git_bootstrap.py` | `admin/git/bootstrap.py` | Startup wiring: cloning every configured repository (`private` and `common`), one repository's clone failure not blocking another's, scheduling only cloned repositories, and graceful handling of config/clone failures |

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

# Back up the workspace every 30 seconds instead of every 5 minutes.
# Values below 1 second are refused: the backup would never wait.
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
