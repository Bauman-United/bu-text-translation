"""
Warn about the VK token before it matters.

An implicit-flow token cannot be refreshed automatically (see api/vk_auth), so
it has to be replaced by hand roughly once a day. Finding that out five minutes
before kick-off is the failure mode worth avoiding, so this checks periodically
and nags only when a scheduled game would actually be affected. A code-flow
token set carries a refresh token and renews itself, so it is never nagged
about — only its complete absence is.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Set

from utils.game_schedule import GameSchedule, list_game_schedules
from utils.vk_token_store import VKTokens, load_tokens

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 3600


class WarningKind(Enum):
    NONE = "none"
    MISSING = "missing"
    EXPIRED = "expired"
    DIES_BEFORE_GAME = "dies_before_game"


@dataclass
class TokenWarning:
    kind: WarningKind
    key: str = ""
    text: str = ""


_OK = TokenWarning(WarningKind.NONE)


def _next_vk_game(schedules: List[GameSchedule], now: datetime) -> Optional[GameSchedule]:
    """
    The soonest upcoming game that actually needs VK.

    Site-mode games parse the league page and never call VK, so a dead token
    does not affect them.
    """
    upcoming = [
        s for s in schedules
        if s.parse_mode != "site" and s.game_datetime_utc > now
    ]
    if not upcoming:
        return None
    return min(upcoming, key=lambda s: s.game_datetime_utc)


def evaluate_token(
    tokens: Optional[VKTokens],
    schedules: List[GameSchedule],
    now: datetime,
) -> TokenWarning:
    """
    Decide whether the owner should be told something about the token.

    Args:
        tokens: Stored token set, or None
        schedules: All saved games
        now: Current UTC time

    Returns:
        A TokenWarning; `kind is WarningKind.NONE` means stay quiet. `key`
        identifies the warning so it is only sent once.
    """
    game = _next_vk_game(schedules, now)
    if game is None:
        # Nothing scheduled that needs VK — no reason to nag.
        return _OK

    kickoff = game.game_datetime_utc.astimezone(timezone.utc)
    when = game.game_datetime.strftime("%Y-%m-%d %H:%M")

    if tokens is None:
        return TokenWarning(
            WarningKind.MISSING,
            key=f"missing:{game.id}",
            text=(
                "🔑 <b>Токена VK нет</b>\n\n"
                f"Ближайшая игра: {when}\n"
                "Пришли /set_vk_token, чтобы получить ссылку."
            ),
        )

    if tokens.can_refresh:
        # The refresh chain renews the access token on its own; expiry-based
        # warnings would be noise. A broken refresh surfaces through the
        # client's auth-failure notification instead.
        return _OK

    if tokens.expires_at and tokens.is_expired:
        return TokenWarning(
            WarningKind.EXPIRED,
            key=f"expired:{game.id}",
            text=(
                "🔑 <b>Токен VK истёк</b>\n\n"
                f"Ближайшая игра: {when}\n"
                "Пришли /set_vk_token, чтобы обновить."
            ),
        )

    if not tokens.expires_at:
        # No known expiry — nothing to predict.
        return _OK

    expiry = datetime.fromtimestamp(tokens.expires_at, tz=timezone.utc)
    if expiry < kickoff:
        hours_left = max(0, int((expiry - now).total_seconds() // 3600))
        return TokenWarning(
            WarningKind.DIES_BEFORE_GAME,
            key=f"dies:{game.id}",
            text=(
                "🔑 <b>Токен VK не доживёт до игры</b>\n\n"
                f"Игра: {when}\n"
                f"Токен истекает через {hours_left} ч — раньше начала.\n"
                "Пришли /set_vk_token ближе к матчу."
            ),
        )

    return _OK


async def run_token_watchdog(app, user_id: int):
    """Check hourly and send each distinct warning at most once."""
    sent: Set[str] = set()
    logger.info("VK token watchdog started")

    while True:
        try:
            warning = evaluate_token(
                load_tokens(), list_game_schedules(), datetime.now(timezone.utc)
            )
            if warning.kind is not WarningKind.NONE and warning.key not in sent:
                sent.add(warning.key)
                try:
                    await app.bot.send_message(
                        chat_id=user_id, text=warning.text, parse_mode="HTML"
                    )
                    logger.info(f"Token watchdog notified owner: {warning.kind.value}")
                except Exception as e:
                    logger.error(f"Token watchdog could not notify: {e}")
            elif warning.kind is WarningKind.NONE:
                # Situation resolved — allow the same warning again later.
                sent.clear()
        except Exception as e:
            logger.error(f"Token watchdog check failed: {e}")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
