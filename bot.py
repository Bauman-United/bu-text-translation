import os
import time
import asyncio
import logging
from datetime import datetime
from typing import Set, Optional
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import vk_api
import re

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')
VK_ACCESS_TOKEN = os.getenv('VK_ACCESS_TOKEN')

# Global state for tracking translations
active_translations = {}


class VKTranslationMonitor:
    """Monitor VK translation for new comments"""
    
    def __init__(self, translation_url: str, channel_id: str, app: Application):
        self.translation_url = translation_url
        self.channel_id = channel_id
        self.app = app
        self.seen_comments: Set[int] = set()
        self.is_active = True
        self.owner_id = None
        self.video_id = None
        self.vk_session = None
        self.vk_api = None
        
        self._parse_url()
        self._initialize_vk()
    
    def _parse_url(self):
        """Parse VK translation URL to extract owner_id and video_id"""
        # Example URL: https://vk.com/video-123456789_456123789
        # or https://vk.com/video?z=video-123456789_456123789
        match = re.search(r'video(-?\d+)_(\d+)', self.translation_url)
        if match:
            self.owner_id = match.group(1)
            self.video_id = match.group(2)
            logger.info(f"Parsed video: owner_id={self.owner_id}, video_id={self.video_id}")
        else:
            raise ValueError("Invalid VK translation URL format")
    
    def _initialize_vk(self):
        """Initialize VK API session"""
        if VK_ACCESS_TOKEN:
            self.vk_session = vk_api.VkApi(token=VK_ACCESS_TOKEN)
            self.vk_api = self.vk_session.get_api()
        else:
            logger.warning("VK_ACCESS_TOKEN not provided, using anonymous access")
            self.vk_session = vk_api.VkApi()
            self.vk_api = self.vk_session.get_api()
    
    async def check_comments(self):
        """Check for new comments on the translation"""
        try:
            # Get video information
            video_info = self.vk_api.video.get(
                owner_id=self.owner_id,
                videos=f"{self.owner_id}_{self.video_id}"
            )
            
            if not video_info or 'items' not in video_info or len(video_info['items']) == 0:
                logger.error("Video not found or access denied")
                return False
            
            video = video_info['items'][0]
            
            # Check if translation is live
            if video.get('live') == 2:
                # Live translation ended
                logger.info("Translation has ended")
                self.is_active = False
                await self.send_message("🔴 Translation has ended. Monitoring stopped.")
                return False
            
            # Get comments
            comments = self.vk_api.video.getComments(
                owner_id=self.owner_id,
                video_id=self.video_id,
                sort='asc',
                count=100
            )
            
            if 'items' not in comments:
                return True
            
            new_comments = []
            for comment in comments['items']:
                comment_id = comment['id']
                if comment_id not in self.seen_comments:
                    self.seen_comments.add(comment_id)
                    new_comments.append(comment)
            
            # Send new comments to Telegram channel
            for comment in new_comments:
                await self.send_comment_to_channel(comment)
            
            return True
            
        except vk_api.exceptions.ApiError as e:
            logger.error(f"VK API error: {e}")
            if e.code == 15:  # Access denied
                await self.send_message("❌ Access denied to video. Please check VK access token permissions.")
                self.is_active = False
                return False
            return True
        except Exception as e:
            logger.error(f"Error checking comments: {e}")
            return True
    
    async def send_comment_to_channel(self, comment: dict):
        """Send a comment to the Telegram channel"""
        try:
            # Get user information
            user_id = comment.get('from_id')
            text = comment.get('text', '')
            date = datetime.fromtimestamp(comment.get('date', 0))
            
            # Try to get user name
            user_name = f"User {user_id}"
            try:
                if user_id > 0:
                    user_info = self.vk_api.users.get(user_ids=user_id)
                    if user_info:
                        user_name = f"{user_info[0].get('first_name', '')} {user_info[0].get('last_name', '')}".strip()
                else:
                    group_info = self.vk_api.groups.getById(group_id=abs(user_id))
                    if group_info:
                        user_name = group_info[0].get('name', user_name)
            except Exception as e:
                logger.warning(f"Could not fetch user info: {e}")
            
            # Format message
            message = (
                f"💬 <b>New Comment</b>\n\n"
                f"👤 <b>{user_name}</b>\n"
                f"🕐 {date.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"{text}"
            )
            
            await self.app.bot.send_message(
                chat_id=self.channel_id,
                text=message,
                parse_mode='HTML'
            )
            
        except Exception as e:
            logger.error(f"Error sending comment to channel: {e}")
    
    async def send_message(self, text: str):
        """Send a message to the Telegram channel"""
        try:
            await self.app.bot.send_message(
                chat_id=self.channel_id,
                text=text,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Error sending message: {e}")
    
    async def start_monitoring(self):
        """Start monitoring the translation"""
        logger.info(f"Starting monitoring for {self.translation_url}")
        await self.send_message(
            f"✅ Started monitoring VK translation\n"
            f"🔗 {self.translation_url}\n"
            f"⏱ Checking every 60 seconds"
        )
        
        # Initial check to populate seen_comments
        try:
            comments = self.vk_api.video.getComments(
                owner_id=self.owner_id,
                video_id=self.video_id,
                sort='asc',
                count=100
            )
            if 'items' in comments:
                for comment in comments['items']:
                    self.seen_comments.add(comment['id'])
                logger.info(f"Initialized with {len(self.seen_comments)} existing comments")
        except Exception as e:
            logger.error(f"Error during initial check: {e}")
        
        # Start monitoring loop
        while self.is_active:
            try:
                is_active = await self.check_comments()
                if not is_active:
                    break
                await asyncio.sleep(60)  # Check every 60 seconds
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(60)
        
        # Cleanup
        if self.translation_url in active_translations:
            del active_translations[self.translation_url]
        
        logger.info(f"Stopped monitoring {self.translation_url}")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    await update.message.reply_text(
        "👋 Welcome to VK Translation Monitor Bot!\n\n"
        "Commands:\n"
        "/monitor <vk_translation_url> - Start monitoring a VK translation\n"
        "/stop <vk_translation_url> - Stop monitoring a translation\n"
        "/list - List active translations being monitored\n\n"
        "Example:\n"
        "/monitor https://vk.com/video-123456789_456123789"
    )


async def monitor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /monitor command"""
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
        monitor = VKTranslationMonitor(translation_url, TELEGRAM_CHANNEL_ID, context.application)
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
    """Handle /stop command"""
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
    """Handle /list command"""
    if not active_translations:
        await update.message.reply_text("📭 No active translations being monitored")
        return
    
    message = "📊 Active translations:\n\n"
    for i, url in enumerate(active_translations.keys(), 1):
        message += f"{i}. {url}\n"
    
    await update.message.reply_text(message)


def main():
    """Start the bot"""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not found in environment variables")
        return
    
    if not TELEGRAM_CHANNEL_ID:
        logger.error("TELEGRAM_CHANNEL_ID not found in environment variables")
        return
    
    # Create application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("monitor", monitor_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("list", list_command))
    
    # Start the bot
    logger.info("Bot started")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()

