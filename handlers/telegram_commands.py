"""
Telegram command handlers for the VK Translation Monitor Bot.

This module contains all the command handlers that process user commands
and interact with the monitoring system.
"""

import asyncio
import re
import logging
import hashlib
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Dict, Any, Optional, List
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, Application

from config.settings import Config
from utils.url_parser import extract_group_id
from monitors.translation_monitor import VKTranslationMonitor
from monitors.group_stream_monitor import VKGroupStreamMonitor
from utils.game_schedule import (
    GameSchedule,
    add_game_schedule,
    delete_game_schedule,
    get_game_schedule,
    list_game_schedules,
    update_game_parse_mode,
    update_game_seen_scores,
)
from utils.match_parser import GoalEvent, fetch_match_html, format_match_teams_summary, parse_match_page

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

active_translations: Dict[str, VKTranslationMonitor] = {}
active_site_monitors: Dict[str, Any] = {}  # schedule_id → MatchSiteMonitor
group_stream_monitor: VKGroupStreamMonitor = None

# User‑flow pending keys stored in context.user_data
GAME_DAY_PENDING_KEY = "pending_game_weekday"
MATCH_URL_PENDING_KEY = "pending_match_url_schedule_id"

SERBIA_TZ = ZoneInfo("Europe/Belgrade")

# Global mapping to store URL hashes (for callback queries)
url_hash_to_url: Dict[str, str] = {}


# ===================================================================
# /start
# ===================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    await update.message.reply_text(
        "👋 Welcome to VK Translation Monitor Bot!\n\n"
        "Commands:\n"
        "/monitor <vk_translation_url> - Start monitoring a VK translation\n"
        "/stop <vk_translation_url> - Stop monitoring a translation\n"
        "/list - List active translations being monitored\n"
        "/group_status - Check VK group monitoring status\n"
        "/catch_existing - Start monitoring any currently live streams\n\n"
        "/set_game - Schedule a game time (controls VK stream monitoring window)\n"
        "/games - List scheduled games and delete them\n\n"
        "/match <url> - Parse match page and post goal commentary to channel\n\n"
        "Examples:\n"
        "/monitor https://vk.com/video-123456789_456123789\n"
        "/match https://bauman_league.join.football/match/5580043"
    )


# ===================================================================
# /set_game  (weekday → time flow)
# ===================================================================

def _weekday_to_label(weekday_index: int) -> str:
    labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    return labels[weekday_index] if 0 <= weekday_index <= 6 else str(weekday_index)


def _parse_hh_mm(text: str) -> Optional[dtime]:
    norm = text.strip().replace("-", ":")
    match = re.match(r"^(\d{1,2}):(\d{2})$", norm)
    if not match:
        return None
    hh = int(match.group(1))
    mm = int(match.group(2))
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        return None
    return dtime(hour=hh, minute=mm)


def _compute_next_weekday_datetime(now: datetime, weekday_index: int, at_time: dtime) -> datetime:
    for i in range(0, 14):
        candidate_date = (now + timedelta(days=i)).date()
        if candidate_date.weekday() != weekday_index:
            continue
        candidate_dt = datetime.combine(candidate_date, at_time).replace(tzinfo=SERBIA_TZ)
        if candidate_dt >= now:
            return candidate_dt
    candidate_date = (now + timedelta(days=7)).date()
    while candidate_date.weekday() != weekday_index:
        candidate_date = candidate_date + timedelta(days=1)
    return datetime.combine(candidate_date, at_time).replace(tzinfo=SERBIA_TZ)


async def set_game_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show weekday buttons to schedule the game monitoring window."""
    days = list(range(7))
    keyboard = [
        [InlineKeyboardButton(_weekday_to_label(i), callback_data=f"set_game_day:{i}")]
        for i in days
    ]
    await update.message.reply_text(
        "Выберите день игры:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def set_game_day_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle selected weekday from inline keyboard."""
    query = update.callback_query
    await query.answer()
    if not query.data or not query.data.startswith("set_game_day:"):
        return

    try:
        weekday_index = int(query.data.split(":", 1)[1])
    except Exception:
        await query.edit_message_text("❌ Неверный день. Попробуйте еще раз: /set_game")
        return

    if weekday_index < 0 or weekday_index > 6:
        await query.edit_message_text("❌ Неверный день. Попробуйте еще раз: /set_game")
        return

    context.user_data[GAME_DAY_PENDING_KEY] = weekday_index
    await query.edit_message_text(
        "Введите время в формате `hh:mm` или `hh-mm`.\nНапример: `21:30`",
        parse_mode="Markdown",
    )


