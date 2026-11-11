"""Utility module for managing package installations from a lock file.

Provides dependency types (bin-tar, pip-venv, apt) that read
source and version information from a JSON lock file and handle
downloading, verifying, and installing packages.
"""
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import venv
from pathlib import Path
from urllib.parse import urlparse

# Determines the lock file formatting that this util works with.
LOCK_VERSION = "0.4.0"

# Default location of the lock file.
DEFAULT_DEPENDENCY_LOCK_FILE = os.path.join(
    os.environ.get("INST_DIR", os.getcwd()),
    "dependencies.lock"
)

# Placement and name of the lock file.
DEPENDENCY_LOCK_FILE = os.environ.get(
    "DEPENDENCY_LOCK_FILE",
    DEFAULT_DEPENDENCY_LOCK_FILE
)

ARCHITECTURE = os.environ.get(
    "TARGETARCH", os.uname().machine
).lower()

# Default directory for downloaded files
DEFAULT_FETCH_DIRECTORY = "/tmp"

# Default directory for installation of packages
DEFAULT_INSTALLATION_DIRECTORY = "/opt"

# Default directory for binaries to be linked to (should be in PATH)
DEFAULT_LINK_DIRECTORY = "/usr/local/bin"


def lock_file_location() -> Path:
    """Returns the location of the lock file as a Path object.

    Raises a FileNotFoundError if the lock file doesn't exist.

    :return: Path object to the lock file.
    :rtype: Path
    """
    location = Path(DEPENDENCY_LOCK_FILE).resolve()
    if location.exists():
        return location
    raise FileNotFoundError(
        f"Lock file not present at '{location}'"
    )


def read_dependency_file() -> dict:
    """Reads the dependency file and returns its contents.

    :return: Dictionary containing the dependency information.
    :rtype: dict
    """
    with open(
        lock_file_location(), encoding="utf-8"
    ) as lock_file:
        return json.load(lock_file)


def check_version(lock_dict: dict):
    """Checks if the lock file version matches expected version.

    Raises an AssertionError if the versions do not match.

    :param lock_dict: Dictionary with the lock file contents.
    :type lock_dict: dict
    """
    lock_ver = lock_dict["lockVersion"]
    assert lock_ver == LOCK_VERSION, (
        f"Lock file uses version {lock_ver} formatting, "
        f"installation util expects version {LOCK_VERSION}"
    )


def get_dependencies_for_package(package_name: str, **kwargs):
    """Factory that returns the right Dependency for a package.

    :param package_name: Name of the package to look up.
    :type package_name: str
    :param kwargs: Extra keyword arguments for the constructor.
    :return: A Dependency subclass instance.
    :rtype: Dependency
    """
    lock_contents = read_dependency_file()
    check_version(lock_contents)

    try:
        packages = lock_contents["packages"]
    except Exception as e:
        raise KeyError(
            "No 'packages' found in dependencies"
        ) from e

    try:
        dependencies = packages[package_name]
    except Exception as e:
        raise KeyError(
            f"Package '{package_name}' not found in dependencies"
        ) from e

    try:
        dependency_type = dependencies["type"]
    except Exception as e:
        raise KeyError(
            f"The dependency type for package '{package_name}' "
            "isn't specified"
        ) from e

    match dependency_type:
        case "pip-venv":
            return PipVenvDependency(
                package_name, dependencies, **kwargs
            )
        case "bin-tar":
            return BinTarDependency(
                package_name, dependencies, **kwargs
            )
        case "apt":
            return AptDependency(
                package_name, dependencies, **kwargs
            )
        case "npm":
            return NpmDependency(
                package_name, dependencies, **kwargs
            )
        case _:
            raise ValueError(
                f"Unknown dependency type '{dependency_type}' "
                f"for package '{package_name}'"
            )


