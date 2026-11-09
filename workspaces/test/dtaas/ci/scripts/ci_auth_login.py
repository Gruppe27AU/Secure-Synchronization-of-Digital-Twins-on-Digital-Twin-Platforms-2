#!/usr/bin/env python3
"""Automate the OAuth2/OIDC login flow against a local Dex provider for
headless CI testing with Oathkeeper + login-relay.

Usage:
    ci_auth_login.py [BASE_URL] [USERNAME] [DEX_BASE_URL] [PASSWORD]
                     [--path PATH] [--no-verify | --ca-bundle PATH]

Defaults:
    BASE_URL     = http://localhost
    USERNAME     = user1
    DEX_BASE_URL = http://dex:5556
    PASSWORD     = password
    PATH         = (empty — accesses /<USERNAME>/)

Exit codes:
    0 – login succeeded and protected resource returned HTTP 200
    1 – any failure

How it works (no browser needed):
    1. GET <BASE_URL>/<USERNAME>/
       -> Oathkeeper sees no token, redirects to login-relay
       -> login-relay redirects to Dex /dex/auth?...
       -> requests follows all redirects and lands on Dex's login page HTML
    2. Extract the form <action> URL from the Dex login page.
    3. POST username + password to that URL.
       -> Dex validates credentials, skips the approval screen, and issues
          a 302 to <BASE_URL>/login-relay/callback?code=XXX&state=XXX
       -> requests follows the redirect; login-relay exchanges the code,
          sets the dtaas_access_token cookie, and issues a final 302 back
          to the original protected URL.
    4. GET <BASE_URL>/<USERNAME>/ with the dtaas_access_token cookie -> expect HTTP 200.

TLS verification:
    By default Python's certifi bundle is used.  When testing against a
    mkcert-generated certificate pass the mkcert root CA:

        ci_auth_login.py https://localhost user1 http://dex:5556 password \\
            --ca-bundle "$(mkcert -CAROOT)/rootCA.pem"

    Alternatively, pass --no-verify to skip verification entirely (insecure).
"""

import argparse
import re
import sys
from typing import Union

# Seconds to wait for any single HTTP request/redirect chain before giving up.
REQUEST_TIMEOUT = 30

try:
    import requests
    import urllib3
    from requests.exceptions import RequestException
except ImportError:
    print(
        "❌ The 'requests' package is required.  Install it with:\n"
        "       pip install requests",
        file=sys.stderr,
    )
    sys.exit(1)


def extract_form_action(html: str) -> str:
    """Extract the form action URL from Dex login HTML.

    Dex renders: <form method="post" action="/dex/auth/local?req=XXXXX">
    HTML-encoded ampersands (&amp;) are decoded to & for use in URLs.
    """
    match = re.search(r'action="([^"]+)"', html)
    if not match:
        return ""
    return match.group(1).replace("&amp;", "&")


def _fetch_dex_login_page(
    session: requests.Session, protected_url: str
) -> str:
    """Follow redirects to reach the Dex login page.

    Returns the login page HTML, or an empty string on failure.
    """
    print("=== Step 1: Follow redirects to Dex login page ===")
    try:
        resp = session.get(protected_url, allow_redirects=True, timeout=REQUEST_TIMEOUT)
    except RequestException as exc:
        print(f"❌ Failed to reach {protected_url}: {exc}")
        return ""

    if "action=" not in resp.text:
        print("❌ Did not reach Dex login page. HTML dump:")
        print(resp.text)
        return ""
    return resp.text


def _submit_credentials(
    session: requests.Session, post_url: str, email: str, password: str
) -> bool:
    """POST credentials to Dex and follow the redirect chain."""
    print("=== Step 3: POST credentials to Dex, follow redirects ===")
    try:
        resp = session.post(
            post_url,
            data={"login": email, "password": password},
            allow_redirects=True,
            timeout=REQUEST_TIMEOUT,
        )
    except RequestException as exc:
        print(f"❌ Failed to POST credentials to {post_url}: {exc}")
        return False
    print(f"  HTTP code after credential POST + redirect chain: {resp.status_code}")
    return True


