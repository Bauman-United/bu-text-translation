"""
Telegram command handlers for the VK Translation Monitor Bot.

This module contains all the command handlers that process user commands
and interact with the monitoring system.
"""

import asyncio
import re
import logging
import hashlib
from datetime import datetime, time as dtime, timedelta
from typing import Dict, Any, Optional
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config.settings import Config
from utils.url_parser import extract_group_id
from monitors.translation_monitor import VKTranslationMonitor
from monitors.group_stream_monitor import VKGroupStreamMonitor
from utils.game_schedule import (
    add_game_schedule,
    delete_game_schedule,
    list_game_schedules,
)

logger = logging.getLogger(__name__)

# Global state for tracking translations and group monitoring
active_translations: Dict[str, VKTranslationMonitor] = {}
group_stream_monitor: VKGroupStreamMonitor = None

# User flow state:
# - after selecting a weekday via buttons, we ask for time input
GAME_DAY_PENDING_KEY = "pending_game_weekday"

SERBIA_TZ = ZoneInfo("Europe/Belgrade")


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
        "Example:\n"
        "/monitor https://vk.com/video-123456789_456123789"
    )


def _weekday_to_label(weekday_index: int) -> str:
    # 0=Mon ... 6=Sun
    labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    return labels[weekday_index] if 0 <= weekday_index <= 6 else str(weekday_index)


def _parse_hh_mm(text: str) -> Optional[dtime]:
    """
    Parse `hh:mm` or `hh-mm` into a `datetime.time`.
    """
    # Normalize dash to colon
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
    """
    Compute nearest date with the chosen weekday (including the chosen day) where
    the resulting datetime is >= now. If the time for the same weekday today has
    already passed, we jump to the next occurrence of that weekday.
    """
    for i in range(0, 14):
        candidate_date = (now + timedelta(days=i)).date()
        if candidate_date.weekday() != weekday_index:
            continue
        candidate_dt = datetime.combine(candidate_date, at_time).replace(tzinfo=SERBIA_TZ)
        if candidate_dt >= now:
            return candidate_dt
    # Fallback (shouldn't happen): pick next week's weekday
    candidate_date = (now + timedelta(days=7)).date()
    # If weekday differs, advance until it matches (max 6 steps)
    while candidate_date.weekday() != weekday_index:
        candidate_date = candidate_date + timedelta(days=1)
    return datetime.combine(candidate_date, at_time).replace(tzinfo=SERBIA_TZ)


async def set_game_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show weekday buttons to schedule the game monitoring window."""
    days = list(range(7))  # 0=Mon..6=Sun
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
    if not query.data:
        return
    if not query.data.startswith("set_game_day:"):
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


async def game_time_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    After user picks a weekday via buttons, this parses time text and saves the schedule.
    This is registered for plain text messages (non-command).
    """
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
        # Re-arm state so user can try again
        context.user_data[GAME_DAY_PENDING_KEY] = weekday_index
        return

    now = datetime.now(SERBIA_TZ)
    game_dt = _compute_next_weekday_datetime(now, weekday_index, parsed_time)
    schedule = add_game_schedule(game_dt)

    window_start = game_dt - timedelta(minutes=10)
    window_end = game_dt + timedelta(hours=2)

    await update.message.reply_text(
        "✅ Дата и время сохранены.\n\n"
        f"Игра: {game_dt.strftime('%Y-%m-%d %H:%M')}\n"
        f"Мониторинг включится: {window_start.strftime('%Y-%m-%d %H:%M')}\n"
        f"Мониторинг выключится: {window_end.strftime('%Y-%m-%d %H:%M')}\n"
        f"(id: {schedule.id[:8]})"
    )


