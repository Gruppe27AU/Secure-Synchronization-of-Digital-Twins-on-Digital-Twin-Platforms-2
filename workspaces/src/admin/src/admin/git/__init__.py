"""
Git backup of the workspace directories.

This package is the home for the git logic of the workspace admin service:
reading the git configuration, cloning the configured repositories,
authenticating against the remotes and keeping the working trees in sync.

- :mod:`admin.git.config` reads ``config.env`` into :class:`RepoConfig`
  objects, one per ``[assets.<name>]`` table.
- :mod:`admin.git.clone` clones a single :class:`RepoConfig`, with the git
  directory and working tree kept separate (``git clone
  --separate-git-dir``).
- :mod:`admin.git.bootstrap` wires the two together for the shared
  ``common`` asset and is called once on service startup
  (:func:`admin.main.cli`).
"""
