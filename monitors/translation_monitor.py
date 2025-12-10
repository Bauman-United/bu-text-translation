"""
VK Translation Monitor for tracking live stream comments.

This module contains the VKTranslationMonitor class that monitors VK live streams
for score comments and sends notifications to Telegram channels.
"""

import asyncio
import logging
from datetime import datetime
from typing import Set, Optional, Tuple, List

from telegram.ext import Application

from api.vk_client import VKClient
from utils.url_parser import parse_video_url, parse_score_comment, is_score_comment
from config.settings import Config
from services.gpt_service import GPTCommentaryService
from utils.error_notifier import send_error_notification

logger = logging.getLogger(__name__)


class VKTranslationMonitor:
    """Monitor VK translation for new comments."""
    
    def __init__(self, translation_url: str, channel_id: str, app: Application, user_id: int):
        """
        Initialize VK translation monitor.
        
        Args:
            translation_url: VK video URL to monitor
            channel_id: Telegram channel ID for notifications
            app: Telegram application instance
            user_id: User ID for direct messages
        """
        self.translation_url = translation_url
        self.channel_id = channel_id
        self.app = app
        self.user_id = user_id
        self.seen_comments: Set[int] = set()
        self.is_active = True
        self.current_score = (0, 0)  # (our_score, opponent_score)
        self.message_history: List[str] = []  # Store previous score change messages
        
        # Initialize GPT service if available
        self.gpt_service = None
        try:
            # Create error notifier for GPT service
            async def gpt_error_notifier(service_name, request_info, error_code, error_message):
                await send_error_notification(self.app, self.user_id, service_name, request_info, error_code, error_message)
            
            self.gpt_service = GPTCommentaryService(error_notifier=gpt_error_notifier)
            logger.info("GPT commentary service initialized")
        except Exception as e:
            logger.warning(f"GPT service not available: {e}")
            self.gpt_service = None
        
        # Parse URL and initialize VK client
        self.owner_id, self.video_id = parse_video_url(translation_url)
        config = Config()
        
        # Create error notifier for VK client
        async def vk_error_notifier(service_name, request_info, error_code, error_message):
            await send_error_notification(self.app, self.user_id, service_name, request_info, error_code, error_message)
        
        self.vk_client = VKClient(config.VK_ACCESS_TOKEN, error_notifier=vk_error_notifier)
    
    async def check_comments(self) -> bool:
        """
        Check for new comments on the translation.
        
        Returns:
            True if monitoring should continue, False if stream ended
        """
        try:
            # Get video information
            video_info = await self.vk_client.get_video_info(self.owner_id, self.video_id)
            
            if not video_info:
                logger.error("Video not found or access denied")
                return False
            
            # Check if translation is live
            if self.vk_client.is_stream_ended(video_info):
                # Live translation ended
                logger.info(f"Translation has ended: {self.translation_url}")
                self.is_active = False
                await self.send_system_message("🔴 Translation has ended. Monitoring stopped.")
                
                # Also send notification to the user who started monitoring
                await self.send_notification_to_user(
                    f"🔴 <b>STREAM FINISHED!</b>\n\n"
                    f"📺 Stream URL: {self.translation_url}\n"
                    f"⏹️ Monitoring has been stopped automatically"
                )
                return False
            
            # Get comments
            comments = await self.vk_client.get_video_comments(self.owner_id, self.video_id)
            
            new_comments = []
            for comment in comments:
                comment_id = comment['id']
                if comment_id not in self.seen_comments:
                    self.seen_comments.add(comment_id)
                    new_comments.append(comment)
            
            # Send new comments to Telegram channel
            for comment in new_comments:
                await self.send_comment_to_channel(comment)
            
            return True
            
        except Exception as e:
            logger.error(f"Error checking comments: {e}")
            return True
    
    async def send_comment_to_channel(self, comment: dict):
        """Send a comment to the Telegram channel only if it contains score information."""
        try:
            # Get user information
            text = comment.get('text', '')
            
            # Check if comment contains score information
            if not is_score_comment(text):
                logger.debug(f"Skipping comment (not a score): {text}")
                return
            
            # Parse the score
            score_data = parse_score_comment(text)
            if not score_data:
                return
            
            our_score, opponent_score, surname = score_data
            previous_our_score, previous_opponent_score = self.current_score
            
            # Check if our team scored (first number increased)
            if our_score > previous_our_score:
                # Generate commentary using GPT if available
                if self.gpt_service and self.gpt_service.is_available():
                    new_score_str = f"{our_score}-{opponent_score}"
                    gpt_message = await self.gpt_service.generate_commentary(
                        self.message_history, 
                        new_score_str, 
                        is_our_goal=True,
                        scorer_surname=surname
                    )
                    if gpt_message:
                        message = gpt_message
                    else:
                        # Fallback to default message if GPT fails
                        message = f"⚽ Забиваем! Счет: {our_score}-{opponent_score}"
                        if surname:
                            surname_capitalized = surname.capitalize()
                            message = f"⚽ Забиваем! Гол забил {surname_capitalized}. Счет: {our_score}-{opponent_score}"
                else:
                    # Use default message format
                    message = f"⚽ Забиваем! Счет: {our_score}-{opponent_score}"
                    if surname:
                        surname_capitalized = surname.capitalize()
                        message = f"⚽ Забиваем! Гол забил {surname_capitalized}. Счет: {our_score}-{opponent_score}"
                
                video_path = None
                if surname:
                    # Check surname in lowercase for video mapping
                    surname_lower = surname.lower()
                    # Determine which video to attach based on surname
                    video_path = self._get_celebration_video_path(surname_lower)
                
                # Send message with or without video
                if video_path:
                    try:
                        await self.app.bot.send_video(
                            chat_id=self.channel_id,
                            video=open(video_path, 'rb'),
                            caption=message,
                            parse_mode='HTML'
                        )
                    except FileNotFoundError:
                        # Fallback to text message if video not found
                        await self.app.bot.send_message(
                            chat_id=self.channel_id,
                            text=message,
                            parse_mode='HTML'
                        )
                else:
                    await self.app.bot.send_message(
                        chat_id=self.channel_id,
                        text=message,
                        parse_mode='HTML'
                    )
            # Check if opponent scored (second number increased)
            elif opponent_score > previous_opponent_score:
                # Generate commentary using GPT if available
                if self.gpt_service and self.gpt_service.is_available():
                    new_score_str = f"{our_score}-{opponent_score}"
                    gpt_message = await self.gpt_service.generate_commentary(
                        self.message_history, 
                        new_score_str, 
                        is_our_goal=False,
                        scorer_surname=None
                    )
                    if gpt_message:
                        message = gpt_message
                    else:
                        # Fallback to default message if GPT fails
                        message = f"Пропускаем. Счет: {our_score}-{opponent_score}"
                else:
                    # Use default message format
                    message = f"Пропускаем. Счет: {our_score}-{opponent_score}"
                
                await self.app.bot.send_message(
                    chat_id=self.channel_id,
                    text=message,
                    parse_mode='HTML'
                )
            else:
                # Score didn't change, skip this comment
                logger.debug(f"Score didn't change: {text}")
                return
            
            # Update current score
            self.current_score = (our_score, opponent_score)
            
            # Store message in history for future GPT context
            self.message_history.append(message)
            
            # Keep only last 10 messages to avoid context overflow
            if len(self.message_history) > 10:
                self.message_history = self.message_history[-10:]
            
            logger.info(f"Posted score update: {message}")
            
        except Exception as e:
            logger.error(f"Error sending comment to channel: {e}")
    
    def _get_celebration_video_path(self, surname_lower: str) -> Optional[str]:
        """
        Get celebration video path based on surname.
        
        Args:
            surname_lower: Surname in lowercase
            
        Returns:
            Path to celebration video or None
        """
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
    
    async def send_message(self, text: str):
        """Send a message to the Telegram channel."""
        try:
            await self.app.bot.send_message(
                chat_id=self.channel_id,
                text=text,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Error sending message: {e}")
    
    async def send_system_message(self, text: str):
        """Send a system message directly to the user."""
        try:
            await self.app.bot.send_message(
                chat_id=self.user_id,
                text=text,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Error sending system message: {e}")
    
    async def send_notification_to_user(self, text: str):
        """Send a notification directly to the user."""
        try:
            await self.app.bot.send_message(
                chat_id=self.user_id,
                text=text,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Error sending notification to user: {e}")
    
    async def process_existing_comments(self):
        """
        Process existing comments when starting monitoring.
        This ensures we catch up on any score updates that happened before monitoring started.
        We track the current score but don't send notifications for old comments to avoid spam.
        """
        try:
            logger.info(f"Processing existing comments for {self.translation_url}")
            comments = await self.vk_client.get_video_comments(self.owner_id, self.video_id)
            
            if not comments:
                logger.info("No existing comments found")
                return
            
            # Process comments in reverse order (oldest first) to track score progression correctly
            # Comments from VK API are typically in reverse chronological order (newest first)
            comments_reversed = list(reversed(comments))
            
            score_comments_processed = 0
            for comment in comments_reversed:
                comment_id = comment['id']
                self.seen_comments.add(comment_id)
                
                # Process score comments to update current score (but don't send notifications)
                text = comment.get('text', '')
                if is_score_comment(text):
                    score_data = parse_score_comment(text)
                    if score_data:
                        our_score, opponent_score, surname = score_data
                        # Update current score to track the latest score from existing comments
                        # We only update if this score is higher (more recent)
                        if our_score > self.current_score[0] or opponent_score > self.current_score[1]:
                            self.current_score = (our_score, opponent_score)
                            score_comments_processed += 1
                            logger.debug(f"Updated score from existing comment: {our_score}-{opponent_score}")
            
            logger.info(f"Processed {len(comments)} existing comments ({score_comments_processed} score comments)")
            if self.current_score != (0, 0):
                logger.info(f"Current score initialized from existing comments: {self.current_score[0]}-{self.current_score[1]}")
            
        except Exception as e:
            logger.error(f"Error processing existing comments: {e}")
    
    async def start_monitoring(self):
        """Start monitoring the translation."""
        logger.info(f"Starting monitoring for {self.translation_url}")
        await self.send_system_message(
            f"✅ Started monitoring VK translation\n"
            f"🔗 {self.translation_url}\n"
            f"⏱ Checking every 30 seconds"
        )
        
        # Process existing comments to catch up on score updates
        await self.process_existing_comments()
        
        # Start monitoring loop
        while self.is_active:
            try:
                is_active = await self.check_comments()
                if not is_active:
                    break
                await asyncio.sleep(30)  # Check every 30 seconds
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(30)
        
        logger.info(f"Stopped monitoring {self.translation_url}")
