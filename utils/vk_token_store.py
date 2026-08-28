"""
Persistence for VK ID access/refresh tokens.

Lives next to `game_schedules.json` in the repo `data/` directory, so both are
covered by the same Docker volume. Losing this file means the refresh chain is
broken and a human has to re-authorize via `scripts/vk_authorize.py`.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Refresh this many seconds before the access token actually expires, so a call
# never goes out with a token that dies mid-flight.
EXPIRY_MARGIN = 300


class VKTokens:
    """A stored VK ID token set."""

    def __init__(
        self,
        access_token: str,
        refresh_token: Optional[str] = None,
        device_id: Optional[str] = None,
        expires_at: float = 0.0,
        user_id: Optional[int] = None,
    ):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.device_id = device_id
        self.expires_at = expires_at  # 0.0 means "no known expiry"
        self.user_id = user_id

    @property
    def can_refresh(self) -> bool:
        return bool(self.refresh_token and self.device_id)

    @property
    def is_expired(self) -> bool:
        """True when the token is past (or nearly past) its expiry."""
        if not self.expires_at:
            return False
        return time.time() >= self.expires_at - EXPIRY_MARGIN

    @property
    def seconds_left(self) -> Optional[float]:
        if not self.expires_at:
            return None
        return max(0.0, self.expires_at - time.time())

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "device_id": self.device_id,
            "expires_at": self.expires_at,
            "user_id": self.user_id,
        }


def _get_store_path() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    return repo_root / "data" / "vk_token.json"


def load_tokens() -> Optional[VKTokens]:
    """Read the stored token set, or None if there isn't one."""
    path = _get_store_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"Could not read VK token store at {path}: {e}")
        return None

    access_token = raw.get("access_token")
    if not access_token:
        return None

    return VKTokens(
        access_token=access_token,
        refresh_token=raw.get("refresh_token"),
        device_id=raw.get("device_id"),
        expires_at=float(raw.get("expires_at") or 0.0),
        user_id=raw.get("user_id"),
    )


def save_tokens(tokens: VKTokens) -> None:
    """Persist a token set, creating the data directory if needed."""
    path = _get_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tokens.to_dict(), indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        # Best effort — some filesystems (mounted volumes) refuse chmod.
        pass
    logger.info(f"VK tokens saved to {path}")


def save_from_response(response: dict, fallback_device_id: Optional[str] = None) -> VKTokens:
    """
    Build and persist a VKTokens from a VK ID token-endpoint response.

    VK ID rotates refresh tokens, so whatever comes back must replace what we had.
    """
    expires_in = response.get("expires_in")
    expires_at = time.time() + float(expires_in) if expires_in else 0.0

    tokens = VKTokens(
        access_token=response["access_token"],
        refresh_token=response.get("refresh_token"),
        device_id=response.get("device_id") or fallback_device_id,
        expires_at=expires_at,
        user_id=response.get("user_id"),
    )
    save_tokens(tokens)
    return tokens
