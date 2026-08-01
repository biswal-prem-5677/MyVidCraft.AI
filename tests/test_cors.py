"""
CORS configuration.

The API previously shipped `allow_origins=["*"]` together with
`allow_credentials=True`. That pair is not merely permissive, it is invalid: the
CORS spec forbids returning a wildcard origin on a credentialed response, and
Starlette works around it by echoing the requesting origin back instead — which
turns every site on the internet into an allowed credentialed origin. The
engineering review graded it Critical (§8, A05).

These tests pin the two properties that matter and are cheap to regress: the
default allows only the local UI, and a wildcard can never coexist with
credentials.
"""

from __future__ import annotations

from apps.api.settings import Settings

_ALLOW_ORIGIN = "access-control-allow-origin"
_ALLOW_CREDS = "access-control-allow-credentials"


def _preflight(client, origin: str):
    return client.options(
        "/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )


def test_default_origins_are_the_local_ui_only():
    parsed = Settings().cors_origin_list
    assert parsed == ["http://localhost:3000", "http://127.0.0.1:3000"]
    assert "*" not in parsed


def test_origins_parse_from_a_comma_separated_string():
    # Whitespace and a trailing comma are what a hand-edited .env actually
    # contains; neither should produce an empty origin that matches nothing.
    parsed = Settings(cors_origins="https://a.example, https://b.example,").cors_origin_list
    assert parsed == ["https://a.example", "https://b.example"]


def test_wildcard_disables_credentials():
    """The invalid combination must be impossible to configure, not just discouraged."""
    wild = Settings(cors_origins="*")
    assert wild.cors_origin_list == ["*"]
    assert wild.cors_allow_credentials is False


def test_explicit_origins_keep_credentials():
    assert Settings(cors_origins="https://app.example").cors_allow_credentials is True


def test_allowed_origin_is_echoed_on_preflight(client):
    res = _preflight(client, "http://localhost:3000")
    assert res.status_code == 200
    assert res.headers.get(_ALLOW_ORIGIN) == "http://localhost:3000"
    assert res.headers.get(_ALLOW_CREDS) == "true"


def test_unknown_origin_is_not_granted_access(client):
    """
    Starlette answers the preflight either way; what matters is that it does not
    hand back an allow-origin header, which is what the browser actually gates on.
    """
    res = _preflight(client, "https://evil.example")
    assert res.headers.get(_ALLOW_ORIGIN) != "https://evil.example"
    assert res.headers.get(_ALLOW_ORIGIN) != "*"
