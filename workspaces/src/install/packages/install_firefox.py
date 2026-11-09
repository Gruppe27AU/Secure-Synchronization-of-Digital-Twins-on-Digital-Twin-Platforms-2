"""Installs Firefox from the Mozilla Team PPA and configures it."""
import os
import subprocess
from pathlib import Path

from installation_util import get_dependencies_for_package, ARCHITECTURE

home = Path(os.environ.get("HOME"))
inst_dir = Path(os.environ.get("INST_DIR"))

get_dependencies_for_package("firefox").default_installation()

# Use system certificate store instead of Firefox's bundled one
LIB_ARCH_MAP = {
    "amd64": "x86_64", "x86_64": "x86_64", "x64": "x86_64",
    "arm64": "aarch64", "aarch64": "aarch64",
}
LIB_ARCH = LIB_ARCH_MAP[ARCHITECTURE]
nssckbi = Path("/usr/lib/firefox/libnssckbi.so")
p11_kit = Path(
    f"/usr/lib/{LIB_ARCH}-linux-gnu/pkcs11/p11-kit-trust.so"
)
if nssckbi.exists():
    nssckbi.unlink()
os.link(str(p11_kit), str(nssckbi))

# Disable first-run pages and telemetry
prefs_dir = Path(
    "/usr/lib/firefox/browser/defaults/preferences"
)
prefs_dir.mkdir(parents=True, exist_ok=True)
(prefs_dir / "firefox.js").write_text(
    'pref("datareporting.policy.firstRunURL", "");\n'
    'pref("datareporting.policy.dataSubmissionEnabled", false);\n'
    'pref("datareporting.healthreport.service.enabled", false);\n'
    'pref("datareporting.healthreport.uploadEnabled", false);\n'
    'pref("trailhead.firstrun.branches", "nofirstrun-empty");\n'
    'pref("browser.aboutwelcome.enabled", false);\n'
)

# Create default profile
subprocess.run(["chown", "-R", "0:0", str(home)], check=True)
subprocess.run(
    ["firefox", "-headless", "-CreateProfile",
     f"kasm {home}/.mozilla/firefox/kasm"],
    check=True
)

# Silence the unprivileged-namespaces security nag
user_js = home / ".mozilla/firefox/kasm/user.js"
user_js.write_text(
    'user_pref("security.sandbox.warn_unprivileged_namespaces",'
    " false);\n"
)
subprocess.run(["chown", "1000:1000", str(user_js)], check=True)

# Pin this profile as the default for the installation hash
with open(home / ".mozilla/firefox/profiles.ini", "a", encoding="utf-8") as f:
    f.write(
        "[Install4F96D1932A9F858E]\n"
        "Default=kasm\n"
        "Locked=1\n"
    )

# Final permissions
subprocess.run(["chown", "-R", "1000:0", str(home)], check=True)
subprocess.run(
    ["find", "/usr/share/", "-name", "icon-theme.cache",
     "-exec", "rm", "-f", "{}", ";"],
    check=True
)
subprocess.run(
    ["chown", "-R", "1000:1000", str(home / ".mozilla")],
    check=True
)