# ===================================================================
# /games  (list + delete + change parse type)
# ===================================================================

def _build_games_display(
    schedules: List[GameSchedule],
    header: str = "📅 Сохраненные игры:\n",
) -> tuple:
    """Return (text, InlineKeyboardMarkup | None)."""
    if not schedules:
        return header + "\nСейчас сохраненных игр нет.", None

    lines = [header]
    keyboard = []
    for idx, s in enumerate(schedules, start=1):
        dt = s.game_datetime
        lines.append(f"{idx}. {dt.strftime('%Y-%m-%d %H:%M')} {s.parse_mode_label}")
        if s.parse_mode == "site" and s.match_url:
            display_url = s.match_url if len(s.match_url) <= 45 else s.match_url[:42] + "..."
            lines.append(f"   🔗 {display_url}")
        keyboard.append([
            InlineKeyboardButton(f"🗑 Удалить {idx}", callback_data=f"del_game:{s.id}"),
            InlineKeyboardButton(f"⚙️ Тип {idx}", callback_data=f"game_type:{s.id}"),
        ])

    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


async def games_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all saved game datetimes and provide delete / type buttons."""
    schedules = list_game_schedules()
    if not schedules:
        await update.message.reply_text("📭 Нет сохраненных дат игры. Используйте /set_game")
        return

    text, markup = _build_games_display(schedules)
    await update.message.reply_text(text, reply_markup=markup)


async def delete_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a chosen saved datetime."""
    query = update.callback_query
    await query.answer()
    if not query.data or not query.data.startswith("del_game:"):
        return

    schedule_id = query.data.split(":", 1)[1]

    # Stop any running site monitor for this game
    if schedule_id in active_site_monitors:
        active_site_monitors[schedule_id].is_active = False
        del active_site_monitors[schedule_id]

    ok = delete_game_schedule(schedule_id)
    if not ok:
        await query.edit_message_text("❌ Не удалось удалить запись (возможно, она уже удалена).")
        return

    schedules = list_game_schedules()
    if not schedules:
        await query.edit_message_text("✅ Удалено. Сейчас сохраненных игр нет.")
        return

    text, markup = _build_games_display(schedules, "✅ Удалено.\n\n")
    await query.edit_message_text(text, reply_markup=markup)


# ===================================================================
# Change parse type for a game
# ===================================================================

async def game_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show parse-mode selection buttons for a game."""
    query = update.callback_query
    await query.answer()
    if not query.data or not query.data.startswith("game_type:"):
        return

    schedule_id = query.data.split(":", 1)[1]
    schedule = get_game_schedule(schedule_id)
    if not schedule:
        await query.edit_message_text("❌ Игра не найдена.")
        return

    dt = schedule.game_datetime
    keyboard = [[
        InlineKeyboardButton("📺 VK комментарии", callback_data=f"set_parse:comments:{schedule_id}"),
        InlineKeyboardButton("🌐 Парсинг сайта", callback_data=f"set_parse:site:{schedule_id}"),
    ]]

    await query.edit_message_text(
        f"Игра: {dt.strftime('%Y-%m-%d %H:%M')}\n"
        f"Текущий режим: {schedule.parse_mode_label}\n\n"
        "Выберите тип мониторинга:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def set_parse_mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle parse-mode selection (comments or site)."""
    query = update.callback_query
    await query.answer()
    if not query.data or not query.data.startswith("set_parse:"):
        return

    parts = query.data.split(":", 2)
    if len(parts) != 3:
        return
    _, mode, schedule_id = parts

    if mode == "comments":
        # Stop running site monitor if any
        if schedule_id in active_site_monitors:
            active_site_monitors[schedule_id].is_active = False
            del active_site_monitors[schedule_id]

        update_game_parse_mode(schedule_id, "comments")
        await query.edit_message_text(
            "✅ Режим изменен: 📺 VK комментарии\n"
            "Мониторинг через VK комментарии будет запущен автоматически в окно игры."
        )

    elif mode == "site":
        # Stop VK comment monitors — site mode doesn't need them
        if active_translations:
            for monitor in list(active_translations.values()):
                monitor.is_active = False
            active_translations.clear()
            logger.info("Stopped VK comment monitors on user switch to site mode")

        context.user_data[MATCH_URL_PENDING_KEY] = schedule_id
        await query.edit_message_text(
            "🌐 Отправьте ссылку на страницу матча.\n"
            "Например: https://bauman_league.join.football/match/5580043"
        )