class Dependency:  # pylint: disable=too-few-public-methods
    """An abstract base class representing a generic dependency.

    Subclasses should implement the default_installation method.
    """
    def __init__(
        self,
        package_name: str,
        package_dict: dict
    ):
        self.package_name = package_name
        self.package_dict = package_dict

    def default_installation(self):
        """Perform the default installation of the dependency.

        Subclasses must implement this method.
        """
        raise NotImplementedError(
            "This method should be implemented by subclasses."
        )


class BinTarDependency(Dependency):
    """A dependency on a tarball containing binaries.

    Running the default_installation method will fetch the
    tarball, check its integrity, extract it, move the contents
    to the installation directory, and add the binaries to the
    system PATH.
    """
    # pylint: disable=too-many-instance-attributes
    # pylint: disable=too-many-arguments
    # pylint: disable=too-many-positional-arguments
    def __init__(
        self,
        package_name: str,
        package_dict: dict,
        fetch_directory: str = DEFAULT_FETCH_DIRECTORY,
        installation_directory: str = DEFAULT_INSTALLATION_DIRECTORY,
        binaries_location_in_tarball: str = "bin",
        installed_package_name: str = None,
        system_bin_directory: str = DEFAULT_LINK_DIRECTORY
    ):
        super().__init__(package_name, package_dict)
        self.installation_location = None
        match ARCHITECTURE:
            case "amd64" | "x86_64" | "x64":
                self.source = package_dict["amd64"]["source"]
                self.checksum = (
                    package_dict["amd64"]["checksum"]
                )
            case "arm64" | "aarch64":
                self.source = package_dict["arm64"]["source"]
                self.checksum = (
                    package_dict["arm64"]["checksum"]
                )
            case _:
                raise ValueError(
                    "Script running on unsupported "
                    f"architecture '{ARCHITECTURE}'"
                )
        compression = package_dict["compression"].lower()
        match compression:
            case "gz" | "gzip" | "lz77":
                self.compression = "gz"
            case "xz" | "lzma" | "lzma2":
                self.compression = "xz"
            case _:
                raise ValueError(
                    f"Unknown compression type '{compression}' "
                    f"for package '{package_name}'"
                )
        tar_name = (
            f"{self.package_name}.tar.{self.compression}"
        )
        self.fetch_spot = (
            Path(fetch_directory) / tar_name
        )
        self.unfolding_directory = (
            Path(fetch_directory) / self.package_name
        )
        self.installation_directory = Path(
            installation_directory
        )
        self.binaries_location_in_tarball = (
            binaries_location_in_tarball
        )
        self.installed_package_name = installed_package_name
        self.system_bin_directory = Path(system_bin_directory)

    def set_fetch_directory(self, fetch_directory: str):
        """Sets the directory for tarball fetching.

        :param fetch_directory: Directory to fetch to.
        :type fetch_directory: str
        """
        tar_name = (
            f"{self.package_name}.tar.{self.compression}"
        )
        self.fetch_spot = (
            Path(fetch_directory) / tar_name
        )
        self.unfolding_directory = (
            Path(fetch_directory) / self.package_name
        )

    def set_installation_directory(
        self, installation_directory: str
    ):
        """Sets the directory for package installation.

        :param installation_directory: Directory to install to.
        :type installation_directory: str
        """
        self.installation_directory = Path(
            installation_directory
        )

    def set_binaries_location_in_tarball(
        self, binaries_location_in_tarball: str
    ):
        """Sets the binaries subdirectory in the tarball.

        :param binaries_location_in_tarball: Subdirectory name.
        :type binaries_location_in_tarball: str
        """
        self.binaries_location_in_tarball = (
            binaries_location_in_tarball
        )

    def set_installed_package_name(
        self, installed_package_name: str
    ):
        """Sets the name of the package once installed.

        :param installed_package_name: Installed package name.
        :type installed_package_name: str
        """
        self.installed_package_name = installed_package_name

    def set_system_bin_directory(
        self, system_bin_directory: str
    ):
        """Sets the directory for binary symlinks.

        :param system_bin_directory: Symlink directory.
        :type system_bin_directory: str
        """
        self.system_bin_directory = Path(system_bin_directory)

    def default_installation(self):
        """Runs the default BinTarDependency installation.

        Fetches the tarball, checks its integrity, extracts it,
        moves the contents to the installation directory, and
        adds the binaries to the system PATH.
        """
        self.fetch_tarball()
        self.check_integrity_of_fetched_tarball()
        self.extract_tarball()
        self.move_unfolded_contents_to_installation()
        self.add_to_path()

    def fetch_tarball(self):
        """Fetches the tarball from the source URL.

        Raises a CalledProcessError if the curl command fails.
        """
        subprocess.run(
            ["curl", "-fsSL", self.source, "-o",
             self.fetch_spot],
            check=True
        )

    def check_integrity_of_fetched_tarball(self):
        """Checks the tarball integrity against its checksum.

        Raises a ValueError if the checksum doesn't match.
        """
        if not self.fetch_spot.exists():
            raise FileNotFoundError(
                f"File '{self.fetch_spot}' not found; "
                f"package '{self.package_name}' hasn't been "
                "downloaded."
            )
        with open(self.fetch_spot, "rb") as f:
            file_hash = hashlib.file_digest(
                f, "sha256"
            ).hexdigest()
            if file_hash != self.checksum:
                raise ValueError(
                    f"Checksum mismatch for package "
                    f"'{self.package_name}' - was "
                    f"'{file_hash}', expected "
                    f"'{self.checksum}'"
                )

    def extract_tarball(self):
        """Extracts the tarball to the unfolding directory.

        Raises a FileNotFoundError if the tarball is not found.
        """
        if not self.fetch_spot.exists():
            raise FileNotFoundError(
                f"File '{self.fetch_spot}' not found; "
                f"package '{self.package_name}' hasn't been "
                "downloaded."
            )
        with tarfile.open(
            self.fetch_spot, f"r:{self.compression}"
        ) as tar:
            tar.extractall(
                path=self.unfolding_directory, filter="data"
            )

    def move_unfolded_contents_to_installation(self):
        """Moves extracted contents to the installation directory.

        Raises a FileNotFoundError if not yet extracted.
        """
        if not self.unfolding_directory.exists():
            raise FileNotFoundError(
                f"Directory '{self.unfolding_directory}' not "
                f"found; package '{self.package_name}' hasn't "
                "been extracted."
            )

        os.makedirs(
            self.installation_directory, exist_ok=True
        )
        unfolded_contents = list(
            self.unfolding_directory.iterdir()
        )
        if (
            len(unfolded_contents) == 1
            and unfolded_contents[0].is_dir()
        ):
            single_dir = unfolded_contents[0]
            if self.installed_package_name:
                self.installation_location = (
                    self.installation_directory /
                    self.installed_package_name
                )
            else:
                self.installation_location = (
                    self.installation_directory /
                    single_dir.name
                )
            single_dir.rename(self.installation_location)
        else:
            extracted = [
                item.name for item in unfolded_contents
            ]
            raise ValueError(
                f"Package '{self.package_name}' extracted "
                f"multiple items unexpectedly: {extracted}."
            )

    def add_to_path(self):
        """Adds binaries to PATH by creating symlinks."""
        if self.installation_location is None:
            raise AttributeError(
                f"Installation location for package "
                f"'{self.package_name}' hasn't been set. "
                "Ensure "
                "'move_unfolded_contents_to_installation'"
                " has been called successfully."
            )
        bin_subdir = self.binaries_location_in_tarball
        binaries_path = (
            self.installation_location / bin_subdir
        )
        if not binaries_path.exists():
            raise FileNotFoundError(
                f"Binaries path '{binaries_path}' not found; "
                f"package '{self.package_name}' may not have "
                "been installed correctly."
            )

        os.makedirs(self.system_bin_directory, exist_ok=True)

        for item in binaries_path.iterdir():
            if not (item.is_file() or item.is_symlink()):
                continue
            link = self.system_bin_directory / item.name
            if link.exists() or link.is_symlink():
                link.unlink()
            os.symlink(item, link)


