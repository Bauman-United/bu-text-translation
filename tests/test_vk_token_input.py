"""Parsing whatever the user pastes back after authorizing in the browser."""

import pytest

from api.vk_auth import (
    AuthCode,
    build_authorize_url,
    build_implicit_authorize_url,
    parse_implicit_redirect,
    parse_redirect,
)

# Real VK tokens are ~220 chars; the parser deliberately requires a long tail so
# ordinary text pasted by mistake is not mistaken for a token.
TOKEN = "vk1.a." + "ABCdef0123456789_-" * 12
FULL = (f"https://oauth.vk.com/blank.html#access_token={TOKEN}"
        "&expires_in=86400&user_id=59411873")


def test_parses_the_whole_redirect_url():
    r = parse_implicit_redirect(FULL)
    assert r.access_token == TOKEN
    assert r.expires_in == 86400
    assert r.user_id == 59411873


def test_parses_a_bare_token():
    """People paste just the token as often as the whole URL."""
    r = parse_implicit_redirect(TOKEN)
    assert r.access_token == TOKEN
    assert r.expires_in is None


def test_tolerates_surrounding_whitespace_and_newlines():
    r = parse_implicit_redirect(f"  \n{FULL}\n ")
    assert r.access_token == TOKEN


def test_reads_params_from_the_query_string_too():
    r = parse_implicit_redirect(
        f"https://oauth.vk.com/blank.html?access_token={TOKEN}&expires_in=3600"
    )
    assert r.access_token == TOKEN
    assert r.expires_in == 3600


def test_rejects_an_error_redirect_with_vk_reason():
    with pytest.raises(ValueError, match="access_denied"):
        parse_implicit_redirect(
            "https://oauth.vk.com/blank.html#error=access_denied"
            "&error_description=User+denied"
        )


@pytest.mark.parametrize("junk", ["", "   ", "привет", "https://vk.com/", "не токен"])
def test_rejects_junk(junk):
    with pytest.raises(ValueError):
        parse_implicit_redirect(junk)


def test_authorize_url_uses_the_flow_that_actually_works():
    """Without a registered redirect_uri the token command falls back to the
    classic implicit endpoint."""
    url = build_implicit_authorize_url("54546527")
    assert url.startswith("https://oauth.vk.com/authorize?")
    assert "response_type=token" in url
    assert "scope=video%2Cwall" in url or "scope=video,wall" in url
    assert "offline" not in url  # VK ID removed it; asking for it errors out


# ---------------------------------------------------------------------------
# Code+PKCE flow (used when VK_REDIRECT_URI is registered on the app)
# ---------------------------------------------------------------------------

CODE_REDIRECT = (
    "https://example.com/vk?code=abc123def"
    "&device_id=DEVICE42&state=" + "s" * 48 + "&type=code_v2"
)


def test_parses_a_code_flow_redirect():
    r = parse_redirect(CODE_REDIRECT)
    assert isinstance(r, AuthCode)
    assert r.code == "abc123def"
    assert r.device_id == "DEVICE42"
    assert r.state == "s" * 48


def test_token_redirect_wins_over_code_when_both_present():
    """Should never happen, but the token is directly usable — prefer it."""
    r = parse_redirect(f"https://example.com/vk?code=abc#access_token={TOKEN}")
    assert not isinstance(r, AuthCode)
    assert r.access_token == TOKEN


def test_url_with_neither_token_nor_code_is_rejected():
    with pytest.raises(ValueError, match="ни access_token, ни code"):
        parse_redirect("https://example.com/vk?foo=bar")


def test_parse_implicit_redirect_refuses_a_code_redirect():
    """Callers that can only store a ready token must not get an AuthCode."""
    with pytest.raises(ValueError):
        parse_implicit_redirect(CODE_REDIRECT)


def test_code_flow_authorize_url_carries_pkce():
    url = build_authorize_url(
        "54546527", "CHALLENGE", "s" * 48, redirect_uri="https://example.com/vk"
    )
    assert url.startswith("https://id.vk.com/authorize?")
    assert "response_type=code" in url
    assert "code_challenge=CHALLENGE" in url
    assert "code_challenge_method=S256" in url
    assert "redirect_uri=https%3A%2F%2Fexample.com%2Fvk" in url
