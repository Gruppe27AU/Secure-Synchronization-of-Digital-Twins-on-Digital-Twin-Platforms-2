# Workspace Admin User Guide

This guide is meant for the user installing and using the workspace.
The guide shows the following:

- How to start
- How to set up the backup system
- What to look for if backup fails

Development documentation can be found here:
[developer documentation](../workspaces/src/admin/DOCUMENTATION.md).

## What is workspace-admin?

Workspace-admin is a service inside every workspace. The service lists the
tools in the workspace, and backs up your files to a git repository of your
choice on GitHub or GitLab.

### What the workspace-admin service can do right now

The service can:

- Back up a repository of your choice
- Save your changes either every 5 minutes (default) or in intervals of
  your choice
- Back up remote when a merge conflict is detected

The service cannot:

- Automatically resolve merge conflicts
- Use anything other than https with a token
- Back up files outside of /workspace/assets
- Pick up changes to the config file without a restart

## Requirements

The user needs the following to have working before installing the service:

- Docker version 27 or newer, with Docker Compose
- Git
- An account on GitHub or GitLab, with a repository for your files
- An access token to the repository with read and write permissions
- Port 80 must be free

## How to Install

### 1. Get the code

Download the code:

```bash
git clone https://github.com/INTO-CPS-Association/workspace.git
```

and go to the folder with the compose file:

```bash
cd workspace/workspaces/test/dtaas
```

### 2. Get the workspace image

Build the image. This can take a while:

```bash
docker compose build
```

### 3. Your user folder

The folder `files/<username>` on your computer and the folder `/workspace`
inside the workspace are the same folder. A file you save in `/workspace`
appears in `files/<username>`, and the other way round. Because the files
are on your computer, they are kept there when the workspace is stopped or
rebuilt.

The default username is `user1`.

On Linux, remember to give the workspace access to the folder:

```bash
sudo chown -R 1000:100 files
```

For several users, see [TRAEFIK.md](../workspaces/test/dtaas/TRAEFIK.md).

## How to Start

All commands are run from the folder with the compose file. If you open a
new terminal, go there first:

```bash
cd workspace/workspaces/test/dtaas
```

You are in the right folder when `compose.yml` is listed by `ls` (or `dir`
on Windows).

Start the workspace:

```bash
docker compose up -d
```

