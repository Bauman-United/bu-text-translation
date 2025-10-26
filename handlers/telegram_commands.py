"""
Telegram command handlers for the VK Translation Monitor Bot.

This module contains all the command handlers that process user commands
and interact with the monitoring system.
"""

import asyncio
import logging
from typing import Dict, Any

from telegram import Update
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
    for i, url in enumerate(active_translations.keys(), 1):
        message += f"{i}. {url}\n"
    
    await update.message.reply_text(message)


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
        videos = group_stream_monitor.vk_client.get_group_videos(extracted_group_id, count=20)
        
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