class PipVenvDependency(Dependency):
    """A dependency requiring a Python virtual environment.

    Running the default_installation method will create a virtual
    environment, install the specified Python packages, and link
    the resulting binaries to a specified directory.
    """
    # pylint: disable=too-many-arguments
    # pylint: disable=too-many-positional-arguments
    def __init__(
        self,
        package_name: str,
        package_dict: dict,
        resulting_binaries: list[str] = None,
        venv_super_directory: str = DEFAULT_INSTALLATION_DIRECTORY,
        installation_directory: str = DEFAULT_LINK_DIRECTORY
    ):
        super().__init__(package_name, package_dict)
        try:
            self.python_packages = package_dict["pyPackages"]
        except Exception as e:
            raise KeyError(
                f"Package '{package_name}' has dependency type "
                "'pip-venv' and therefore should include one or "
                "more python package definitions in 'pyPackages'"
            ) from e
        self.resulting_binaries = (
            resulting_binaries or [package_name]
        )
        self.venv_path = Path(
            venv_super_directory
        ) / f"{package_name}_env"
        self.installation_directory = installation_directory

    def set_resulting_binaries(
        self, resulting_binaries: list[str]
    ):
        """Sets the binaries to be linked after installation.

        :param resulting_binaries: List of binary names.
        :type resulting_binaries: list[str]
        """
        self.resulting_binaries = resulting_binaries

    def set_venv_super_directory(
        self, venv_super_directory: str
    ):
        """Sets the parent directory for the virtual environment.

        :param venv_super_directory: Parent directory path.
        :type venv_super_directory: str
        """
        self.venv_path = Path(
            venv_super_directory
        ) / f"{self.package_name}_env"

    def set_installation_directory(
        self, installation_directory: str
    ):
        """Sets the directory for binary symlinks.

        :param installation_directory: Symlink directory.
        :type installation_directory: str
        """
        self.installation_directory = installation_directory

    def default_installation(self):
        """Runs the default PipVenvDependency installation.

        Creates the virtual environment, installs the specified
        Python packages, and links resulting binaries.
        """
        self.create_venv()
        self.install_python_packages()
        self.link_binaries()

    def create_venv(self):
        """Creates a virtual environment at venv_path."""
        venv.create(self.venv_path, with_pip=True)

    def _package_to_pip_format(
        self, py_package_name: str, py_package_dict: dict
    ) -> str:
        """Converts a package name and dict to pip format.

        :param py_package_name: The Python package name.
        :type py_package_name: str
        :param py_package_dict: Dict with package details.
        :type py_package_dict: dict
        :return: 'name==version' or just 'name'.
        :rtype: str
        """
        try:
            return (
                f"{py_package_name}=="
                f"{py_package_dict['version']}"
            )
        except KeyError:
            return f"{py_package_name}"

    def install_python_packages(self):
        """Installs Python packages into the virtual environment.

        Raises FileNotFoundError if the venv doesn't exist.
        """
        if not self.venv_path.exists():
            raise FileNotFoundError(
                "Virtual environment hasn't been created."
            )

        pip_bin = os.path.join(
            self.venv_path, "bin", "pip"
        )

        run_command = [pip_bin, "install", "--no-cache-dir"]
        run_command.extend([
            self._package_to_pip_format(
                package_name, package_dict
            )
            for package_name, package_dict
            in self.python_packages.items()
        ])

        subprocess.run(run_command, check=True)

    def link_binaries(self):
        """Creates symlinks for binaries from the venv."""
        os.makedirs(
            self.installation_directory, exist_ok=True
        )

        for resulting_binary in self.resulting_binaries:
            package_binary = (
                Path(self.venv_path) / "bin" /
                resulting_binary
            )

            if not package_binary.exists():
                raise FileNotFoundError(
                    f"Binary '{resulting_binary}' hasn't been "
                    "installed in the virtual environment."
                )

            link = Path(
                self.installation_directory
            ) / resulting_binary
            if link.exists() or link.is_symlink():
                link.unlink()
            os.symlink(package_binary, link)


