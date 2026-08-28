"""
VK Translation Monitor for tracking live stream comments.

This module contains the VKTranslationMonitor class that monitors VK live streams
for score comments and sends notifications to Telegram channels.
"""

import asyncio
import logging
from typing import Set

from telegram.ext import Application

from api.vk_auth import VKAuthError
from api.vk_client import VKClient
from utils.url_parser import parse_video_url, parse_score_comment
from config.settings import Config
from services.goal_announcer import GoalAnnouncer, get_channel_tracker
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

        # Score, history, dedup and posting all live in the shared announcer, so
        # a goal already reported by the site monitor or the manual translation
        # is not posted a second time here.
        self.announcer = GoalAnnouncer(
            app, channel_id, user_id=user_id, gpt_service=self.gpt_service
        )

    @property
    def current_score(self):
        """Running score, owned by the tracker shared across all sources."""
        return get_channel_tracker(self.channel_id).score

    async def check_comments(self) -> bool:
        """
        Check for new comments on the translation.
        
        Returns:
            True if monitoring should continue, False if stream ended
        """
        try:
            # Get comments directly - removed video.get call to reduce API usage
            # Stream end detection is handled by the group monitor or by detecting
            # when comments stop coming for an extended period
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

        except VKAuthError as e:
            # Authorization is dead — polling on would spam the owner every 30s
            # and cannot succeed until someone re-runs scripts/vk_authorize.py.
            logger.error(f"Stopping monitoring for {self.translation_url}: VK auth failed — {e}")
            return False
        except Exception as e:
            # VK: sometimes the stream video can't be accessed anymore / doesn't exist
            # (e.g. "Access denied: video not found", code=15). In this case, keep polling
            # would waste VK quota and spam notifications, so we stop monitoring.
            error_code = getattr(e, "code", None)
            error_text = str(e).lower()
            if error_code == 15 or "video not found" in error_text or "access denied" in error_text:
                logger.info(
                    f"Stopping monitoring for {self.translation_url} due to terminal VK error: "
                    f"code={error_code}, error={e}"
                )
                return False

            logger.error(f"Error checking comments: {e}")
            return True
    
    async def send_comment_to_channel(self, comment: dict):
        """Announce a comment to the channel when it reports a new score."""
        try:
            score_data = parse_score_comment(comment.get('text', ''))
            if not score_data:
                logger.debug(f"Skipping comment (not a score): {comment.get('text', '')}")
                return

            our_score, opponent_score, surname = score_data
            await self.announcer.announce(
                our_score,
                opponent_score,
                scorer_surname=surname or None,
            )
        except Exception as e:
            logger.error(f"Error sending comment to channel: {e}")

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
            
            # get_video_comments() already returns comments oldest-first, which is
            # what tracking score progression needs — do not reorder them here.
            tracker = get_channel_tracker(self.channel_id)
            score_comments_processed = 0
            for comment in comments:
                comment_id = comment['id']
                self.seen_comments.add(comment_id)
                
                # Process score comments to update current score (but don't send notifications)
                score_data = parse_score_comment(comment.get('text', ''))
                if score_data:
                    our_score, opponent_score, _ = score_data
                    # Advance the tracker without announcing: these goals already
                    # happened before monitoring started.
                    if tracker.register(our_score, opponent_score):
                        score_comments_processed += 1
                        logger.debug(f"Seeded score from existing comment: {our_score}-{opponent_score}")
            
            logger.info(f"Processed {len(comments)} existing comments ({score_comments_processed} score comments)")
            if self.current_score != (0, 0):
                logger.info(f"Current score initialized from existing comments: {self.current_score[0]}-{self.current_score[1]}")
            
        except VKAuthError:
            # Let start_monitoring's loop hit the same error and stop cleanly.
            logger.error("Could not process existing comments: VK auth failed")
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
        
        # Send current score as initial status if we found one
        if self.current_score != (0, 0):
            our_score, opponent_score = self.current_score
            initial_message = f"📊 Текущий счет: {our_score}-{opponent_score}"
            await self.send_message(initial_message)
            logger.info(f"Sent initial score: {our_score}-{opponent_score}")
        
        # Add delay after processing existing comments to avoid rate limits
        # This gives time between get_video_comments() and the first check_comments() call
        # The rate limiter adds 10 seconds between calls, so we add extra margin
        # We wait longer to ensure any other concurrent API calls (like from group monitor) complete first
        # Also gives time for the rate limiter to properly space out calls
        # VK has strict rate limits, so we wait 20 seconds to be safe
        await asyncio.sleep(20)
        
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
        # Cleanup: remove from active_translations so future discovery can start again.
        try:
            from handlers.telegram_commands import get_active_translations
            active_translations = get_active_translations()
            if self.translation_url in active_translations:
                del active_translations[self.translation_url]
        except Exception:
            # Cleanup should never crash monitoring shutdown
            logger.debug("Cleanup after stopping monitoring failed", exc_info=True)
