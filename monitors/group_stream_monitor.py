"""
VK Group Stream Monitor for tracking new live streams.

This module contains the VKGroupStreamMonitor class that monitors VK groups
for new live streams and automatically starts monitoring them.
"""

import asyncio
import logging
from datetime import datetime
from typing import Set, Optional

import vk_api

from telegram.ext import Application

from api.vk_client import VKClient
from utils.url_parser import extract_group_id
from monitors.translation_monitor import VKTranslationMonitor
from config.settings import Config
from utils.error_notifier import send_error_notification
from utils.game_schedule import is_time_in_any_window

logger = logging.getLogger(__name__)


class VKGroupStreamMonitor:
    """Monitor VK group for new live streams."""
    
    def __init__(self, group_id: str, channel_id: str, app: Application, user_id: int):
        """
        Initialize VK group stream monitor.
        
        Args:
            group_id: VK group ID to monitor
            channel_id: Telegram channel ID for notifications
            app: Telegram application instance
            user_id: User ID for direct messages
        """
        self.group_id = extract_group_id(group_id)
        self.channel_id = channel_id
        self.app = app
        self.user_id = user_id
        # Track streams we've already started monitoring (by video id "owner_id_id")
        self.seen_streams: Set[str] = set()
        # Track last seen wall post id to only process new posts
        self.last_wall_post_id: Optional[int] = None
        self.is_active = True
        
        # Initialize VK client with access token
        config = Config()
        
        # Create error notifier for VK client
        async def vk_error_notifier(service_name, request_info, error_code, error_message):
            await send_error_notification(self.app, self.user_id, service_name, request_info, error_code, error_message)
        
        self.vk_client = VKClient(config.VK_ACCESS_TOKEN, error_notifier=vk_error_notifier)
    
    async def check_for_new_streams(self) -> bool:
        """
        Check for new wall posts in the VK group and start monitoring any live stream videos found.
        
        Returns:
            True if monitoring should continue, False if stopped
        """
        try:
            now = datetime.now()

            # Stop comment monitoring when we are outside all scheduled windows.
            from handlers.telegram_commands import get_active_translations
            active_translations = get_active_translations()

            if not is_time_in_any_window(now):
                if active_translations:
                    for monitor in list(active_translations.values()):
                        monitor.is_active = False
                    active_translations.clear()
                    logger.info("Outside scheduled monitoring windows: stopped all active stream monitors")
                return True
            
            # We are inside at least one scheduled window.
            # If we already have an active stream being monitored, skip VK discovery to avoid extra VK calls.
            if active_translations:
                logger.debug(
                    f"Skipping new stream check - already monitoring {len(active_translations)} stream(s)"
                )
                return True
            
            logger.info(f"Checking for new wall posts in group {self.group_id}")
            
            posts = await self.vk_client.get_group_wall_posts(self.group_id, count=30)
            if not posts:
                logger.debug("No wall posts returned")
                return True

            # Debug: show what we got from VK (ids + attachment types for newest few)
            try:
                newest_preview = posts[:5]
                logger.info(
                    "VK wall.get preview (newest first): "
                    + ", ".join(
                        f"id={p.get('id')} att={[a.get('type') for a in (p.get('attachments') or [])]}"
                        f"{' copy_history=' + str(len(p.get('copy_history') or [])) if (p.get('copy_history') or []) else ''}"
                        for p in newest_preview
                    )
                )
            except Exception:
                # Never fail monitoring due to debug logging
                pass
            
            # wall.get returns newest first; we want to process only posts newer than last_wall_post_id
            if self.last_wall_post_id is None:
                # First run: initialize watermark to current newest post id, don't back-process history
                newest_id = max((p.get('id') or 0) for p in posts)
                self.last_wall_post_id = int(newest_id) if newest_id else 0
                logger.info(f"Initialized wall post watermark: {self.last_wall_post_id} (newest wall post id)")

                # IMPORTANT: Also process the latest wall post once.
                # This satisfies "catch last post" behavior without scanning old history.
                newest_posts = [p for p in posts if (p.get('id') or 0) == int(self.last_wall_post_id)]
                if not newest_posts and posts:
                    newest_posts = [posts[0]]
                
                started = 0
                for post in newest_posts:
                    post_id = post.get('id')
                    post_dt = None
                    if post.get('date') is not None:
                        try:
                            post_dt = datetime.fromtimestamp(int(post.get('date')))
                        except Exception:
                            post_dt = None

                    videos = self.vk_client.extract_videos_from_wall_post(post)
                    if not videos:
                        att_types = [a.get('type') for a in (post.get('attachments') or [])]
                        ch_len = len(post.get('copy_history') or [])
                        logger.info(
                            f"Init wall post {post_id}: no video attachments found "
                            f"(attachments={att_types}, copy_history={ch_len})"
                        )
                        continue
                    
                    for video in videos:
                        logger.info(
                            f"Init wall post {post_id}: found video owner_id={video.get('owner_id')} id={video.get('id')} "
                            f"live={video.get('live')} live_status={video.get('live_status')} is_mobile_live={video.get('is_mobile_live')} "
                            f"type={video.get('type')}"
                        )
                        if not self.vk_client.is_live_stream(video):
                            continue
                        
                        video_id = self.vk_client.get_video_id(video)
                        title = video.get('title', 'Live Stream')
                        stream_url = self.vk_client.get_video_url(video)

                        # Safety: only start monitoring if this wall post is within any window.
                        if post_dt is not None and not is_time_in_any_window(post_dt):
                            logger.info(
                                f"Skipping stream from wall post {post_id} because post_dt is outside windows "
                                f"(post_dt={post_dt.isoformat()})"
                            )
                            continue
                        
                        if stream_url in active_translations:
                            logger.debug(f"Live stream already being monitored (init from wall post {post_id}): {video_id}")
                            continue
                        if video_id in self.seen_streams:
                            logger.debug(f"Live stream already seen (init from wall post {post_id}): {video_id}")
                            continue
                        
                        logger.info(f"NEW LIVE STREAM FROM LAST WALL POST {post_id}: {video_id} - {title}")
                        self.seen_streams.add(video_id)
                        await self.handle_new_stream(video)
                        started += 1
                
                logger.info(f"Init processing complete. Started {started} live stream monitor(s) from last wall post.")
                return True
            
            new_posts = [p for p in posts if (p.get('id') or 0) > int(self.last_wall_post_id)]
            if not new_posts:
                logger.debug(f"No new wall posts since last check (watermark={self.last_wall_post_id})")
                return True
            
            # Process oldest -> newest to preserve order
            new_posts.sort(key=lambda p: p.get('id') or 0)
            logger.info(
                f"New wall posts detected: ids={[p.get('id') for p in new_posts]} (watermark={self.last_wall_post_id})"
            )
            
            started = 0
            for post in new_posts:
                post_id = post.get('id')
                post_dt = None
                if post.get('date') is not None:
                    try:
                        post_dt = datetime.fromtimestamp(int(post.get('date')))
                    except Exception:
                        post_dt = None

                videos = self.vk_client.extract_videos_from_wall_post(post)
                if not videos:
                    # Log attachment types to understand why we didn't see videos
                    att_types = [a.get('type') for a in (post.get('attachments') or [])]
                    ch_len = len(post.get('copy_history') or [])
                    logger.info(f"Wall post {post_id}: no video attachments found (attachments={att_types}, copy_history={ch_len})")
                    continue
                
                for video in videos:
                    logger.info(
                        f"Wall post {post_id}: found video owner_id={video.get('owner_id')} id={video.get('id')} "
                        f"live={video.get('live')} live_status={video.get('live_status')} is_mobile_live={video.get('is_mobile_live')} "
                        f"type={video.get('type')}"
                    )
                    if not self.vk_client.is_live_stream(video):
                        continue
                    
                    video_id = self.vk_client.get_video_id(video)
                    title = video.get('title', 'Live Stream')
                    stream_url = self.vk_client.get_video_url(video)

                    # Only start monitoring if wall post date is within any scheduled windows.
                    if post_dt is not None and not is_time_in_any_window(post_dt):
                        logger.info(
                            f"Skipping stream from wall post {post_id} because post_dt is outside windows "
                            f"(post_dt={post_dt.isoformat()})"
                        )
                        continue
                    
                    if stream_url in active_translations:
                        logger.debug(f"Live stream already being monitored (from wall post {post_id}): {video_id}")
                        continue
                    if video_id in self.seen_streams:
                        logger.debug(f"Live stream already seen (from wall post {post_id}): {video_id}")
                        continue
                    
                    logger.info(f"NEW LIVE STREAM FROM WALL POST {post_id}: {video_id} - {title}")
                    self.seen_streams.add(video_id)
                    await self.handle_new_stream(video)
                    started += 1
            
            # Advance watermark
            newest_processed = max((p.get('id') or 0) for p in new_posts)
            self.last_wall_post_id = max(int(self.last_wall_post_id), int(newest_processed or 0))
            
            logger.info(
                f"Processed {len(new_posts)} new wall post(s), started {started} live stream monitor(s). "
                f"Watermark now {self.last_wall_post_id}"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Error checking for new streams: {e}")
            return True
    
    async def handle_new_stream(self, stream: dict):
        """Handle a new live stream found."""
        try:
            stream_url = self.vk_client.get_video_url(stream)
            stream_title = stream.get('title', 'Live Stream')
            
            logger.info(f"New live stream found: {stream_url}")
            
            # Send notification to user
            await self.send_notification(
                f"🔴 <b>NEW STREAM FOUND!</b>\n\n"
                f"📺 Title: {stream_title}\n"
                f"🔗 URL: {stream_url}\n\n"
                f"Starting automatic monitoring..."
            )
            
            # Send message to channel about new translation
            await self.send_channel_message(
                f"Ссылка на трансляцию матча: {stream_url}"
            )
            
            # Create and start monitoring the stream
            monitor = VKTranslationMonitor(
                stream_url, 
                self.channel_id, 
                self.app, 
                self.user_id
            )
            
            # Import here to avoid circular imports
            from handlers.telegram_commands import get_active_translations
            active_translations = get_active_translations()
            active_translations[stream_url] = monitor
            
            # Add delay before starting translation monitor to avoid concurrent API calls
            # This ensures the group monitor's current API call cycle completes first
            await asyncio.sleep(2)
            
            # Start monitoring in background
            asyncio.create_task(monitor.start_monitoring())
            
        except Exception as e:
            logger.error(f"Error handling new stream: {e}")
    
    async def send_notification(self, text: str):
        """Send notification directly to the user."""
        try:
            await self.app.bot.send_message(
                chat_id=self.user_id,
                text=text,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Error sending notification: {e}")
    
    async def send_channel_message(self, text: str):
        """Send message to the Telegram channel."""
        try:
            await self.app.bot.send_message(
                chat_id=self.channel_id,
                text=text,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Error sending channel message: {e}")
    
    async def start_polling(self):
        """Start polling for new streams every 30 seconds."""
        logger.info(f"Starting VK group stream monitoring for group {self.group_id}")
        await self.send_notification(
            f"✅ Started monitoring VK group {self.group_id} for new live streams\n"
            f"⏱ Checking every 30 seconds"
        )
        # Initialize watermark on first check (no back-processing history)
        try:
            await self.check_for_new_streams()
        except Exception as e:
            logger.error(f"Error during initial wall watermark setup: {e}")
        
        # Start polling loop
        while self.is_active:
            try:
                is_active = await self.check_for_new_streams()
                if not is_active:
                    break
                await asyncio.sleep(30)  # Check every 30 seconds
            except Exception as e:
                logger.error(f"Error in stream polling loop: {e}")
                await asyncio.sleep(30)