class AptDependency(Dependency):
    """A dependency installed via the apt package manager.
    """

    def __init__(
        self,
        package_name: str,
        package_dict: dict,
        apt_package_name: str = None
    ):
        super().__init__(package_name, package_dict)
        self.apt_package_name = apt_package_name or package_name
        self.version = package_dict.get("version")
        self.gpg_key_url = package_dict.get("gpgKeyUrl")
        self.repository = package_dict.get("repository")
        self.ppa = package_dict.get("ppa")
        self.additional_packages = package_dict.get(
            "additionalPackages", []
        )
        self._keyring_path = None

    def set_apt_package_name(self, apt_package_name: str):
        """Sets the apt package name.

        :param apt_package_name: The apt package name.
        :type apt_package_name: str
        """
        self.apt_package_name = apt_package_name

    def _apt_env(self) -> dict:
        """Returns env with DEBIAN_FRONTEND=noninteractive."""
        return {
            **os.environ,
            "DEBIAN_FRONTEND": "noninteractive"
        }

    def _run_apt(self, *args):
        """Runs an apt-get command."""
        subprocess.run(
            ["apt-get", *args],
            check=True,
            env=self._apt_env()
        )

    def add_gpg_key(self, key_url: str = None):
        """Downloads and installs a GPG key for apt.

        :param key_url: URL of the GPG key.
        :type key_url: str
        """
        key_url = key_url or self.gpg_key_url
        if not key_url:
            raise ValueError(
                f"No GPG key URL for package "
                f"'{self.package_name}'. Provide one as "
                "argument or set gpgKeyUrl in the lock file."
            )
        keyring = (
            f"/usr/share/keyrings/"
            f"{self.apt_package_name}-archive-keyring.gpg"
        )
        curl = subprocess.run(
            ["curl", "-fsSL", key_url],
            capture_output=True, check=True
        )
        with open(keyring, "wb") as f:
            subprocess.run(
                ["gpg", "--dearmor"],
                input=curl.stdout, stdout=f, check=True
            )
        self._keyring_path = keyring

    def add_repository(
        self,
        repo_url: str = None,
        component: str = None,
        pin_priority: int = 900
    ):
        """Adds a custom apt repository and pins it.

        :param repo_url: Base URL of the apt repository.
        :type repo_url: str
        :param component: Repository component name.
        :type component: str
        :param pin_priority: Pin priority (default 900).
        :type pin_priority: int
        """
        repo_url = repo_url or self.repository
        if not repo_url:
            raise ValueError(
                f"No repository URL for package "
                f"'{self.package_name}'. Provide one as "
                "argument or set repository in the lock file."
            )
        arch = subprocess.run(
            ["dpkg", "--print-architecture"],
            capture_output=True, text=True, check=True
        ).stdout.strip()
        codename = subprocess.run(
            ["lsb_release", "-cs"],
            capture_output=True, text=True, check=True
        ).stdout.strip()

        component = component or self.apt_package_name
        signed_by = ""
        if self._keyring_path:
            signed_by = f" signed-by={self._keyring_path}"

        repo_line = (
            f"deb [arch={arch}{signed_by}] "
            f"{repo_url} {codename} {component}\n"
        )
        list_path = (
            f"/etc/apt/sources.list.d/"
            f"{self.apt_package_name}.list"
        )
        with open(list_path, "w", encoding="utf-8") as f:
            f.write(repo_line)

        origin = urlparse(repo_url).hostname
        pin_path = (
            f"/etc/apt/preferences.d/"
            f"99{self.apt_package_name}"
        )
        with open(pin_path, "w", encoding="utf-8") as f:
            f.write(
                f"Package: *\n"
                f"Pin: origin {origin}\n"
                f"Pin: release o={self.apt_package_name}\n"
                f"Pin-Priority: {pin_priority}\n"
            )

    def add_ppa(self, ppa: str = None, pin_priority: int = 1001):
        """Adds a PPA repository and pins it.

        :param ppa: PPA string, e.g. "ppa:owner/name".
        :type ppa: str
        :param pin_priority: Pin priority (default 1001).
        :type pin_priority: int
        """
        ppa = ppa or self.ppa
        if not ppa:
            raise ValueError(
                f"No PPA for package '{self.package_name}'. "
                "Provide one as argument or set ppa in the "
                "lock file."
            )
        subprocess.run(
            ["add-apt-repository", "-y", ppa],
            check=True, env=self._apt_env()
        )
        # Derive pin origin: ppa:owner/name -> LP-PPA-owner
        # or LP-PPA-owner-name when name != "ppa"
        parts = ppa.replace("ppa:", "").split("/")
        owner = parts[0]
        name = parts[1] if len(parts) > 1 else "ppa"
        if name == "ppa":
            origin = f"LP-PPA-{owner}"
        else:
            origin = f"LP-PPA-{owner}-{name}"

        pin_path = (
            f"/etc/apt/preferences.d/"
            f"{self.apt_package_name}"
        )
        with open(pin_path, "w", encoding="utf-8") as f:
            f.write(
                f"Package: *\n"
                f"Pin: release o={origin}\n"
                f"Pin-Priority: {pin_priority}\n"
            )

    def default_installation(self):
        """Installs the apt package, optionally version-pinned.

        If gpgKeyUrl, repository, or ppa are defined in the lock
        file, sets up the package source first. Runs apt-get
        update and install. Any additionalPackages from the lock
        file are installed alongside the main package.
        """
        if self.gpg_key_url:
            self.add_gpg_key()
        if self.repository:
            self.add_repository()
        if self.ppa:
            self.add_ppa()
        if self.version:
            pkg = f"{self.apt_package_name}={self.version}*"
        else:
            pkg = self.apt_package_name
        self._run_apt("update")
        self._run_apt(
            "install", "--no-install-recommends", "-y",
            pkg, *self.additional_packages
        )