# ===================================================================
# Unified text input handler (game time + match URL)
# ===================================================================

async def game_time_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Catch‑all for plain text messages.
    Dispatches to the appropriate pending flow (match URL or game time).
    """
    # 1. Pending match URL (site parsing mode)?
    if MATCH_URL_PENDING_KEY in context.user_data:
        await _handle_match_url_input(update, context)
        return

    # 2. Pending game time input?
    if GAME_DAY_PENDING_KEY not in context.user_data:
        return
    if not update.message or not update.message.text:
        return

    weekday_index = context.user_data.pop(GAME_DAY_PENDING_KEY, None)
    if weekday_index is None:
        return

    parsed_time = _parse_hh_mm(update.message.text)
    if not parsed_time:
        await update.message.reply_text(
            "❌ Не понял время. Введите `hh:mm` или `hh-mm`, например `21:30`."
        )
        context.user_data[GAME_DAY_PENDING_KEY] = weekday_index
        return

    now = datetime.now(SERBIA_TZ)
    game_dt = _compute_next_weekday_datetime(now, weekday_index, parsed_time)
    schedule = add_game_schedule(game_dt)

    window_start = game_dt - timedelta(minutes=30)
    window_end = game_dt + timedelta(hours=2)

    await update.message.reply_text(
        "✅ Дата и время сохранены.\n\n"
        f"Игра: {game_dt.strftime('%Y-%m-%d %H:%M')}\n"
        f"Мониторинг включится: {window_start.strftime('%Y-%m-%d %H:%M')}\n"
        f"Мониторинг выключится: {window_end.strftime('%Y-%m-%d %H:%M')}\n"
        f"(id: {schedule.id[:8]})"
    )


async def _handle_match_url_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process a match‑page URL sent by the user after choosing 'site' mode."""
    schedule_id = context.user_data.pop(MATCH_URL_PENDING_KEY, None)
    if not schedule_id:
        return
    if not update.message or not update.message.text:
        return

    match_url = update.message.text.strip()

    await update.message.reply_text("⏳ Проверяю ссылку...")

    # Try to parse the page
    try:
        html = await asyncio.get_event_loop().run_in_executor(None, fetch_match_html, match_url)
        parsed = parse_match_page(html)
    except Exception as e:
        logger.error(f"Error parsing match page for schedule {schedule_id}: {e}")
        await update.message.reply_text(
            f"❌ Ошибка при разборе страницы: {e}\n"
            "Попробуйте отправить другую ссылку."
        )
        context.user_data[MATCH_URL_PENDING_KEY] = schedule_id
        return

    goals = parsed.goals

    # Persist the parse mode + URL + already-seen scores
    seen_scores = [g.score for g in goals]
    update_game_parse_mode(schedule_id, "site", match_url)
    update_game_seen_scores(schedule_id, seen_scores)

    # Post existing goals to channel
    if goals:
        config = Config()
        try:
            await _post_goals_to_channel(
                goals, context.application, config.TELEGRAM_CHANNEL_ID, update.effective_user.id,
            )
        except Exception as e:
            logger.error(f"Error posting initial goals: {e}")

    # Start a site monitor
    schedule = get_game_schedule(schedule_id)
    if schedule:
        _start_site_monitor_for_schedule(schedule, context.application, update.effective_user.id)

    if goals:
        goal_text = f"⚽ Найдено {len(goals)} гол(ов), опубликовано в канал."
    elif not parsed.timeline_present:
        goal_text = (
            "📋 Событий на странице пока нет (матч ещё не начался или лента событий недоступна)."
        )
    else:
        goal_text = "⚽ Голов пока нет."

    teams_text = format_match_teams_summary(parsed)

    await update.message.reply_text(
        f"✅ Ссылка сохранена. Режим: 🌐 Парсинг сайта\n"
        f"{teams_text}\n"
        f"{goal_text}\n"
        f"🕐 Мониторинг: за 5 мин до игры, каждые 60 сек, в течение 2 часов."
    )


# ===================================================================
# Shared goal-posting helper
# ===================================================================

