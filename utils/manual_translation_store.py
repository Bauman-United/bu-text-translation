"""
Persistence for the manual Telegram translation mode.

Stored next to the game schedules and the VK token so a deploy in the middle of
a match does not lose the running score. Without this, restarting the bot at
half-time would silently reset the score to 0-0 and the next goal would be
announced as the opener.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class ManualTranslationState:
    """A manual translation session in progress."""

    def __init__(
        self,
        our_score: int = 0,
        opponent_score: int = 0,
        history: Optional[List[str]] = None,
        channel_id: Optional[str] = None,
    ):
        self.our_score = our_score
        self.opponent_score = opponent_score
        self.history = history or []
        self.channel_id = channel_id

    def to_dict(self) -> dict:
        return {
            "our_score": self.our_score,
            "opponent_score": self.opponent_score,
            "history": self.history,
            "channel_id": self.channel_id,
        }


def _get_store_path() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    return repo_root / "data" / "manual_translation.json"


def load_state() -> Optional[ManualTranslationState]:
    """Read the active session, or None when no translation is running."""
    path = _get_store_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"Could not read manual translation state at {path}: {e}")
        return None

    if not isinstance(raw, dict):
        return None

    return ManualTranslationState(
        our_score=int(raw.get("our_score", 0)),
        opponent_score=int(raw.get("opponent_score", 0)),
        history=list(raw.get("history") or []),
        channel_id=raw.get("channel_id"),
    )


def save_state(state: ManualTranslationState) -> None:
    """Persist the running session."""
    path = _get_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def clear_state() -> None:
    """End the session — /end_translation, or a session that was never started."""
    path = _get_store_path()
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        logger.error(f"Could not clear manual translation state: {e}")


def is_active() -> bool:
    return load_state() is not None
