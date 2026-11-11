"""Domain rules for external actions (§4.3.5 Domains, `config/action_domains.yaml`).

- `allow_submit`: URL prefixes (after `{PUBLIC_BASE_URL}` substitution) where a form may be submitted.
- `read_only`: host globs (`*.gov.hk` …) that are never submitted to, even if also listed above.
- Anything else is denied.

Internal-URL rewrite: the Action Runner's Chromium lives in the `browser` container. Inside Docker it cannot
rely on reaching the public hostname (hairpin NAT through Caddy), so an **allowlisted** URL under
`PUBLIC_BASE_URL` is navigated at the same path on the api's internal address (`AHBO_ACTION_INTERNAL_BASE`,
default `settings.api_internal_url`, i.e. `http://api:8000`). The allowlist is still checked on the public URL;
the rewrite only changes how the same page is reached. Non-allowlisted URLs are never rewritten.
"""
from __future__ import annotations

import fnmatch
import os
from typing import Any, Literal
from urllib.parse import urlsplit

Verdict = Literal["submit", "read_only", "denied"]


def origin(url: str) -> str:
    u = urlsplit(url)
    host = (u.hostname or "").lower()
    port = u.port or {"http": 80, "https": 443}.get(u.scheme, 0)
    return f"{u.scheme}://{host}:{port}"


def _prefix_match(url: str, prefix: str) -> bool:
    if "{PUBLIC_BASE_URL}" in prefix:      # unsubstituted config: never matches
        return False
    u, p = urlsplit(url), urlsplit(prefix)
    if not p.scheme or origin(url) != origin(prefix):
        return False
    ppath = p.path or "/"
    path = u.path or "/"
    if not ppath.endswith("/"):
        ppath += "/"
    return (path if path.endswith("/") else path + "/").startswith(ppath)


def _host_glob(host: str, pattern: str) -> bool:
    pattern = pattern.lower()
    if fnmatch.fnmatch(host, pattern):
        return True
    return pattern.startswith("*.") and host == pattern[2:]


def classify_url(url: str, domains: dict[str, Any]) -> Verdict:
    u = urlsplit(url or "")
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
        return "denied"
    host = u.hostname.lower()
    if any(_host_glob(host, p) for p in domains.get("read_only") or []):
        return "read_only"
    if any(_prefix_match(url, p) for p in domains.get("allow_submit") or []):
        return "submit"
    return "denied"


def internal_base(settings: Any) -> str:
    return (os.environ.get("AHBO_ACTION_INTERNAL_BASE") or getattr(settings, "api_internal_url", "")
            or "http://api:8000").rstrip("/")


def navigation_url(url: str, domains: dict[str, Any], public_base_url: str, internal: str) -> str:
    """The URL the browser actually opens (see module doc). Only allowlisted public URLs are rewritten."""
    base = public_base_url.rstrip("/")
    if classify_url(url, domains) != "submit" or not base:
        return url
    if origin(url) != origin(base) or not internal:
        return url
    u = urlsplit(url)
    rest = u.path + (f"?{u.query}" if u.query else "")
    return internal.rstrip("/") + rest
