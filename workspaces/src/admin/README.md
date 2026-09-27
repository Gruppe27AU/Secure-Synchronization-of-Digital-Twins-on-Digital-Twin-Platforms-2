# Admin Service

FastAPI service for workspace service discovery and management.

## Features

- `/services` endpoint - Returns JSON list of available workspace services
- Path prefix support for multi-user deployments
- Command-line interface for standalone operation
- Periodic git backup - commits, pulls and pushes the workspace's git assets
  every five minutes, so users never run a git command themselves
- Conflict resolution that keeps the workspace's own version of a file when
  it and the remote have both changed
- Git assets configured in one TOML file, `config.env`, which is validated
  on startup so a mistake in it is reported by name instead of failing
  later inside git

## Layout

```text
src/admin/
├── api.py                        # FastAPI app factory and HTTP routes
├── services.py                   # Workspace service discovery
├── main.py                       # Command-line entry point
├── config/
│   ├── services_template.json    # Service catalogue
│   └── config.env.example        # Example git asset configuration
└── git/                          # Git backup of workspace directories
    ├── config.py                 # config.env -> RepoConfig
    ├── auth.py                   # Shared HTTP auth header and failure detection
    ├── clone.py                  # Clone a single RepoConfig
    ├── sync.py                   # Commit, pull and push one working tree
    ├── scheduler.py              # Run the sync on a timer, in the background
    └── bootstrap.py              # Wire the above together on startup
```

See [DOCUMENTATION.md](DOCUMENTATION.md) for the full architecture and
endpoint reference, and
[DOCUMENTATION.md#configuration](DOCUMENTATION.md#configuration) for every
key `config.env` accepts.

## Cloning the Private and Common Repositories

On startup the service clones the repositories listed in `config.env`:
`private` for the user's own files and `common` for files shared between
users. Either can be left out. To set them up:

1. **Prepare the remotes.** Each repository needs at least one commit on the
   branch you configure (on GitLab, tick *Initialize repository with a
   README* when creating the project). Create an access token with
   `read_repository` and `write_repository`.

2. **Write `config.env`.** Copy
   [`src/admin/config/config.env.example`](src/admin/config/config.env.example)
   and fill in your values. The file is TOML, so every value is a quoted
   string:

   ```toml
   HOME_DIR = "/home/username"
   WORKSPACE_DIR = "/workspace"
   WORKSPACE_APP_DIR = ".workspace"

   [assets.private]
   GIT_REPO_URL = "https://gitlab.com/username/private-repo.git"
   GIT_REPO_BRANCH = "main"
   GIT_REPO_USERNAME = "gitlab-username"
   GIT_REPO_TOKEN = "gitlab-access-token"
   GIT_DIR = "private"
   GIT_WORK_TREE = "assets/private"

   [assets.common]
   GIT_REPO_URL = "https://gitlab.com/username/common-repo.git"
   GIT_REPO_BRANCH = "main"
   GIT_REPO_USERNAME = "gitlab-username"
   GIT_REPO_TOKEN = "gitlab-access-token"
   GIT_DIR = "common"
   GIT_WORK_TREE = "assets/common"
   ```

   Leave out the `[assets.common]` or `[assets.private]` section if you do
   not need it.

3. **Put the file where the service looks.** The service reads
   `$WORKSPACE_APP_DIR/config.env`, using the environment variable
   `WORKSPACE_APP_DIR`, or `config.env` in the directory it was started
   from when the variable is not set:

   ```bash
   export WORKSPACE_APP_DIR=/path/to/dir/holding/config.env
   ```

4. **Start the service.** Cloning happens once, on startup:

   ```bash
   poetry run workspace-admin
   ```

   In the workspace container, the service starts with the container, so
   restart the container instead.

5. **Check the result.** The log shows one line per repository:

   ```text
   INFO admin.git.clone Cloned repository 'private' (https://gitlab.com/..., branch main) into /home/username/.workspace/assets/private
   INFO admin.git.clone Cloned repository 'common' (https://gitlab.com/..., branch main) into /home/username/.workspace/assets/common
   ```

   With the example values, each repository's files are in
   `/home/username/.workspace/assets/<name>` and its git data is in
   `/workspace/<name>`.

On later starts the log says `already cloned ...; skipping` instead, and
nothing is cloned again. If a clone fails, the reason is logged, the other
repository is still cloned, and the service starts anyway. See
[Cloning the Git Assets](DOCUMENTATION.md#cloning-the-git-assets) for the
full process, the requirements, and what each error means.

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
