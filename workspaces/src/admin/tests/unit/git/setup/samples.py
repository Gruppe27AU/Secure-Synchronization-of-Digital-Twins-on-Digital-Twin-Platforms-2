"""Config files shared by the tests of admin.git.setup."""

from pathlib import Path

#: Root passed to every load_config() call, as $PERSISTENT_DIR in the image.
ROOT = Path("/workspace")


VALID_CONFIG = """\
[assets.private]
GIT_REPO_URL = "https://gitlab.com/user/private.git"
GIT_REPO_BRANCH = "main"
GIT_REPO_TOKEN = "private-token"

[assets.common]
GIT_REPO_URL = "https://gitlab.com/user/common.git"
GIT_REPO_BRANCH = "develop"
GIT_REPO_TOKEN = "common-token"
"""


PRIVATE_ONLY_CONFIG = """\
[assets.private]
GIT_REPO_URL = "https://gitlab.com/user/private.git"
GIT_REPO_BRANCH = "main"
GIT_REPO_TOKEN = "private-token"
"""


def write_config(tmp_path: Path, content: str) -> Path:
    """Write ``content`` to a config file and return its path."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(content, encoding="utf-8")
    return config_path
