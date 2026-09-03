"""
VK ID (OAuth 2.1 + PKCE) authorization helpers.

VK ID replaced the old `offline` permission: implicit-flow tokens now live for
24 hours only. Long-lived access is obtained by authorizing once with
`response_type=code` + PKCE and then exchanging a rotating refresh token for
fresh access tokens.

This module holds the pure HTTP/crypto pieces. Persistence lives in
`utils.vk_token_store`, wiring into API calls lives in `api.vk_client`.
"""

import base64
import hashlib
import logging
import re
import secrets
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import requests

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://id.vk.com/authorize"
# The classic endpoint. VK ID's code+PKCE flow requires a redirect_uri
# registered on the application, so /set_vk_token uses it only when
# VK_REDIRECT_URI is configured. Without one it falls back to the implicit
# flow below, whose tokens are IP-bound, live 24h and cannot be refreshed.
IMPLICIT_AUTHORIZE_URL = "https://oauth.vk.com/authorize"
TOKEN_URL = "https://id.vk.com/oauth2/auth"
DEFAULT_REDIRECT_URI = "https://oauth.vk.com/blank.html"

# Scopes the bot actually needs: wall.get for stream discovery,
# video.getComments for the live text translation.
DEFAULT_SCOPE = "video wall"

REQUEST_TIMEOUT = 20


class VKAuthError(Exception):
    """Raised when VK ID refuses to issue or refresh a token."""


def generate_pkce_pair() -> tuple:
    """
    Build a PKCE (verifier, challenge) pair.

    Returns:
        Tuple of (code_verifier, code_challenge) — challenge is S256 of verifier.
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def generate_state() -> str:
    """VK ID requires a `state` of at least 32 characters."""
    return secrets.token_hex(24)


def build_authorize_url(
    client_id: str,
    code_challenge: str,
    state: str,
    scope: str = DEFAULT_SCOPE,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
) -> str:
    """Build the URL the human opens once to grant access."""
    params = {
        "response_type": "code",
        "client_id": str(client_id),
        "scope": scope,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _post_token_request(payload: Dict[str, str]) -> Dict:
    """POST to the VK ID token endpoint and unwrap the response."""
    try:
        resp = requests.post(TOKEN_URL, data=payload, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.RequestException as e:
        raise VKAuthError(f"VK ID request failed: {e}") from e

    try:
        data = resp.json()
    except ValueError:
        raise VKAuthError(
            f"VK ID returned non-JSON response (HTTP {resp.status_code}): {resp.text[:200]}"
        )

    if "error" in data:
        raise VKAuthError(
            f"VK ID error: {data.get('error')} — {data.get('error_description', 'no description')}"
        )

    if not data.get("access_token"):
        raise VKAuthError(f"VK ID response has no access_token: {data}")

    return data


def exchange_code_for_tokens(
    client_id: str,
    code: str,
    code_verifier: str,
    device_id: str,
    state: str,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
) -> Dict:
    """
    Exchange the one-time `code` from the redirect for an access/refresh pair.

    Returns:
        Dict with access_token, refresh_token, expires_in, user_id.
    """
    return _post_token_request({
        "grant_type": "authorization_code",
        "client_id": str(client_id),
        "code": code,
        "code_verifier": code_verifier,
        "device_id": device_id,
        "redirect_uri": redirect_uri,
        "state": state,
    })


def refresh_access_token(
    client_id: str,
    refresh_token: str,
    device_id: str,
    state: Optional[str] = None,
) -> Dict:
    """
    Trade a refresh token for a fresh access token.

    VK ID rotates refresh tokens: the response carries a NEW refresh_token and
    the one passed in stops working. Callers must persist the new value.
    """
    return _post_token_request({
        "grant_type": "refresh_token",
        "client_id": str(client_id),
        "refresh_token": refresh_token,
        "device_id": device_id,
        "state": state or generate_state(),
    })


# ---------------------------------------------------------------------------
# Redirect parsing for the /set_vk_token command
# ---------------------------------------------------------------------------

@dataclass
class ImplicitToken:
    """What VK hands back on the blank.html redirect."""

    access_token: str
    expires_in: Optional[int] = None
    user_id: Optional[int] = None


@dataclass
class AuthCode:
    """What VK ID hands back on a code-flow redirect."""

    code: str
    device_id: Optional[str] = None
    state: Optional[str] = None


# vk1.a.<base64ish payload> — long enough that no ordinary text matches it.
_TOKEN_RE = re.compile(r"^vk\d+\.[a-z]\.[A-Za-z0-9_\-]{20,}$")


def build_implicit_authorize_url(
    client_id: str,
    scope: str = "video,wall",
    redirect_uri: str = DEFAULT_REDIRECT_URI,
) -> str:
    """
    Build the link a human opens to mint a fresh 24h token.

    Scope is comma-separated here: the classic endpoint rejects the
    space-separated OAuth 2.1 form.
    """
    params = {
        "response_type": "token",
        "client_id": str(client_id),
        "scope": scope,
        "redirect_uri": redirect_uri,
        "display": "page",
        "v": "5.199",
    }
    return f"{IMPLICIT_AUTHORIZE_URL}?{urlencode(params)}"


def _redirect_params(text: str) -> Dict[str, str]:
    """Collect params from both the fragment and the query of a redirect URL."""
    parsed = urlparse(text)
    params: Dict[str, str] = {}
    for part in (parsed.fragment, parsed.query):
        for key, value in parse_qs(part).items():
            if value:
                params.setdefault(key, value[0])
    return params


def parse_redirect(pasted: str):
    """
    Read whatever the user pasted back after authorizing in the browser.

    Handles both flows: an implicit-flow redirect (or a bare token) and a VK ID
    code-flow redirect carrying `code` + `device_id`.

    Args:
        pasted: Redirect URL from the address bar, or just the access token

    Returns:
        ImplicitToken or AuthCode, depending on what the URL carried.

    Raises:
        ValueError: nothing usable in the input, or VK reported a refusal.
    """
    text = (pasted or "").strip()
    if not text:
        raise ValueError("Пустой ввод")

    if not text.startswith("http"):
        if _TOKEN_RE.match(text):
            return ImplicitToken(access_token=text)
        raise ValueError("Это не похоже ни на токен, ни на адрес редиректа")

    params = _redirect_params(text)

    if "error" in params:
        raise ValueError(
            f"VK отказал: {params['error']} — {params.get('error_description', 'без пояснения')}"
        )

    def _as_int(name):
        try:
            return int(params[name])
        except (KeyError, TypeError, ValueError):
            return None

    token = params.get("access_token")
    if token:
        return ImplicitToken(
            access_token=token,
            expires_in=_as_int("expires_in"),
            user_id=_as_int("user_id"),
        )

    code = params.get("code")
    if code:
        return AuthCode(
            code=code,
            device_id=params.get("device_id"),
            state=params.get("state"),
        )

    raise ValueError("В адресе нет ни access_token, ни code")


def parse_implicit_redirect(pasted: str) -> ImplicitToken:
    """Like parse_redirect, but only accepts an implicit-flow result."""
    result = parse_redirect(pasted)
    if not isinstance(result, ImplicitToken):
        raise ValueError("В адресе нет access_token")
    return result
