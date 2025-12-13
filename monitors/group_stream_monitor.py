"""
VK Group Stream Monitor for tracking new live streams.

This module contains the VKGroupStreamMonitor class that monitors VK groups
for new live streams and automatically starts monitoring them.
"""

import asyncio
import logging
from typing import Set

import vk_api

from telegram.ext import Application

from api.vk_client import VKClient
from utils.url_parser import extract_group_id
from monitors.translation_monitor import VKTranslationMonitor
from config.settings import Config
from utils.error_notifier import send_error_notification

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
        
        # Create error notifier for VK client
        async def vk_error_notifier(service_name, request_info, error_code, error_message):
            await send_error_notification(self.app, self.user_id, service_name, request_info, error_code, error_message)
        
        self.vk_client = VKClient(config.VK_ACCESS_TOKEN, error_notifier=vk_error_notifier)
    
    async def check_for_new_streams(self) -> bool:
        """
        Check for new live streams in the VK group.
        
        Returns:
            True if monitoring should continue, False if stopped
        """
        try:
            # Check if we already have an active stream being monitored
            # If yes, skip checking for new streams to avoid unnecessary API calls
            from handlers.telegram_commands import get_active_translations
            active_translations = get_active_translations()
            
            if active_translations:
                logger.debug(f"Skipping new stream check - already monitoring {len(active_translations)} stream(s)")
                return True
            
            logger.info(f"Checking for new streams in group {self.group_id}")
            
            # Get videos from the group
            videos = await self.vk_client.get_group_videos(self.group_id, count=50)
            
            if not videos:
                logger.warning("No videos found in group or access denied")
                return True
            
            # Filter videos that might be live based on wall data
            # Sort by date (newest first) - videos from wall.get() are already sorted by date
            potential_live_videos = []
            for video in videos:
                # Check if video might be live based on wall data
                live_status = video.get('live')
                live_status_str = video.get('live_status', '')
                is_mobile_live = video.get('is_mobile_live', False)
                
                # Include videos that might be live or recently finished
                if (live_status == 1 or live_status_str == 'started' or 
                    (is_mobile_live and live_status_str != 'finished') or
                    live_status_str == 'finished'):  # Include finished to detect ended streams
                    potential_live_videos.append(video)
            
            new_streams = []
            ended_streams = []
            
            # Only check and process the most recent video that appears to be live
            if potential_live_videos:
                # Get the first (most recent) potential live video
                video_to_check = potential_live_videos[0]
                video_id = self.vk_client.get_video_id(video_to_check)
                logger.info(f"Checking most recent potential live video: {video_id}")
                
                # Check if this video is already being monitored by a translation monitor
                stream_url = self.vk_client.get_video_url(video_to_check)
                
                if stream_url in active_translations:
                    # Video is already being monitored, skip it entirely
                    # Don't check for new streams if we already have one being monitored
                    logger.debug(f"Video {video_id} is already being monitored, skipping to avoid checking for new streams")
                    return True
                
                # Video is not being monitored yet
                # We use wall data to determine if it's live - no need for additional video.get call
                # The wall.get response already contains live status fields (live, live_status, is_mobile_live)
                logger.debug(f"Video {video_id} not yet monitored, using wall data to determine live status")
                
                # Process only this video for new/ended stream detection
                title = video_to_check.get('title', 'No title')
                
                # Check if it's a live stream
                if self.vk_client.is_live_stream(video_to_check):
                    if video_id not in self.seen_streams:
                        logger.info(f"NEW LIVE STREAM DETECTED: {video_id} - {title}")
                        self.seen_streams.add(video_id)
                        new_streams.append(video_to_check)
                    else:
                        logger.debug(f"Live stream already seen: {video_id}")
                elif self.vk_client.is_stream_ended(video_to_check) and video_id in self.seen_streams:
                    # Stream ended
                    logger.info(f"STREAM ENDED: {video_id} - {title}")
                    ended_streams.append(video_to_check)
                    self.seen_streams.discard(video_id)  # Remove from seen streams
            else:
                logger.debug("No potential live videos found in wall posts")
            
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
            
            # Add delay before starting translation monitor to avoid concurrent API calls
            # This ensures the group monitor's current API call cycle completes first
            await asyncio.sleep(2)
            
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
        """Start polling for new streams every 30 seconds."""
        logger.info(f"Starting VK group stream monitoring for group {self.group_id}")
        await self.send_notification(
            f"✅ Started monitoring VK group {self.group_id} for new live streams\n"
            f"⏱ Checking every 30 seconds"
        )
        
        # Initial check to populate seen_streams and process existing streams
        try:
            videos = await self.vk_client.get_group_videos(self.group_id, count=50)
            if videos:
                existing_streams = []
                
                # Filter videos that might be live based on wall data
                potential_live_videos = []
                for video in videos:
                    live_status = video.get('live')
                    live_status_str = video.get('live_status', '')
                    is_mobile_live = video.get('is_mobile_live', False)
                    
                    # Include videos that might be live
                    if (live_status == 1 or live_status_str == 'started' or 
                        (is_mobile_live and live_status_str != 'finished')):
                        potential_live_videos.append(video)
                
                # Only check the most recent potential live video
                if potential_live_videos:
                    video_to_check = potential_live_videos[0]
                    video_id = self.vk_client.get_video_id(video_to_check)
                    logger.info(f"Initial check: verifying most recent potential live video: {video_id}")
                    
                    # Skip get_video_info during initial check to avoid rate limits
                    # We already have enough info from wall posts to determine if it's live
                    # The detailed check will happen in the regular polling cycle
                    logger.debug("Skipping detailed video info check during initial check to avoid rate limits")
                
                # Check all videos (using wall data or updated data) for live streams
                for video in videos:
                    if self.vk_client.is_live_stream(video):
                        video_id = self.vk_client.get_video_id(video)
                        self.seen_streams.add(video_id)
                        existing_streams.append(video)
                
                logger.info(f"Found {len(existing_streams)} existing live streams")
                
                # Process existing streams - start monitoring them and process their comments
                for stream in existing_streams:
                    await self.handle_new_stream(stream)
                    
        except Exception as e:
            logger.error(f"Error during initial stream check: {e}")
        
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