async def games_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all saved game datetimes and provide delete buttons."""
    schedules = list_game_schedules()
    if not schedules:
        await update.message.reply_text("📭 Нет сохраненных дат игры. Используйте /set_game")
        return

    lines = ["📅 Сохраненные игры:\n"]
    keyboard = []
    for idx, s in enumerate(schedules, start=1):
        dt = s.game_datetime
        lines.append(f"{idx}. {dt.strftime('%Y-%m-%d %H:%M')} (id: {s.id[:8]})")
        keyboard.append(
            [InlineKeyboardButton(f"🗑 Удалить {idx}", callback_data=f"del_game:{s.id}")]
        )

    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def delete_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a chosen saved datetime."""
    query = update.callback_query
    await query.answer()
    if not query.data or not query.data.startswith("del_game:"):
        return

    schedule_id = query.data.split(":", 1)[1]
    ok = delete_game_schedule(schedule_id)
    if not ok:
        await query.edit_message_text("❌ Не удалось удалить запись (возможно, она уже удалена).")
        return

    # Show updated list
    schedules = list_game_schedules()
    if not schedules:
        await query.edit_message_text("✅ Удалено. Сейчас сохраненных игр нет.")
        return

    lines = ["✅ Удалено. Текущие игры:\n"]
    keyboard = []
    for idx, s in enumerate(schedules, start=1):
        dt = s.game_datetime
        lines.append(f"{idx}. {dt.strftime('%Y-%m-%d %H:%M')} (id: {s.id[:8]})")
        keyboard.append(
            [InlineKeyboardButton(f"🗑 Удалить {idx}", callback_data=f"del_game:{s.id}")]
        )

    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def monitor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /monitor command."""
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide a VK translation URL\n"
            "Example: /monitor https://vk.com/video-123456789_456123789"
        )
        return
    
    translation_url = context.args[0]
    
    # Check if already monitoring
    if translation_url in active_translations:
        await update.message.reply_text("⚠️ Already monitoring this translation")
        return
    
    try:
        # Create monitor
        config = Config()
        monitor = VKTranslationMonitor(
            translation_url, 
            config.TELEGRAM_CHANNEL_ID, 
            context.application, 
            update.effective_user.id
        )
        active_translations[translation_url] = monitor
        
        await update.message.reply_text("✅ Starting to monitor the translation...")
        
        # Start monitoring in background
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
        # Truncate URL for display if too long
        display_url = url if len(url) <= 50 else url[:47] + "..."
        message += f"{i}. {display_url}\n"
        
        # Create a hash of the URL for callback data (Telegram has 64-byte limit)
        url_hash = hashlib.md5(url.encode()).hexdigest()
        callback_data = f"remove:{url_hash}"
        
        # Store the mapping globally for later retrieval
        url_hash_to_url[url_hash] = url
        
        # Add button for each translation
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
    """Handle /catch_existing command - start monitoring any currently live streams."""
    config = Config()
    
    if not config.is_group_monitoring_configured:
        await update.message.reply_text("❌ VK group monitoring is not configured")
        return
    
    if not group_stream_monitor:
        await update.message.reply_text("❌ VK group monitoring is not running")
        return
    
    try:
        # Extract group ID from URL if needed
        extracted_group_id = extract_group_id(config.VK_GROUP)
        
        # Get videos from the group
        videos = await group_stream_monitor.vk_client.get_group_videos(extracted_group_id, count=20)
        
        if not videos:
            await update.message.reply_text("❌ No videos found in group or access denied")
            return
        
        live_streams = []
        for video in videos:
            if group_stream_monitor.vk_client.is_live_stream(video):
                live_streams.append(video)
        
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
            
            # Check if already monitoring this stream
            if stream_url not in active_translations:
                # Send message to channel about new translation
                try:
                    await context.application.bot.send_message(
                        chat_id=config.TELEGRAM_CHANNEL_ID,
                        text=f"Ссылка на трансляцию матча: {stream_url}",
                        parse_mode='HTML'
                    )
                except Exception as e:
                    logger.error(f"Error sending channel message: {e}")
                
                # Start monitoring this stream
                monitor = VKTranslationMonitor(
                    stream_url, 
                    config.TELEGRAM_CHANNEL_ID, 
                    context.application, 
                    update.effective_user.id
                )
                active_translations[stream_url] = monitor
                
                # Start monitoring in background
                asyncio.create_task(monitor.start_monitoring())
                started_monitoring += 1
                
                # Mark as seen to avoid duplicate detection
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


def get_active_translations() -> Dict[str, VKTranslationMonitor]:
    """Get the dictionary of active translations."""
    return active_translations


def get_group_stream_monitor() -> VKGroupStreamMonitor:
    """Get the group stream monitor instance."""
    return group_stream_monitor


def set_group_stream_monitor(monitor: VKGroupStreamMonitor):
    """Set the group stream monitor instance."""
    global group_stream_monitor
    group_stream_monitor = monitor


# Global mapping to store URL hashes (for callback queries)
url_hash_to_url: Dict[str, str] = {}


async def remove_translation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback query for removing a translation."""
    query = update.callback_query
    
    # Answer the callback query to remove loading state
    await query.answer()
    
    if not query.data or not query.data.startswith("remove:"):
        await query.edit_message_text("❌ Invalid callback data")
        return
    
    # Extract hash from callback data
    url_hash = query.data.split(":", 1)[1]
    
    # Find the URL from the hash mapping
    translation_url = url_hash_to_url.get(url_hash)
    
    if not translation_url:
        # Fallback: try to find it in active_translations by matching hash
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
    
    # Stop the monitor
    monitor = active_translations[translation_url]
    monitor.is_active = False
    
    # Remove from active translations
    del active_translations[translation_url]
    
    # Clean up hash mapping
    if url_hash in url_hash_to_url:
        del url_hash_to_url[url_hash]
    
    # Update the message to show it was removed
    removed_url = translation_url if len(translation_url) <= 50 else translation_url[:47] + "..."
    
    if active_translations:
        # If there are still translations, show updated list
        # Clear old hash mappings and rebuild from current active translations
        url_hash_to_url.clear()
        
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
        await query.edit_message_text(message, reply_markup=reply_markup)
    else:
        await query.edit_message_text(
            f"✅ Removed translation:\n{removed_url}\n\n"
            f"📭 No active translations remaining"
        )
