dependencies.lock is a JSON file that is used to pin versions and sources of
the Workspaces dependencies.
On the top level it contains values, the `lockVersion` and the `packages`.

`lockVersion` is the version of the formatting that the file uses, and is
checked by the installation scripts before they try to read it. In case of
mismatch between this version number and the number expected by the script the
installation will error out.

`packages` contains descriptions of dependency relevant information on all the
semantically differentiated packages that are installed into the workspace.
Each contained packages has a name that defines it, and contains at least the
`type` of dependency it is. This `type` parameter then informs how the package
is installed and what other parameters can/must be set for it to be installed.

Currently the types are:
 - `bin-tar`: Short for Binary Tar, signifies that the package is downloaded as
    a tar file, which is expected to contian one or more "binaries" (which in
    truth can be any executable file that should be available in the users PATH
    after installation).
 - `apt`: Signifies that the package is installed via the debeian package 
    manager.
 - `pip-venv`: Signifies that the package is available on PyPI and should be
    installed via pip in a virtual environment and linked into PATH.
 - `npm`: Signifies that the package is installable via npm.

The possible parameters for each package are described in the following
sections.

## `bin-tar`
A `bin-tar` type package must have all of the following parameters set.
 - `compression`: The compression used for the tar. Possible values are `xz`
    and `gz`.
 - `amd64`/`arm64`: Contains the source and checksum for the amd64 or arm64
    binaries respectively
     - `source`: Url for the location of the tar file containing the binaries.
     - `checksum`: sha256 hash of the tar file that is expected to be located at
        `source`.

## `apt`
All parameters for an `apt` type dependency are optional, but `ppa` and 
`repository` are mutually exclusive.
 - `ppa`: The Personal Package Archive that is to be installed.
 - `repository`: Url for the location of a repository to be installed.
 - `gpgKeyUrl`: Url for the location of a gpg key used to check the files downloaded from `repository`.
 - `version`: The version of the package to install. If omitted, the latest version will be installed.
 - `additionalPackages`: Additional packages already present in the base repository that this depedency depends on in turn. These will be installed along with the package.

## `pip-venv`
A `pip-venv` dependency has only one parameter `pyPackages` which must be set
and must contain at least one object whose name is the name of a package in
PyPI, and which in turn optionally can contain the `version` paramter,
designating the specific version to install. If the `version` parameter is
omitted, the latest version of the python pacakge available on PyPI will be
installed.

## `npm`
A `npm` dependency works similarly to a `pip-venv` dependency, except its one
parameter is called `jsPackages` and the name of the contained objects must
correspond to the name of a packege in npm.