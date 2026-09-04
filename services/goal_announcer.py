"""
Shared goal announcing.

Every source of score updates — VK live-stream comments, the match-page site
monitor, the one-shot /match command and the manual Telegram translation —
ends the same way: work out who scored, write a line about it, attach a
celebration video and post it to the channel. That logic used to be copy-pasted
into each of them, so wording drifted apart and fixes had to be made four times.
It lives here now.
"""

import logging
import os
from typing import Dict, List, Optional, Tuple

from telegram.ext import Application

logger = logging.getLogger(__name__)

# How many past messages to hand GPT as context.
HISTORY_LIMIT = 10

CELEBRATION_ROOT = "celebrations"

# Surnames and nicknames that map to a player's own celebration clip.
# Anything unlisted falls back to the shared one.
_CELEBRATION_BY_SURNAME = {
    "алексеев": "алексеев",
    "богомолов": "богомолов",
    "багич": "богомолов",
    "гришанов": "гришанов",
    "заночуев": "заночуев",
    "калиниченко": "калиниченко",
    "королев": "королев",
    "королёв": "королев",
    "панфер": "панферов",
    "панфёр": "панферов",
    "панферов": "панферов",
    "панфёров": "панферов",
    "писарь": "писарев",
    "писарев": "писарев",
    "поляшов": "поляшов",
    "поляшёв": "поляшов",
    "шева": "шевченко",
    "шевченко": "шевченко",
    "яковлев": "яковлев",
}
_DEFAULT_CELEBRATION = "другие"


def get_celebration_video_path(surname: str) -> str:
    """
    Resolve a player's celebration clip.

    Args:
        surname: Surname or nickname, any case

    Returns:
        Relative path to the clip; the shared one for unknown players.
    """
    key = (surname or "").strip().lower()
    clip = _CELEBRATION_BY_SURNAME.get(key, _DEFAULT_CELEBRATION)
    return f"{CELEBRATION_ROOT}/{clip}.mp4"


class ScoreTracker:
    """
    The running score for one match, plus the messages already posted about it.

    Deduplication lives here: `register` only reports a goal when the score
    actually moved forward, so two sources reporting the same score cannot
    produce two posts.
    """

    def __init__(self, our_score: int = 0, opponent_score: int = 0):
        self.our_score = our_score
        self.opponent_score = opponent_score
        self.history: List[str] = []

    @property
    def score(self) -> Tuple[int, int]:
        return (self.our_score, self.opponent_score)

    def register(self, our_score: int, opponent_score: int) -> Optional[str]:
        """
        Record a reported score.

        Returns:
            "ours" or "theirs" when this is a new goal, None when the score did
            not move forward (a repeat from another source, or a correction
            downwards, which we never announce).
        """
        if our_score > self.our_score:
            side = "ours"
        elif opponent_score > self.opponent_score:
            side = "theirs"
        else:
            return None

        self.our_score = our_score
        self.opponent_score = opponent_score
        return side

    def remember(self, message: str) -> None:
        """Append a posted message, keeping only the recent tail for GPT context."""
        self.history.append(message)
        if len(self.history) > HISTORY_LIMIT:
            self.history = self.history[-HISTORY_LIMIT:]


# One tracker per channel, shared by every source posting to that channel.
_channel_trackers: Dict[str, ScoreTracker] = {}


def get_channel_tracker(channel_id: str) -> ScoreTracker:
    """Get (creating if needed) the tracker shared by all sources for a channel."""
    key = str(channel_id)
    if key not in _channel_trackers:
        _channel_trackers[key] = ScoreTracker()
    return _channel_trackers[key]


def reset_channel_tracker(channel_id: str, our_score: int = 0, opponent_score: int = 0) -> ScoreTracker:
    """Start a fresh match on a channel, optionally from a score already reached."""
    tracker = ScoreTracker(our_score, opponent_score)
    _channel_trackers[str(channel_id)] = tracker
    return tracker