def _verify_authenticated_access(
    session: requests.Session, protected_url: str
) -> bool:
    """Access the protected resource and check for HTTP 200."""
    print("=== Step 4: Access protected resource with session cookie ===")
    try:
        resp = session.get(protected_url, allow_redirects=True, timeout=REQUEST_TIMEOUT)
    except RequestException as exc:
        print(f"❌ Failed to GET {protected_url} after login: {exc}")
        return False

    print(f"  HTTP code for authenticated request: {resp.status_code}")
    if resp.status_code == 200:
        print(f"✅ Authenticated access to {protected_url} succeeded (HTTP 200)")
        return True
    print(f"❌ Expected HTTP 200 but got {resp.status_code}")
    print("--- Response content (first 2000 chars) ---")
    print(resp.text[:2000])
    return False


def login(
    protected_url: str,
    username: str,
    dex_base_url: str,
    password: str,
    verify: Union[bool, str] = True,
) -> bool:
    """Perform the full OAuth2 login flow.

    Args:
        protected_url: Full URL of the protected resource to verify after login.
        username: Workspace username to log in as.
        dex_base_url: Base URL of the Dex OIDC provider (e.g. http://dex:5556).
        password: Password for the user.
        verify: TLS verification – True uses the default certifi bundle,
                False disables verification, or a filesystem path to a CA
                bundle / directory of certificates.

    Returns:
        True when authenticated access returns HTTP 200, False otherwise.
    """
    email = f"{username}@localhost"

    session = requests.Session()
    session.verify = verify

    # ── Step 1 ─────────────────────────────────────────────────────────────
    login_html = _fetch_dex_login_page(session, protected_url)
    if not login_html:
        return False

    # ── Step 2 ─────────────────────────────────────────────────────────────
    print("=== Step 2: Extract Dex login form action URL ===")
    form_path = extract_form_action(login_html)
    if not form_path:
        print("❌ Could not extract form action from Dex login page.")
        print(login_html)
        return False
    print(f"  Form action path: {form_path}")

    # Only accept relative paths to prevent SSRF via a compromised form action.
    if form_path.startswith("http"):
        print(f"❌ Refusing absolute URL in form action: {form_path}")
        return False

    # ── Step 3 ─────────────────────────────────────────────────────────────
    post_url = f"{dex_base_url}{form_path}"
    if not _submit_credentials(session, post_url, email, password):
        return False

    # ── Step 4 ─────────────────────────────────────────────────────────────
    return _verify_authenticated_access(session, protected_url)


def main() -> int:
    """Parse CLI arguments and run the OAuth2 login flow."""
    parser = argparse.ArgumentParser(
        description=(
            "Automate the OAuth2/OIDC login flow against a local Dex provider "
            "for headless CI testing with Oathkeeper + login-relay."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "base_url",
        nargs="?",
        default="http://localhost",
        help="Base URL of the protected service (default: http://localhost)",
    )
    parser.add_argument(
        "username",
        nargs="?",
        default="user1",
        help="Workspace username to log in as (default: user1)",
    )
    parser.add_argument(
        "dex_base_url",
        nargs="?",
        default="http://dex:5556",
        help="Dex OIDC base URL (default: http://dex:5556)",
    )
    parser.add_argument(
        "password",
        nargs="?",
        default="password",
        help="User password (default: password)",
    )

    parser.add_argument(
        "--path",
        metavar="PATH",
        default="",
        help=(
            "Sub-path within the user workspace to verify after login "
            "(e.g. 'services').  Defaults to the workspace root '/<USERNAME>/'."
        ),
    )

    tls_group = parser.add_mutually_exclusive_group()
    tls_group.add_argument(
        "--no-verify",
        action="store_true",
        help=(
            "Disable TLS certificate verification entirely (insecure; "
            "prefer --ca-bundle for self-signed certificates)"
        ),
    )
    tls_group.add_argument(
        "--ca-bundle",
        metavar="PATH",
        help=(
            "Path to a CA bundle (PEM file or directory) to use for TLS "
            "verification instead of the default certifi bundle. "
            "Pass the mkcert root CA with: "
            '--ca-bundle "$(mkcert -CAROOT)/rootCA.pem"'
        ),
    )

    args = parser.parse_args()

    verify: Union[bool, str]
    if args.no_verify:
        verify = False
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    elif args.ca_bundle:
        verify = args.ca_bundle
    else:
        verify = True

    clean_path = args.path.strip("/")
    protected_url = (
        f"{args.base_url}/{args.username}/{clean_path}" if clean_path
        else f"{args.base_url}/{args.username}/"
    )
    success = login(
        protected_url=protected_url,
        username=args.username,
        dex_base_url=args.dex_base_url,
        password=args.password,
        verify=verify,
    )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