class NpmDependency(Dependency):  # pylint: disable=too-few-public-methods
    """A dependency installed via npm global packages.

    Running the default_installation method will install the
    specified JavaScript packages globally with version pinning
    using npm.
    """

    def __init__(
        self,
        package_name: str,
        package_dict: dict,
        npm_cache_directory: str = "/tmp/npm-cache"
    ):
        super().__init__(package_name, package_dict)
        try:
            self.js_packages = package_dict["jsPackages"]
        except KeyError as e:
            raise KeyError(
                f"Package '{package_name}' has dependency type "
                "'npm' and therefore should include one or more "
                "JavaScript package definitions in 'jsPackages'"
            ) from e
        self.npm_cache_directory = npm_cache_directory

    def _package_to_npm_format(
        self, js_package_name: str, js_package_dict: dict
    ) -> str:
        """Converts a JS package name and dict to npm format.

        :param js_package_name: The name of the JS package.
        :type js_package_name: str
        :param js_package_dict: Dict with package details.
        :type js_package_dict: dict
        :return: 'package_name@version' or 'package_name'.
        :rtype: str
        """
        try:
            return (
                f"{js_package_name}@"
                f"{js_package_dict['version']}"
            )
        except KeyError:
            return js_package_name

    def default_installation(self):
        """Installs npm packages globally with version pinning.

        Checks that npm is available on PATH, then runs
        npm install -g with the specified packages and versions.

        Raises an EnvironmentError if npm is not found.
        """
        if not shutil.which("npm"):
            raise EnvironmentError(
                "npm is not available on PATH. Install Node.js "
                "before installing npm packages."
            )

        run_command = [
            "npm", "install", "-g",
            "--cache", self.npm_cache_directory,
            "--prefix", "/usr/local/npm-packages"
        ]
        run_command.extend([
            self._package_to_npm_format(pkg_name, pkg_dict)
            for pkg_name, pkg_dict
            in self.js_packages.items()
        ])

        subprocess.run(run_command, check=True)
