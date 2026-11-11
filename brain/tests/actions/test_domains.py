"""Domain allowlist + internal-URL rewrite (`actions/domains.py`, §4.3.5 domains, §26.5 action_domains.yaml)."""
from __future__ import annotations

import pytest

from ahbo.actions import domains

# Unsubstituted {PUBLIC_BASE_URL} must never match (defence against a mis-loaded config).
RAW = {"allow_submit": ["{PUBLIC_BASE_URL}/mock-centre/"], "read_only": ["*.gov.hk", "*.hkcss.org.hk"]}
# What the loader produces with PUBLIC_BASE_URL=https://test.invalid.
CFG = {"allow_submit": ["https://test.invalid/mock-centre/"], "read_only": RAW["read_only"]}
INTERNAL = "http://api:8000"


def test_submit_read_only_denied():
    assert domains.classify_url("https://test.invalid/mock-centre/", CFG) == "submit"
    assert domains.classify_url("https://test.invalid/mock-centre/?variant=captcha", CFG) == "submit"
    assert domains.classify_url("https://www.gov.hk/en/activities/", CFG) == "read_only"
    assert domains.classify_url("https://gov.hk/", CFG) == "read_only"          # bare host matches *.gov.hk
    assert domains.classify_url("https://www.hkcss.org.hk/", CFG) == "read_only"
    assert domains.classify_url("https://notgov.hk/", CFG) == "denied"          # *.gov.hk ≠ notgov.hk
    assert domains.classify_url("https://evil.test.invalid/mock-centre/", CFG) == "denied"
    assert domains.classify_url("https://test.invalid/mock-centre-evil/", CFG) == "denied"   # prefix is path-safe
    assert domains.classify_url("http://test.invalid/mock-centre/", CFG) == "denied"          # scheme + origin
    assert domains.classify_url("javascript:alert(1)", CFG) == "denied"
    assert domains.classify_url("https://user:pw@test.invalid/mock-centre/", CFG) == "denied"
    assert domains.classify_url("", CFG) == "denied"
    assert domains.classify_url("https://test.invalid/mock-centre/", RAW) == "denied"


def test_read_only_beats_allow_submit():
    both = {"allow_submit": ["https://test.invalid/mock-centre/"], "read_only": ["test.invalid"]}
    assert domains.classify_url("https://test.invalid/mock-centre/", both) == "read_only"


def test_navigation_url_rewrites_only_allowlisted_public_urls():
    assert domains.navigation_url("https://test.invalid/mock-centre/?variant=hkid", CFG,
                                  "https://test.invalid", INTERNAL) == \
        "http://api:8000/mock-centre/?variant=hkid"
    # different public origin → untouched; read-only → untouched; unsubstituted prefix → untouched
    assert domains.navigation_url("https://other.example/mock-centre/", CFG, "https://test.invalid",
                                  INTERNAL) == "https://other.example/mock-centre/"
    assert domains.navigation_url("https://www.gov.hk/x/", CFG, "https://test.invalid", INTERNAL) == \
        "https://www.gov.hk/x/"
    assert domains.navigation_url("https://test.invalid/mock-centre/", RAW, "https://test.invalid",
                                  INTERNAL) == "https://test.invalid/mock-centre/"
    # no internal base configured → no rewrite
    assert domains.navigation_url("https://test.invalid/mock-centre/", CFG, "https://test.invalid",
                                  "") == "https://test.invalid/mock-centre/"


def test_internal_base(monkeypatch):
    monkeypatch.setenv("AHBO_ACTION_INTERNAL_BASE", "http://ahbo-t2-api:8000/")
    assert domains.internal_base(object()) == "http://ahbo-t2-api:8000"
    monkeypatch.delenv("AHBO_ACTION_INTERNAL_BASE")
    s = type("S", (), {"api_internal_url": "http://api-in:8000"})()
    assert domains.internal_base(s) == "http://api-in:8000"
    assert domains.internal_base(object()) == "http://api:8000"     # settings without the attr → default


@pytest.mark.parametrize("prefix,url,ok", [
    ("https://t.invalid/mock-centre/", "https://t.invalid/mock-centre/", True),
    ("https://t.invalid/mock-centre/", "https://t.invalid/mock-centre", True),   # missing slash still matches
    ("https://t.invalid/mock-centre/", "https://t.invalid/mock-centre/x?y=1", True),
    ("https://t.invalid/mock-centre/", "https://t.invalid:443/mock-centre/", True),   # default port = same origin
])
def test_prefix_match(prefix, url, ok):
    got = domains.classify_url(url, {"allow_submit": [prefix], "read_only": []})
    assert (got == "submit") is ok
