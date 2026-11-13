"""
Git backup of the workspace directories.

- :mod:`admin.git.setup`: finding, creating and reading ``config.toml``
- :mod:`admin.git.run`: running git, credentials and error kinds
- :mod:`admin.git.repo`: cloning a repository and the backup cycle
- :mod:`admin.git.service`: starting, scheduling and reporting the backup

``service`` uses ``repo``, which uses ``run`` and ``setup``. Those two only
share their bottom modules: ``setup.config`` and ``run.runner`` import
nothing from the package.
"""
