"""
Telegram command handlers for the VK Translation Monitor Bot.

This module contains all the command handlers that process user commands
and interact with the monitoring system.
"""

import asyncio
import logging
import hashlib
from typing import Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config.settings import Config
from utils.url_parser import extract_group_id
from monitors.translation_monitor import VKTranslationMonitor
from monitors.group_stream_monitor import VKGroupStreamMonitor

logger = logging.getLogger(__name__)

# Global state for tracking translations and group monitoring
active_translations: Dict[str, VKTranslationMonitor] = {}
group_stream_monitor: VKGroupStreamMonitor = None


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
        "Example:\n"
        "/monitor https://vk.com/video-123456789_456123789"
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
