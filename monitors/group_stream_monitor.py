"""
VK Group Stream Monitor for tracking new live streams.

This module contains the VKGroupStreamMonitor class that monitors VK groups
for new live streams and automatically starts monitoring them.
"""

import asyncio
import logging
from typing import Set

from telegram.ext import Application

from api.vk_client import VKClient
from utils.url_parser import extract_group_id
from monitors.translation_monitor import VKTranslationMonitor
from config.settings import Config

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
        self.seen_streams: Set[str] = set()
        self.is_active = True
        
        # Initialize VK client with access token
        config = Config()
        self.vk_client = VKClient(config.VK_ACCESS_TOKEN)
    
    async def check_for_new_streams(self) -> bool:
        """
        Check for new live streams in the VK group.
        
        Returns:
            True if monitoring should continue, False if stopped
        """
        try:
            logger.info(f"Checking for new streams in group {self.group_id}")
            
            # Get videos from the group - request more to ensure we catch all live streams
            videos = self.vk_client.get_group_videos(self.group_id, count=50)
            
            if not videos:
                logger.warning("No videos found in group or access denied")
                return True
            
            # Always check ALL videos individually to get fresh data
            # The list API might return stale data, so we need to verify each video
            # This ensures we catch live streams even if they're not at the top of the list
            logger.info(f"Checking {len(videos)} videos individually for live status...")
            for video in videos:
                video_id = self.vk_client.get_video_id(video)
                # Always check individually to get the most up-to-date status
                try:
                    detailed_info = self.vk_client.get_video_info(
                        str(video['owner_id']), 
                        str(video['id'])
                    )
                    if detailed_info:
                        # Update video with detailed info (this includes fresh live status)
                        old_live = video.get('live')
                        old_live_status = video.get('live_status')
                        video.update(detailed_info)
                        new_live = detailed_info.get('live')
                        new_live_status = detailed_info.get('live_status')
                        new_is_mobile_live = detailed_info.get('is_mobile_live')
                        # Only log if status changed or if it's a live stream
                        if (old_live != new_live or old_live_status != new_live_status or 
                            new_live == 1 or new_live_status == 'started' or new_is_mobile_live):
                            logger.info(f"Video {video_id}: live={new_live}, live_status={new_live_status}, is_mobile_live={new_is_mobile_live}")
                except Exception as e:
                    logger.debug(f"Could not check video {video_id} individually: {e}")
            
            new_streams = []
            ended_streams = []
            
            # Debug: log all videos to see what we're getting
            logger.info(f"Retrieved {len(videos)} videos from group")
            for idx, video in enumerate(videos[:5]):  # Log first 5 videos for debugging
                video_id = self.vk_client.get_video_id(video)
                title = video.get('title', 'No title')
                live = video.get('live')
                live_status = video.get('live_status', '')
                # Log all video fields to understand structure
                if idx == 0:
                    logger.info(f"First video structure: {list(video.keys())}")
                is_mobile_live = video.get('is_mobile_live', False)
                logger.info(f"Video {idx+1}: id={video_id}, title={title[:50]}, live={live}, live_status={live_status}, is_mobile_live={is_mobile_live}, type={video.get('type', 'N/A')}")
            
            for video in videos:
                video_id = self.vk_client.get_video_id(video)
                title = video.get('title', 'No title')
                
                # Check if it's a live stream
                if self.vk_client.is_live_stream(video):
                    if video_id not in self.seen_streams:
                        logger.info(f"NEW LIVE STREAM DETECTED: {video_id} - {title}")
                        self.seen_streams.add(video_id)
                        new_streams.append(video)
                    else:
                        logger.debug(f"Live stream already seen: {video_id}")
                elif self.vk_client.is_stream_ended(video) and video_id in self.seen_streams:
                    # Stream ended
                    logger.info(f"STREAM ENDED: {video_id} - {title}")
                    ended_streams.append(video)
                    self.seen_streams.discard(video_id)  # Remove from seen streams
            
            logger.info(f"Found {len(new_streams)} new streams")
            logger.info(f"Found {len(ended_streams)} ended streams")
            logger.info(f"Total seen streams: {len(self.seen_streams)}")
            
            # Process new streams
            for stream in new_streams:
                await self.handle_new_stream(stream)
            
            # Process ended streams
            for stream in ended_streams:
                await self.handle_ended_stream(stream)
            
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
            
            # Start monitoring in background
            asyncio.create_task(monitor.start_monitoring())
            
        except Exception as e:
            logger.error(f"Error handling new stream: {e}")
    
    async def handle_ended_stream(self, stream: dict):
        """Handle an ended live stream."""
        try:
            stream_url = self.vk_client.get_video_url(stream)
            stream_title = stream.get('title', 'Live Stream')
            
            logger.info(f"Live stream ended: {stream_url}")
            
            # Send notification to user
            await self.send_notification(
                f"🔴 <b>STREAM FINISHED!</b>\n\n"
                f"📺 Title: {stream_title}\n"
                f"🔗 URL: {stream_url}\n\n"
                f"⏹️ Stream has ended and monitoring has been stopped"
            )
            
            # Stop monitoring this stream if it's in active_translations
            from handlers.telegram_commands import get_active_translations
            active_translations = get_active_translations()
            
            if stream_url in active_translations:
                monitor = active_translations[stream_url]
                monitor.is_active = False
                del active_translations[stream_url]
                logger.info(f"Stopped monitoring ended stream: {stream_url}")
            
        except Exception as e:
            logger.error(f"Error handling ended stream: {e}")
    
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
        """Start polling for new streams every 15 seconds."""
        logger.info(f"Starting VK group stream monitoring for group {self.group_id}")
        await self.send_notification(
            f"✅ Started monitoring VK group {self.group_id} for new live streams\n"
            f"⏱ Checking every 15 seconds"
        )
        
        # Initial check to populate seen_streams
        try:
            videos = self.vk_client.get_group_videos(self.group_id, count=20)
            if videos:
                for video in videos:
                    if self.vk_client.is_live_stream(video):
                        video_id = self.vk_client.get_video_id(video)
                        self.seen_streams.add(video_id)
                logger.info(f"Initialized with {len(self.seen_streams)} existing live streams")
        except Exception as e:
            logger.error(f"Error during initial stream check: {e}")
        
        # Start polling loop
        while self.is_active:
            try:
                is_active = await self.check_for_new_streams()
                if not is_active:
                    break
                await asyncio.sleep(15)  # Check every 15 seconds
            except Exception as e:
                logger.error(f"Error in stream polling loop: {e}")
                await asyncio.sleep(15)
