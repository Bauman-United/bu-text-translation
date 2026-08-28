"""Parsing whatever the user pastes back after authorizing in the browser."""

import pytest

from api.vk_auth import build_implicit_authorize_url, parse_implicit_redirect

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
    """The VK ID code flow needs a registered redirect_uri this app cannot have,
    so the token command must use the classic implicit endpoint."""
    url = build_implicit_authorize_url("54546527")
    assert url.startswith("https://oauth.vk.com/authorize?")
    assert "response_type=token" in url
    assert "scope=video%2Cwall" in url or "scope=video,wall" in url
    assert "offline" not in url  # VK ID removed it; asking for it errors out
