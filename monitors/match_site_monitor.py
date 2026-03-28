"""
Match site monitor for periodic checking of match pages.

Fetches a match page periodically and posts new goal events
to the Telegram channel with GPT-generated commentary.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Set

from telegram.ext import Application

from config.settings import Config
from utils.error_notifier import send_error_notification
from utils.match_parser import GoalEvent, fetch_match_html, parse_match_page

logger = logging.getLogger(__name__)


def _get_celebration_video_path(surname_lower: str) -> Optional[str]:
    """Standalone copy of the celebration-video lookup."""
    if surname_lower in ["богомолов", "багич"]:
        return "celebrations/богомолов.mp4"
    elif surname_lower == "заночуев":
        return "celebrations/заночуев.mp4"
    elif surname_lower in ["панфер", "панфёр", "панферов", "панфёров"]:
        return "celebrations/панферов.mp4"
    elif surname_lower in ["писарь", "писарев"]:
        return "celebrations/писарев.mp4"
    elif surname_lower in ["шева", "шевченко"]:
        return "celebrations/шевченко.mp4"
    else:
        return "celebrations/другие.mp4"


class MatchSiteMonitor:
    """Monitor a match page for new goals and post them to Telegram channel."""

    def __init__(
        self,
        schedule_id: str,
        match_url: str,
        game_datetime_utc: datetime,
        channel_id: str,
        app: Application,
        user_id: int,
        seen_scores: Optional[Set[str]] = None,
    ):
        self.schedule_id = schedule_id
        self.match_url = match_url
        self.game_datetime_utc = game_datetime_utc
        self.channel_id = channel_id
        self.app = app
        self.user_id = user_id
        self.is_active = True
        self.seen_scores: Set[str] = seen_scores if seen_scores is not None else set()
        self.message_history: List[str] = []

        self.gpt_service = None
        try:
            from services.gpt_service import GPTCommentaryService

            async def gpt_error_notifier(service_name, request_info, error_code, error_message):
                await send_error_notification(
                    app, user_id, service_name, request_info, error_code, error_message,
                )

            self.gpt_service = GPTCommentaryService(error_notifier=gpt_error_notifier)
        except Exception as e:
            logger.warning(f"GPT service not available for site monitor: {e}")

    # ------------------------------------------------------------------
    # Goal checking
    # ------------------------------------------------------------------

    async def check_for_new_goals(self):
        """Fetch the match page and post any new goals to the channel."""
        try:
            html = await asyncio.get_event_loop().run_in_executor(
                None, fetch_match_html, self.match_url,
            )
            parsed = parse_match_page(html)
            goals = parsed.goals
        except Exception as e:
            logger.error(f"Site monitor {self.schedule_id}: error fetching page — {e}")
            return

        new_goals = [g for g in goals if g.score not in self.seen_scores]
        if not new_goals:
            return

        logger.info(f"Site monitor {self.schedule_id}: {len(new_goals)} new goal(s)")

        for goal in new_goals:
            message = await self._generate_message(goal)
            await self._post_to_channel(goal, message)
            self.seen_scores.add(goal.score)
            self.message_history.append(message)
            if len(self.message_history) > 10:
                self.message_history = self.message_history[-10:]

        from utils.game_schedule import update_game_seen_scores
        update_game_seen_scores(self.schedule_id, list(self.seen_scores))

    async def _generate_message(self, goal: GoalEvent) -> str:
        score_normalized = goal.score.replace(" ", "").replace(":", "-")

        if self.gpt_service and self.gpt_service.is_available():
            gpt_msg = await self.gpt_service.generate_commentary(
                self.message_history,
                score_normalized,
                is_our_goal=goal.is_our_goal,
                scorer_surname=goal.scorer_surname,
            )
            if gpt_msg:
                return gpt_msg

        if goal.is_our_goal:
            if goal.scorer_name:
                return f"⚽ Забиваем! Гол забил {goal.scorer_name}. Счет: {score_normalized}"
            return f"⚽ Забиваем! Счет: {score_normalized}"
        return f"Пропускаем. Счет: {score_normalized}"

    async def _post_to_channel(self, goal: GoalEvent, message: str):
        video_path = None
        if goal.is_our_goal and goal.scorer_surname:
            video_path = _get_celebration_video_path(goal.scorer_surname.lower())

        try:
            if video_path:
                try:
                    await self.app.bot.send_video(
                        chat_id=self.channel_id,
                        video=open(video_path, "rb"),
                        caption=message,
                        parse_mode="HTML",
                    )
                except FileNotFoundError:
                    await self.app.bot.send_message(
                        chat_id=self.channel_id,
                        text=message,
                        parse_mode="HTML",
                    )
            else:
                await self.app.bot.send_message(
                    chat_id=self.channel_id,
                    text=message,
                    parse_mode="HTML",
                )
        except Exception as e:
            logger.error(f"Site monitor {self.schedule_id}: error posting to channel — {e}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_monitoring(self):
        """Wait for the monitoring window, then poll every 60 s."""
        start_time = self.game_datetime_utc - timedelta(minutes=5)
        end_time = self.game_datetime_utc + timedelta(hours=2)

        logger.info(
            f"Site monitor {self.schedule_id}: window "
            f"{start_time.isoformat()} → {end_time.isoformat()}"
        )

        await self._send_user_notification(
            f"🌐 Мониторинг сайта запущен\n"
            f"🔗 {self.match_url}\n"
            f"⏱ Проверка каждые 60 секунд\n"
            f"🕐 Окно: −5 мин … +2 ч от времени игры"
        )

        # Wait until 5 min before game (check is_active every 30 s)
        while self.is_active:
            now = datetime.now(timezone.utc)
            if now >= start_time:
                break
            await asyncio.sleep(30)

        if not self.is_active:
            logger.info(f"Site monitor {self.schedule_id}: cancelled before window")
            self._cleanup()
            return

        logger.info(f"Site monitor {self.schedule_id}: window open, starting polling")

        while self.is_active:
            now = datetime.now(timezone.utc)
            if now > end_time:
                break
            await self.check_for_new_goals()
            await asyncio.sleep(60)

        logger.info(f"Site monitor {self.schedule_id}: finished")
        self._cleanup()

    def _cleanup(self):
        try:
            from handlers.telegram_commands import get_active_site_monitors
            monitors = get_active_site_monitors()
            if self.schedule_id in monitors:
                del monitors[self.schedule_id]
        except Exception:
            logger.debug("Site monitor cleanup failed", exc_info=True)

    async def _send_user_notification(self, text: str):
        try:
            await self.app.bot.send_message(
                chat_id=self.user_id, text=text, parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Site monitor notification error: {e}")
