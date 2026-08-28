"""
Manual Telegram translation mode.

`/start_translation` makes the bot treat the owner's plain-text messages the way
it treats VK live-stream comments: a message like "2-1 Шевченко" becomes a goal
announcement in the channel, with GPT commentary and a celebration clip.
`/end_translation` stops it.

Runs alongside the VK and site monitors. They all share one ScoreTracker per
channel, so whichever source reports a goal first wins and the others stay quiet
instead of double-posting it.
"""

import logging
import re
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from config.settings import Config
from services.goal_announcer import (
    GoalAnnouncer,
    get_channel_tracker,
    reset_channel_tracker,
)
from utils.manual_translation_store import (
    ManualTranslationState,
    clear_state,
    load_state,
    save_state,
)
from utils.url_parser import parse_score_comment

logger = logging.getLogger(__name__)

_START_SCORE_RE = re.compile(r"^(\d+)\s*[-:]\s*(\d+)$")


def _is_owner(update: Update, config: Config) -> bool:
    """
    Only the bot owner may drive the channel.

    The bot posts into a public channel, so without this anyone who finds it
    could push messages there.
    """
    user = update.effective_user
    if not user or not config.MY_ID:
        return False
    try:
        return int(user.id) == int(config.MY_ID)
    except (TypeError, ValueError):
        return False


def _build_announcer(app, config, user_id: Optional[int]) -> GoalAnnouncer:
    """Announcer wired to the channel tracker shared with the automatic monitors."""
    gpt_service = None
    if config.is_openai_configured:
        try:
            from services.gpt_service import GPTCommentaryService
            from utils.error_notifier import send_error_notification

            async def gpt_error_notifier(service_name, request_info, error_code, error_message):
                await send_error_notification(
                    app, user_id, service_name, request_info, error_code, error_message
                )

            gpt_service = GPTCommentaryService(error_notifier=gpt_error_notifier)
        except Exception as e:
            logger.warning(f"GPT service not available for manual translation: {e}")

    return GoalAnnouncer(app, config.TELEGRAM_CHANNEL_ID, user_id=user_id, gpt_service=gpt_service)


async def start_translation_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start_translation [score] — begin listening to the owner's messages."""
    config = Config()
    if not _is_owner(update, config):
        await update.message.reply_text("⛔ Эта команда доступна только владельцу бота.")
        return

    our_score, opponent_score = 0, 0
    if context.args:
        match = _START_SCORE_RE.match("".join(context.args).strip())
        if not match:
            await update.message.reply_text(
                "❌ Не понял счёт. Формат: `/start_translation 3-2`\n"
                "Без счёта трансляция начнётся с 0-0.",
                parse_mode="Markdown",
            )
            return
        our_score, opponent_score = int(match.group(1)), int(match.group(2))

    reset_channel_tracker(config.TELEGRAM_CHANNEL_ID, our_score, opponent_score)
    save_state(ManualTranslationState(
        our_score=our_score,
        opponent_score=opponent_score,
        channel_id=config.TELEGRAM_CHANNEL_ID,
    ))

    await update.message.reply_text(
        f"🎙 <b>Ручная трансляция включена</b>\n\n"
        f"Текущий счёт: {our_score}-{opponent_score}\n"
        f"Пиши сюда счёт в формате <code>2-1 Шевченко</code> — "
        f"я сгенерирую комментарий и опубликую в канал.\n\n"
        f"Остальные сообщения игнорирую.\n"
        f"Завершить: /end_translation",
        parse_mode="HTML",
    )
    logger.info(f"Manual translation started at {our_score}-{opponent_score}")


async def end_translation_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /end_translation — stop listening."""
    config = Config()
    if not _is_owner(update, config):
        await update.message.reply_text("⛔ Эта команда доступна только владельцу бота.")
        return

    state = load_state()
    if not state:
        await update.message.reply_text("ℹ️ Ручная трансляция и так не запущена.")
        return

    tracker = get_channel_tracker(config.TELEGRAM_CHANNEL_ID)
    our_score, opponent_score = tracker.score
    clear_state()

    await update.message.reply_text(
        f"🏁 <b>Ручная трансляция завершена</b>\n\n"
        f"Итоговый счёт: {our_score}-{opponent_score}",
        parse_mode="HTML",
    )
    logger.info(f"Manual translation ended at {our_score}-{opponent_score}")


async def handle_translation_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Process one plain-text message as a possible score report.

    Called from the catch-all text handler AFTER the pending /set_game and match-URL
    flows have had their turn — "21:30" is a valid score pattern, so a game time
    being entered must not be mistaken for a 21-30 scoreline.

    Returns:
        True when the message was consumed by the translation, False otherwise.
    """
    state = load_state()
    if not state:
        return False

    config = Config()
    if not _is_owner(update, config):
        return False

    if not update.message or not update.message.text:
        return False

    parsed = parse_score_comment(update.message.text)
    if not parsed:
        # Not a score — the owner is just typing. Stay quiet.
        return False

    our_score, opponent_score, surname = parsed

    announcer = _build_announcer(context.application, config, update.effective_user.id)
    message = await announcer.announce(
        our_score,
        opponent_score,
        scorer_surname=surname or None,
    )

    tracker = get_channel_tracker(config.TELEGRAM_CHANNEL_ID)
    state.our_score, state.opponent_score = tracker.score
    state.history = list(tracker.history)
    save_state(state)

    if message is None:
        await update.message.reply_text(
            f"⏭ Счёт {our_score}-{opponent_score} уже объявлен, пропускаю.\n"
            f"Текущий: {tracker.score[0]}-{tracker.score[1]}"
        )
    else:
        await update.message.reply_text(f"✅ Опубликовано: {message}")

    return True


def restore_session(app) -> None:
    """
    Re-seed the channel tracker after a restart.

    Called at boot: without it the score would silently reset to 0-0 mid-match.
    """
    state = load_state()
    if not state:
        return

    config = Config()
    channel_id = state.channel_id or config.TELEGRAM_CHANNEL_ID
    tracker = reset_channel_tracker(channel_id, state.our_score, state.opponent_score)
    tracker.history = list(state.history)
    logger.info(
        f"Restored manual translation at {state.our_score}-{state.opponent_score}"
    )