async def _post_goals_to_channel(
    goals: List[GoalEvent],
    app: Application,
    channel_id: str,
    user_id: int,
) -> List[str]:
    """Generate commentary for each goal and post it to the channel.

    Returns the list of posted message texts (for GPT history).
    """
    config = Config()
    gpt_service = None
    if config.is_openai_configured:
        try:
            from services.gpt_service import GPTCommentaryService
            from utils.error_notifier import send_error_notification as _send_err

            async def gpt_err(sn, ri, ec, em):
                await _send_err(app, user_id, sn, ri, ec, em)

            gpt_service = GPTCommentaryService(error_notifier=gpt_err)
        except Exception:
            pass

    message_history: list[str] = []

    for goal in goals:
        score_normalized = goal.score.replace(" ", "").replace(":", "-")

        # Generate message
        if gpt_service and gpt_service.is_available():
            gpt_msg = await gpt_service.generate_commentary(
                message_history,
                score_normalized,
                is_our_goal=goal.is_our_goal,
                scorer_surname=goal.scorer_surname,
            )
            if gpt_msg:
                message = gpt_msg
            elif goal.is_our_goal:
                message = f"⚽ Забиваем! Гол забил {goal.scorer_name}. Счет: {score_normalized}"
            else:
                message = f"Пропускаем. Счет: {score_normalized}"
        elif goal.is_our_goal:
            message = f"⚽ Забиваем! Гол забил {goal.scorer_name}. Счет: {score_normalized}"
        else:
            message = f"Пропускаем. Счет: {score_normalized}"

        # Celebration video
        video_path = None
        if goal.is_our_goal and goal.scorer_surname:
            from monitors.translation_monitor import VKTranslationMonitor as _TM
            video_path = _TM._get_celebration_video_path(None, goal.scorer_surname.lower())

        # Post
        try:
            if video_path:
                try:
                    await app.bot.send_video(
                        chat_id=channel_id,
                        video=open(video_path, "rb"),
                        caption=message,
                        parse_mode="HTML",
                    )
                except FileNotFoundError:
                    await app.bot.send_message(
                        chat_id=channel_id, text=message, parse_mode="HTML",
                    )
            else:
                await app.bot.send_message(
                    chat_id=channel_id, text=message, parse_mode="HTML",
                )
        except Exception as e:
            logger.error(f"Error posting goal to channel: {e}")

        message_history.append(message)
        if len(message_history) > 10:
            message_history = message_history[-10:]

        await asyncio.sleep(2)

    return message_history


# ===================================================================
# /match  (one-shot parse & post)
# ===================================================================

async def match_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /match command — parse a match page and post goal commentary to channel."""
    if not context.args:
        await update.message.reply_text(
            "❌ Укажите ссылку на матч\n"
            "Пример: /match https://bauman_league.join.football/match/5580043"
        )
        return

    match_url = context.args[0]

    await update.message.reply_text("⏳ Загружаю страницу матча...")

    try:
        html = await asyncio.get_event_loop().run_in_executor(None, fetch_match_html, match_url)
        parsed = parse_match_page(html)
    except Exception as e:
        logger.error(f"Error parsing match page: {e}")
        await update.message.reply_text(f"❌ Ошибка при разборе страницы: {e}")
        return

    goals = parsed.goals
    teams_text = format_match_teams_summary(parsed)

    if not goals:
        if not parsed.timeline_present:
            note = (
                "📋 Событий на странице пока нет (матч ещё не начался или лента событий недоступна)."
            )
        else:
            note = "⚠️ Голов на странице матча не найдено."
        await update.message.reply_text(f"{note}\n\n{teams_text}")
        return

    await update.message.reply_text(f"⚽ Найдено {len(goals)} гол(ов). Генерирую посты...\n\n{teams_text}")

    config = Config()
    try:
        await _post_goals_to_channel(
            goals, context.application, config.TELEGRAM_CHANNEL_ID, update.effective_user.id,
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при отправке в канал: {e}")
        return

    await update.message.reply_text(f"✅ Опубликовано {len(goals)} пост(ов) в канал")


# ===================================================================
# /monitor, /stop, /list, /group_status, /catch_existing
# ===================================================================

async def monitor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /monitor command."""
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide a VK translation URL\n"
            "Example: /monitor https://vk.com/video-123456789_456123789"
        )
        return

    translation_url = context.args[0]

    if translation_url in active_translations:
        await update.message.reply_text("⚠️ Already monitoring this translation")
        return

    try:
        config = Config()
        monitor = VKTranslationMonitor(
            translation_url,
            config.TELEGRAM_CHANNEL_ID,
            context.application,
            update.effective_user.id,
        )
        active_translations[translation_url] = monitor

        await update.message.reply_text("✅ Starting to monitor the translation...")
        asyncio.create_task(monitor.start_monitoring())

    except ValueError as e:
        await update.message.reply_text(f"❌ Error: {e}")
    except Exception as e:
        logger.error(f"Error starting monitor: {e}")
        await update.message.reply_text(f"❌ Error starting monitor: {e}")


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stop command."""
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide a VK translation URL\n"
            "Example: /stop https://vk.com/video-123456789_456123789"
        )
        return

    translation_url = context.args[0]

    if translation_url not in active_translations:
        await update.message.reply_text("⚠️ Not monitoring this translation")
        return

    monitor = active_translations[translation_url]
    monitor.is_active = False

    await update.message.reply_text("✅ Stopped monitoring the translation")


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list command."""
    if not active_translations:
        await update.message.reply_text("📭 No active translations being monitored")
        return

    message = "📊 Active translations:\n\n"
    keyboard = []

    for i, url in enumerate(active_translations.keys(), 1):
        display_url = url if len(url) <= 50 else url[:47] + "..."
        message += f"{i}. {display_url}\n"

        url_hash = hashlib.md5(url.encode()).hexdigest()
        callback_data = f"remove:{url_hash}"
        url_hash_to_url[url_hash] = url

        keyboard.append([InlineKeyboardButton(f"🗑️ Remove {i}", callback_data=callback_data)])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup)


