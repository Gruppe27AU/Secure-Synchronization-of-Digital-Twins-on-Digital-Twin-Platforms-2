# Admin Service

FastAPI service for workspace service discovery and management.

## Features

- `/services` endpoint - Returns JSON list of available workspace services
- Path prefix support for multi-user deployments
- Command-line interface for standalone operation
- Periodic git backup - commits, pulls and pushes the workspace's git assets
  every five minutes by default, so users never run a git command themselves
- Conflict resolution that keeps the workspace's own version of a file when
  it and the remote have both changed
- Never commits an unfinished merge or a file with conflict markers; those
  wait for the user (see
  [Conflicts](DOCUMENTATION.md#conflicts-what-is-automatic-and-what-is-manual))
- Git assets configured in one TOML file, `config.toml`, which is validated
  on startup so a mistake in it is reported by name instead of failing
  later inside git

## Layout

```text
src/admin/
├── api.py                        # FastAPI app factory and HTTP routes
├── services.py                   # Workspace service discovery
├── main.py                       # Command-line entry point
├── config/
│   └── services_template.json    # Service catalogue
└── git/                          # Git backup of workspace directories
    ├── config.py                 # config.toml -> RepoConfig
    ├── auth.py                   # Shared HTTP auth header and failure detection
    ├── clone.py                  # Clone a single RepoConfig
    ├── sync.py                   # Commit, pull and push one working tree
    ├── scheduler.py              # Run the sync on a timer, in the background
    ├── status.py                 # Outcome of the last backups, for /storage/status
    ├── provision.py              # Create a missing config.toml from the template
    ├── runner.py                 # Run one git command with a time limit
    └── bootstrap.py              # Wire the above together on startup
```

See [DOCUMENTATION.md](DOCUMENTATION.md) for the full architecture and
endpoint reference. Parts of it still describe an older layout of
`config.toml`; where it disagrees with this README, this README is right.

## Where the Config File Lives

The service always reads its config file from one place:

```text
$PERSISTENT_DIR/.config/workspace-admin/config.toml
```

When `PERSISTENT_DIR` is not set, your home directory is used instead. The
directory you start `workspace-admin` from does not matter.

| Where it runs | Config file |
| --- | --- |
| Workspace container | `/workspace/.config/workspace-admin/config.toml` |
| Locally, Linux or macOS | `~/.config/workspace-admin/config.toml` |
| Locally, Windows | `%USERPROFILE%\.config\workspace-admin\config.toml` |

The full path is logged every time the service starts.

- **In the container** the file is created for you the first time.
- **Locally** you create the directory and the file yourself; see
  [Run it locally](#run-it-locally).

The repositories are placed below the same root: each one's files in
`assets/<name>` and its git data in `.local/share/workspace-admin/<name>`.

## Getting Started

These steps take you from nothing to a running git backup:

1. [Prepare the remote repositories](#1-prepare-the-remote-repositories)
2. [Understand the config file](#2-understand-the-config-file)
3. Run it, either [locally](#run-it-locally) or
   [in the workspace container](#run-it-in-the-workspace-container)
4. [Check the backup](#checking-the-backup)

### 1. Prepare the remote repositories

The service backs up two repositories: `private` for your own files and
`common` for files shared between users. You need at least one of them.

- Create the repository on GitLab (or another git host). It needs at least
  one commit on the branch you will use, so tick *Initialize repository
  with a README* when creating it.
- Create an access token with the `read_repository` and
  `write_repository` scopes.

### 2. Understand the config file

This is the complete example. It is the same as
[`templates/config.toml.example`](../templates/config.toml.example):

```toml
# Seconds between two backups, from 1 to 86400 (one day). Optional:
# without it the backup runs every 300 seconds. Keep it above the
# [assets] sections, or TOML reads it as part of the section before it.
SYNC_INTERVAL_SECONDS = 300

# Git repository configuration. The section name decides where each
# repository's files appear: /workspace/assets/<name>.

[assets.private]
GIT_REPO_URL = "https://gitlab.com/username/repository.git"
GIT_REPO_BRANCH = "main"
# HTTP token for authentication
GIT_REPO_TOKEN = "gitlab-api-token"

# Uncomment to also back up files shared between users.
# [assets.common]
# GIT_REPO_URL = "https://gitlab.com/username/repository.git"
# GIT_REPO_BRANCH = "main"
# # HTTP token for authentication
# GIT_REPO_TOKEN = "gitlab-api-token"
```

What each line means:

| Key | Required | What to put there |
| --- | --- | --- |
| `SYNC_INTERVAL_SECONDS` | No | Seconds between two backups, 1 to 86400. Default 300. |
| `[assets.private]` | One of the two | Your own repository. |
| `[assets.common]` | One of the two | A repository shared between users. Remove the `#` in front of its lines to use it. |
| `GIT_REPO_URL` | Yes | The repository's `https://` address, as shown under *Clone* on GitLab. |
| `GIT_REPO_BRANCH` | Yes | The branch to back up, usually `main`. |
| `GIT_REPO_TOKEN` | Yes | The access token from step 1. |

Things to know:

- Every text value is quoted, because the file is TOML.
- The example values (`username/repository.git`, `gitlab-api-token`) are
  refused. You have to replace them, so nothing is ever sent to the
  example address.
- The file holds your token. Keep it readable only by you, and never put
  it inside one of the backed-up folders.

### Run it locally

You need Python 3.12 or newer, git, and [Poetry](https://python-poetry.org/).

#### Step 1: Install the service

```bash
cd workspaces/src/admin
poetry install
```

#### Step 2: Create the directory

The directory is `.config/workspace-admin` in your home directory.

Windows PowerShell:

```powershell
New-Item -ItemType Directory -Force "$HOME\.config\workspace-admin"
```

Linux or macOS:

```bash
mkdir -p ~/.config/workspace-admin
```

#### Step 3: Create the config file

Create an empty `config.toml` in that directory and open it.

Windows PowerShell:

```powershell
New-Item -ItemType File "$HOME\.config\workspace-admin\config.toml"
notepad "$HOME\.config\workspace-admin\config.toml"
```

Linux or macOS:

```bash
touch ~/.config/workspace-admin/config.toml
chmod 600 ~/.config/workspace-admin/config.toml
nano ~/.config/workspace-admin/config.toml
```

#### Step 4: Paste the example and fill it in

Paste the [example](#2-understand-the-config-file) into the file, then
replace `GIT_REPO_URL`, `GIT_REPO_BRANCH` and `GIT_REPO_TOKEN` with your
own values. Save the file.

#### Step 5: Start the service

```bash
poetry run workspace-admin
```

The first log line shows which file it read. It must be the file you just
made:

```text
INFO:     Git backup config file: C:\Users\<name>\.config\workspace-admin\config.toml
```

Cloning happens once, on startup. The repositories are cloned into
`assets/<name>` in your home directory. After a change to the config file,
stop the service with `Ctrl+C` and start it again.

#### Step 6: Check that it runs

See [Checking the backup](#checking-the-backup).

#### Using another directory

To keep the config file and the repositories somewhere other than your
home directory, set `PERSISTENT_DIR` before every start. The config file
then belongs in `$PERSISTENT_DIR/.config/workspace-admin/config.toml`:

```powershell
$env:PERSISTENT_DIR = "C:\my-workspace"
poetry run workspace-admin
```

### Run it in the workspace container

The service starts by itself with the container. These steps use the
compose files in `workspaces/test/dtaas`, which mount
`files/<username>` on the host as `/workspace` in the container.

1. **Start the container once.**

   ```bash
   cd workspaces/test/dtaas
   docker compose up -d
   ```

   The service finds no config file and creates one from the template, then
   waits. The log says:

   ```text
   WARNING:  Created /workspace/.config/workspace-admin/config.toml; fill in your repositories and restart the workspace
   ```

2. **Fill in the config file.** Edit it from the host, at
   `files/<username>/.config/workspace-admin/config.toml`, or from inside
   the workspace. The `.config` folder is hidden, so show hidden files to
   see it.

3. **Restart the container.**

   ```bash
   docker compose restart workspace
   ```

4. **Check that it runs.** See [Checking the backup](#checking-the-backup).
   The repositories' files appear in `/workspace/assets/<name>`.

### Checking the backup

The log shows each step. On the first start:

```text
INFO:     Git backup config file: /workspace/.config/workspace-admin/config.toml
INFO:     Cloned repository 'private' (https://gitlab.com/..., branch main) into /workspace/assets/private
INFO:     Started git sync for 1 repository/repositories every 300 seconds
```

On later starts `Cloned repository` is replaced by
`already cloned ...; skipping`. Then, every interval, each repository is
committed, pulled and pushed:

```text
INFO:     Committed changes in repository 'private'
INFO:     Pushed 1 commit(s) from repository 'private' to branch main
```

The outcome of the last backups is also served as JSON, under the path
prefix (`dtaas-user` unless `--path-prefix` says otherwise):

```bash
curl http://localhost:8091/dtaas-user/storage/status
```

In the container, go through nginx instead: `http://localhost/<username>/storage/status`
with the bundled compose files.

If nothing happens, the log says why:

| Log line | Meaning |
| --- | --- |
| `... config.toml not found; git backup is disabled` | No file at the logged path. Check the path in the first log line. |
| `... placeholder ...` | The file still holds a value from the template. |
| `Cannot clone git assets: ...` | The file is not valid; the rest of the line names the problem. |
| `... GIT_REPO_TOKEN was rejected ...` | The remote refused the token. |

If one repository fails to clone, the other is still cloned and the
service starts anyway. See
[Cloning the Git Assets](DOCUMENTATION.md#cloning-the-git-assets) for the
full process and what each error means.

## Running

### As a Service (in workspace container)

The service is automatically started when the workspace container starts.
It runs on port 8091 and is accessible via the nginx reverse proxy at `/services`.

### As a CLI Utility

```bash
poetry install
poetry run workspace-admin --path-prefix dtaas-user
```

`poetry run workspace-admin --help` lists every flag. See
[DOCUMENTATION.md](DOCUMENTATION.md#running-locally) for worked examples.

## Development

```bash
poetry install
poetry run pytest --cov=admin --cov-report=term-missing
poetry run pylint src/admin tests
```

See [DOCUMENTATION.md](DOCUMENTATION.md#development) for the full workflow,
including coverage reports and the checks run in CI.
