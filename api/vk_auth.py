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
import secrets
from typing import Dict, Optional
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://id.vk.com/authorize"
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