async def group_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /group_status command."""
    config = Config()

    if not config.is_group_monitoring_configured:
        await update.message.reply_text("❌ VK group monitoring is not configured")
        return

    if not group_stream_monitor:
        await update.message.reply_text("❌ VK group monitoring is not running")
        return

    if group_stream_monitor.is_active:
        status = "✅ Active"
        streams_count = len(group_stream_monitor.seen_streams)
        message = (
            f"📊 <b>VK Group Monitoring Status</b>\n\n"
            f"🔍 Group ID: {config.VK_GROUP}\n"
            f"📈 Status: {status}\n"
            f"📺 Streams found: {streams_count}\n"
            f"⏱ Check interval: 15 seconds"
        )
    else:
        message = "❌ VK group monitoring is not active"

    await update.message.reply_text(message, parse_mode='HTML')


async def catch_existing_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /catch_existing command — start monitoring any currently live streams."""
    config = Config()

    if not config.is_group_monitoring_configured:
        await update.message.reply_text("❌ VK group monitoring is not configured")
        return

    if not group_stream_monitor:
        await update.message.reply_text("❌ VK group monitoring is not running")
        return

    try:
        extracted_group_id = extract_group_id(config.VK_GROUP)
        videos = await group_stream_monitor.vk_client.get_group_videos(extracted_group_id, count=20)

        if not videos:
            await update.message.reply_text("❌ No videos found in group or access denied")
            return

        live_streams = [v for v in videos if group_stream_monitor.vk_client.is_live_stream(v)]

        if not live_streams:
            await update.message.reply_text("❌ No live streams found in the group")
            return

        message = f"🔴 Found {len(live_streams)} live stream(s):\n\n"
        started_monitoring = 0

        for stream in live_streams:
            stream_url = group_stream_monitor.vk_client.get_video_url(stream)
            stream_title = stream.get('title', 'Live Stream')
            video_id = group_stream_monitor.vk_client.get_video_id(stream)

            message += f"📺 {stream_title}\n🔗 {stream_url}\n\n"

            if stream_url not in active_translations:
                try:
                    await context.application.bot.send_message(
                        chat_id=config.TELEGRAM_CHANNEL_ID,
                        text=f"Ссылка на трансляцию матча: {stream_url}",
                        parse_mode='HTML',
                    )
                except Exception as e:
                    logger.error(f"Error sending channel message: {e}")

                monitor = VKTranslationMonitor(
                    stream_url,
                    config.TELEGRAM_CHANNEL_ID,
                    context.application,
                    update.effective_user.id,
                )
                active_translations[stream_url] = monitor
                asyncio.create_task(monitor.start_monitoring())
                started_monitoring += 1
                group_stream_monitor.seen_streams.add(video_id)
            else:
                message += f"⚠️ Already monitoring: {stream_title}\n\n"

        if started_monitoring > 0:
            message += f"✅ Started monitoring {started_monitoring} stream(s)"
        else:
            message += "ℹ️ All streams are already being monitored"

        await update.message.reply_text(message, parse_mode='HTML')

    except Exception as e:
        logger.error(f"Error in catch_existing: {e}")
        await update.message.reply_text(f"❌ Error: {e}")