If you skipped [step 2](#2-get-the-workspace-image), the image will start
building first.

When the command is done, you can navigate to your browser and type one of
the following addresses:

- Jupyter Lab: <http://localhost/user1/lab>
- VS Code: <http://localhost/user1/tools/vscode>
- Desktop: <http://localhost/user1/tools/vnc>

The backup does not run the first time you start the `workspace-admin`
service. It creates the config file and waits for you to fill it in.

To stop the workspace type:

```bash
docker compose down
```

## How to Set Up the Backup

### 1. Make a repository

If you have a repository, or are given access to one you can skip this part.

Make a repository on GitHub or GitLab for your files. It can be empty, or
hold files you want in the workspace.

### 2. Make a token

Make an access token for the repository. The token must be able to read and
write the repository. See how on
[GitHub](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)
or [GitLab](https://docs.gitlab.com/user/project/settings/project_access_tokens/).
On GitLab, choose the role Maintainer, if you wish for backup to work on a
protected branch.

Copy the token. You need it in the next step.

### 3. Fill in the config file

The config file is created the first time the workspace starts. The
workspace log shows where:

```bash
docker compose logs workspace
```

This is an example of how it would look:
`/workspace/.config/workspace-admin/config.toml`. You can open the file
using the VS Code tool:

1. Open VS Code: <http://localhost/user1/tools/vscode>
2. Choose File → Open File.
3. Type `/workspace/.config/workspace-admin/config.toml` and press Enter.

On Linux and macOS the folder `.config` is hidden on your computer, so you
may need to show hidden files to see it there.

Fill in the section `[assets.private]`:

```toml
[assets.private]
GIT_REPO_URL = "https://github.com/<your-name>/<your-repository>.git"
GIT_REPO_BRANCH = "main"
GIT_REPO_TOKEN = "<your token>"
```

The URL must start with `https://`. The file holds your token, so do not
share it. The config file is never backed up as long as it stays in this
folder. Do not move it into `/workspace/assets`.

### 4. Restart the workspace

Since the workspace only reads the config file when it starts, you need to
restart the service each time you save a change in the file.

```bash
docker compose restart
```

Your repository now should appear in `/workspace/assets/private`. Only files
in this folder are backed up.

## How to Check That the Backup Works

Open <http://localhost/user1/storage/status>. The backup works when
`state` is `running`, and the time in `last_success` moves forward every
5 minutes by default. Your commits also appear in the repository on GitHub
or GitLab.

If the backup does not work, the log tells why:

```bash
docker compose logs workspace
```

The page looks like this when the backup works:

```json
{
  "state": "running",
  "enabled": true,
  "repositories": {
    "private": {
      "state": "syncing",
      "last_attempt": "2026-11-19T17:12:23+00:00",
      "last_success": "2026-11-19T17:12:23+00:00",
      "last_error": null,
      "last_remote_overwrite": null
    }
  }
}
```

The `state` at the top is the backup as a whole. Each repository under
`repositories` has a `state` of its own:

| `repositories.<name>.state` | Meaning |
| --- | --- |
| `cloning` | The repository is being fetched the first time. |
| `syncing` | The repository is backed up on every interval. |
| `clone_failed` | The repository could not be fetched the first time. The reason is in `last_error.kind`. |

## When the Backup Needs You

If something is wrong look for the state at:
<http://localhost/user1/storage/status>

The reason is in `last_error.kind` under your repository, for example
`repositories.private.last_error.kind`.

The last error stays on the page after the backup works again. If
`last_success` is shown later than `last_error.at`, it is solved and working again. 

What each kind means:

| Kind | What to do |
| --- | --- |
| `auth_failed` | The token is wrong or expired. Put a new token in the config file and restart. |
| `network`, `timeout` | GitHub or GitLab cannot be reached. The backup tries again by itself. |
| `remote_changed` | Someone pushed to the repository while the backup was solving a conflict. The backup tries again by itself. |
| `overwrite_refused` | The repository does not allow force-pushes to your branch, so a conflict could not be solved. Allow force-pushes to the branch, or open a terminal in `/workspace/assets/private`, run `git merge origin/<your branch>`, fix the files and commit. |
| `pull_failed` | The branch in the repository cannot be fetched, for example because it was renamed or its history was rewritten. Check `GIT_REPO_BRANCH` and the branch on GitHub or GitLab. |
| `file_too_large` | A file is larger than 50 MiB and was left out. The rest was backed up. Make the file smaller or move it out of `/workspace/assets`. |
| `conflict_markers` | A file holds conflict markers (`<<<<<<<`) and was left out. The rest was backed up. Remove the markers and save the file. |
| `mass_deletion` | Most files in the folder are missing, so the backup stops to keep them safe in the repository. Put the files back, or delete them in smaller steps. |
| `wrong_branch`, `detached_head` | Another branch is checked out in a terminal. Run `git checkout <your branch>`. |
| `operation_in_progress`, `unresolved_conflicts` | A merge or rebase you started yourself is not finished. Fix the files, then `git add` and `git commit`, or stop it with `git merge --abort`. |
| `index_locked` | Another git command was running. The backup tries again by itself. |
| `local_changes` | Only for `common`: you changed a file that the repository changed too. Undo your change with `git checkout -- <file>`. |
| `git_failed`, `unexpected` | Something else went wrong. The log tells what. |

While the status shows `overwrite_refused` or `pull_failed`, your changes
are saved in the workspace, but not on GitHub or GitLab.

### When a file is changed locally and on remote

You do not need to do anything. Your version wins: the backup replaces the
branch in the repository with yours. The repository's version is saved
first, on a different branch, example:
`remote-backup/2026-11-19_10-15-30-UTC-a1b2c3d`.
The log names the branch, and the status shows the time in
`last_remote_overwrite`.

To get a file back from that branch, open a terminal in
`/workspace/assets/private` and run:

```bash
git fetch origin remote-backup/<time>-<id>
git checkout FETCH_HEAD -- notes.md
```

The next backup saves it like any other change.

Git asks you to sign in, because the token in the config file is only
used by the backup. It is not saved in the repository, so other programs
in the workspace cannot read it. Type your username on GitHub or GitLab,
and paste the token as the password. The token is not shown while you
paste it.

In the VS Code terminal the question comes as a popup that offers to sign
in with GitHub. This may not work inside the workspace. Close the popup,
and let git ask in the terminal instead:

```bash
GIT_ASKPASS= git fetch origin remote-backup/<time>-<id>
```

## Config File Reference

| Key | Meaning |
| --- | --- |
| `SYNC_INTERVAL_SECONDS` | Seconds between two backups, 1 to 86400. Default 300. Put it above the sections. |
| `GIT_REPO_URL` | The https address of the repository. |
| `GIT_REPO_BRANCH` | The branch to back up to. |
| `GIT_REPO_TOKEN` | The access token. |
| `MODE` | `"sync"` backs up and fetches (default for `private`). `"pull"` only fetches (default for `common`). |

The section `[assets.common]` receives files shared between users, in
`/workspace/assets/common`. A read-only token is enough for it.