class GoalAnnouncer:
    """Turns a reported score into a channel post."""

    def __init__(
        self,
        app: Application,
        channel_id: str,
        user_id: Optional[int] = None,
        gpt_service=None,
        tracker: Optional[ScoreTracker] = None,
    ):
        """
        Args:
            app: Telegram application
            channel_id: Channel to post into
            user_id: Owner's chat id, for error notifications
            gpt_service: GPTCommentaryService, or None to always use templates
            tracker: Explicit tracker. Defaults to the shared per-channel one;
                pass your own to replay a match without touching live state.
        """
        self.app = app
        self.channel_id = channel_id
        self.user_id = user_id
        self.gpt_service = gpt_service
        self._tracker = tracker
        self._video_root = CELEBRATION_ROOT

    @property
    def tracker(self) -> ScoreTracker:
        if self._tracker is not None:
            return self._tracker
        return get_channel_tracker(self.channel_id)

    async def announce(
        self,
        our_score: int,
        opponent_score: int,
        scorer_name: Optional[str] = None,
        scorer_surname: Optional[str] = None,
    ) -> Optional[str]:
        """
        Announce a score change, if it is one.

        Args:
            our_score: Bauman United's goals
            opponent_score: The opponent's goals
            scorer_name: Full name, when the source knows it
            scorer_surname: Surname/nickname, used for GPT and the celebration clip

        Returns:
            The posted message, or None when nothing was posted because the
            score had already been announced.
        """
        tracker = self.tracker
        side = tracker.register(our_score, opponent_score)
        if side is None:
            logger.debug(
                f"Score {our_score}-{opponent_score} already announced or not a goal, skipping"
            )
            return None

        is_our_goal = side == "ours"
        score_text = f"{our_score}-{opponent_score}"

        message = await self._build_message(
            tracker.history, score_text, is_our_goal, scorer_name, scorer_surname
        )
        await self._post(message, is_our_goal, scorer_surname)
        tracker.remember(message)
        logger.info(f"Posted score update: {message}")
        return message

    async def _build_message(
        self,
        history: List[str],
        score_text: str,
        is_our_goal: bool,
        scorer_name: Optional[str],
        scorer_surname: Optional[str],
    ) -> str:
        """GPT commentary when available, a plain template otherwise."""
        if self.gpt_service and self.gpt_service.is_available():
            try:
                generated = await self.gpt_service.generate_commentary(
                    history,
                    score_text,
                    is_our_goal=is_our_goal,
                    scorer_surname=scorer_surname,
                )
                if generated:
                    return generated
            except Exception as e:
                logger.error(f"GPT commentary failed, using template: {e}")

        return self._template_message(score_text, is_our_goal, scorer_name, scorer_surname)

    @staticmethod
    def _template_message(
        score_text: str,
        is_our_goal: bool,
        scorer_name: Optional[str],
        scorer_surname: Optional[str],
    ) -> str:
        """Wording used when GPT is off or failed."""
        if not is_our_goal:
            return f"Пропускаем. Счет: {score_text}"

        scorer = scorer_name or (scorer_surname.capitalize() if scorer_surname else None)
        if scorer:
            return f"⚽ Забиваем! Гол забил {scorer}. Счет: {score_text}"
        return f"⚽ Забиваем! Счет: {score_text}"

    async def _post(self, message: str, is_our_goal: bool, scorer_surname: Optional[str]) -> None:
        """Post to the channel, with the celebration clip when we have one."""
        video_path = None
        if is_our_goal and scorer_surname:
            candidate = get_celebration_video_path(scorer_surname)
            candidate = candidate.replace(CELEBRATION_ROOT, self._video_root, 1)
            if os.path.exists(candidate):
                video_path = candidate
            else:
                logger.warning(f"Celebration video missing: {candidate}")

        try:
            if video_path:
                with open(video_path, "rb") as video:
                    await self.app.bot.send_video(
                        chat_id=self.channel_id,
                        video=video,
                        caption=message,
                        parse_mode="HTML",
                    )
            else:
                await self.app.bot.send_message(
                    chat_id=self.channel_id,
                    text=message,
                    parse_mode="HTML",
                )
        except Exception as e:
            logger.error(f"Error posting goal to channel: {e}")