# ===================================================================
# Callback: remove translation
# ===================================================================

async def remove_translation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback query for removing a translation."""
    query = update.callback_query
    await query.answer()

    if not query.data or not query.data.startswith("remove:"):
        await query.edit_message_text("❌ Invalid callback data")
        return

    url_hash = query.data.split(":", 1)[1]
    translation_url = url_hash_to_url.get(url_hash)

    if not translation_url:
        for url in active_translations.keys():
            if hashlib.md5(url.encode()).hexdigest() == url_hash:
                translation_url = url
                break

    if not translation_url:
        await query.edit_message_text("❌ Translation not found")
        return

    if translation_url not in active_translations:
        await query.edit_message_text("⚠️ Translation is not being monitored")
        return

    monitor = active_translations[translation_url]
    monitor.is_active = False
    del active_translations[translation_url]

    if url_hash in url_hash_to_url:
        del url_hash_to_url[url_hash]

    removed_url = translation_url if len(translation_url) <= 50 else translation_url[:47] + "..."

    if active_translations:
        url_hash_to_url.clear()

        message = "📊 Active translations:\n\n"
        keyboard = []

        for i, url in enumerate(active_translations.keys(), 1):
            display_url = url if len(url) <= 50 else url[:47] + "..."
            message += f"{i}. {display_url}\n"

            new_hash = hashlib.md5(url.encode()).hexdigest()
            callback_data = f"remove:{new_hash}"
            url_hash_to_url[new_hash] = url

            keyboard.append([InlineKeyboardButton(f"🗑️ Remove {i}", callback_data=callback_data)])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(message, reply_markup=reply_markup)
    else:
        await query.edit_message_text(
            f"✅ Removed translation:\n{removed_url}\n\n"
            f"📭 No active translations remaining"
        )


# ===================================================================
# Site monitor helpers
# ===================================================================

def _start_site_monitor_for_schedule(
    schedule: GameSchedule,
    app: Application,
    user_id: int,
):
    """Create and start a MatchSiteMonitor as a background task."""
    from monitors.match_site_monitor import MatchSiteMonitor

    if schedule.id in active_site_monitors:
        logger.info(f"Site monitor already running for schedule {schedule.id}")
        return

    if not schedule.match_url:
        logger.warning(f"No match_url for schedule {schedule.id}, cannot start site monitor")
        return

    end_time = schedule.game_datetime_utc + timedelta(hours=2)
    now = datetime.now(timezone.utc)
    if now > end_time:
        logger.info(f"Game window already closed for schedule {schedule.id}")
        return

    config = Config()
    monitor = MatchSiteMonitor(
        schedule_id=schedule.id,
        match_url=schedule.match_url,
        game_datetime_utc=schedule.game_datetime_utc,
        channel_id=config.TELEGRAM_CHANNEL_ID,
        app=app,
        user_id=user_id,
        seen_scores=set(schedule.seen_scores),
    )
    active_site_monitors[schedule.id] = monitor
    asyncio.create_task(monitor.start_monitoring())
    logger.info(f"Started site monitor for schedule {schedule.id}")


def start_pending_site_monitors(app: Application, user_id: int):
    """Boot-time helper: start monitors for all site-mode games that are still valid."""
    now = datetime.now(timezone.utc)
    for schedule in list_game_schedules():
        if schedule.parse_mode != "site" or not schedule.match_url:
            continue
        end_time = schedule.game_datetime_utc + timedelta(hours=2)
        if now > end_time:
            continue
        _start_site_monitor_for_schedule(schedule, app, user_id)


# ===================================================================
# Accessors used by other modules
# ===================================================================

def get_active_translations() -> Dict[str, VKTranslationMonitor]:
    return active_translations


def get_active_site_monitors() -> Dict[str, Any]:
    return active_site_monitors


def get_group_stream_monitor() -> VKGroupStreamMonitor:
    return group_stream_monitor


def set_group_stream_monitor(monitor: VKGroupStreamMonitor):
    global group_stream_monitor
    group_stream_monitor = monitor
