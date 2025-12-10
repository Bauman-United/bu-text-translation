"""
VK API client wrapper.

This module provides a clean interface to the VK API, handling authentication,
API calls, and error handling for VK-related operations.
"""

import vk_api
import logging
import asyncio
import sys
from typing import Dict, List, Optional, Tuple, Callable, Awaitable

logger = logging.getLogger(__name__)

# Compatibility: asyncio.to_thread was added in Python 3.9
if sys.version_info >= (3, 9):
    _run_in_thread = asyncio.to_thread
else:
    # Fallback for Python 3.7-3.8
    def _run_in_thread(func, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return loop.run_in_executor(None, lambda: func(*args, **kwargs))


class VKClient:
    """VK API client wrapper."""
    
    def __init__(self, access_token: Optional[str] = None, error_notifier: Optional[Callable[[str, str, Optional[str], str], Awaitable[None]]] = None):
        """
        Initialize VK API client.
        
        Args:
            access_token: VK API access token (optional, will use anonymous access if not provided)
            error_notifier: Async function to call when errors occur: (request_info, error_code, error_message)
        """
        self.access_token = access_token
        self.vk_session = None
        self.vk_api = None
        self.error_notifier = error_notifier
        self._initialize_vk()
    
    def _initialize_vk(self):
        """Initialize VK API session."""
        if self.access_token and self.access_token.strip():
            logger.info("Initializing VK API with access token")
            self.vk_session = vk_api.VkApi(token=self.access_token)
            self.vk_api = self.vk_session.get_api()
        else:
            logger.warning("VK_ACCESS_TOKEN not provided or empty, using anonymous access")
            self.vk_session = vk_api.VkApi()
            self.vk_api = self.vk_session.get_api()
    
    async def get_video_info(self, owner_id: str, video_id: str) -> Optional[Dict]:
        """
        Get video information from VK.
        
        Args:
            owner_id: Video owner ID
            video_id: Video ID
            
        Returns:
            Video information dictionary or None if not found
        """
        try:
            # Check if we have access token for video operations
            if not self.access_token or not self.access_token.strip():
                logger.error("VK_ACCESS_TOKEN required for video operations")
                raise ValueError("VK_ACCESS_TOKEN is required for video operations")
            
            # Run blocking vk_api call in thread pool to avoid blocking event loop
            video_info = await _run_in_thread(
                self.vk_api.video.get,
                owner_id=owner_id,
                videos=f"{owner_id}_{video_id}"
            )
            
            if not video_info or 'items' not in video_info or len(video_info['items']) == 0:
                logger.error("Video not found or access denied")
                return None
            
            return video_info['items'][0]
            
        except vk_api.exceptions.ApiError as e:
            logger.error(f"VK API error getting video info: {e}")
            error_code = getattr(e, 'code', None)
            error_code_str = str(error_code) if error_code is not None else None
            request_info = f"video.get(owner_id={owner_id}, videos={owner_id}_{video_id})"
            if self.error_notifier:
                await self.error_notifier("VK API", request_info, error_code_str, str(e))
            raise
        except Exception as e:
            logger.error(f"Error getting video info: {e}")
            request_info = f"video.get(owner_id={owner_id}, videos={owner_id}_{video_id})"
            if self.error_notifier:
                await self.error_notifier("VK API", request_info, None, str(e))
            raise
    
    async def get_video_comments(self, owner_id: str, video_id: str, count: int = 100) -> List[Dict]:
        """
        Get comments for a video.
        
        Args:
            owner_id: Video owner ID
            video_id: Video ID
            count: Number of comments to retrieve
            
        Returns:
            List of comment dictionaries
        """
        try:
            # Check if we have access token for comment operations
            if not self.access_token or not self.access_token.strip():
                logger.error("VK_ACCESS_TOKEN required for comment operations")
                raise ValueError("VK_ACCESS_TOKEN is required for comment operations")
            
            # Run blocking vk_api call in thread pool to avoid blocking event loop
            comments = await _run_in_thread(
                self.vk_api.video.getComments,
                owner_id=owner_id,
                video_id=video_id,
                sort='asc',
                count=count
            )
            
            if 'items' not in comments:
                return []
            
            return comments['items']
            
        except vk_api.exceptions.ApiError as e:
            logger.error(f"VK API error getting comments: {e}")
            error_code = getattr(e, 'code', None)
            error_code_str = str(error_code) if error_code is not None else None
            request_info = f"video.getComments(owner_id={owner_id}, video_id={video_id})"
            if self.error_notifier:
                await self.error_notifier("VK API", request_info, error_code_str, str(e))
            raise
        except Exception as e:
            logger.error(f"Error getting comments: {e}")
            request_info = f"video.getComments(owner_id={owner_id}, video_id={video_id})"
            if self.error_notifier:
                await self.error_notifier("VK API", request_info, None, str(e))
            raise
    
    async def get_group_videos(self, group_id: str, count: int = 20) -> List[Dict]:
        """
        Get videos from a VK group using multiple methods.
        
        Args:
            group_id: VK group ID
            count: Number of videos to retrieve
            
        Returns:
            List of video dictionaries
        """
        all_videos = []
        
        try:
            # Check if we have access token for group operations
            if not self.access_token or not self.access_token.strip():
                logger.error("VK_ACCESS_TOKEN required for group video operations")
                raise ValueError("VK_ACCESS_TOKEN is required for group video operations")
            
            # Convert group_id to integer and make it negative for groups
            owner_id = -int(group_id)
            logger.info(f"Getting videos for group {group_id} (owner_id: {owner_id})")
            
            # Method 1: Get videos from video.get() (videos uploaded to group's video section)
            try:
                # Run blocking vk_api call in thread pool to avoid blocking event loop
                videos = await _run_in_thread(
                    self.vk_api.video.get,
                    owner_id=owner_id,  # Negative integer for groups
                    count=count,
                    sort=2  # Sort by date (newest first)
                )
                
                if videos and 'items' in videos:
                    all_videos.extend(videos['items'])
                    logger.info(f"Found {len(videos['items'])} videos from video.get() with owner_id={owner_id}")
            except Exception as e:
                logger.warning(f"Error getting videos from video.get() with owner_id: {e}")
            
            
            # Method 2: Get videos from wall posts (live streams are often posted on wall)
            try:
                # Run blocking vk_api call in thread pool to avoid blocking event loop
                wall_posts = await _run_in_thread(
                    self.vk_api.wall.get,
                    owner_id=owner_id,
                    count=min(count * 2, 100),  # Get more posts to find videos
                    filter='all'  # Get all posts, not just owner's
                )
                
                if wall_posts and 'items' in wall_posts:
                    wall_videos = []
                    for post in wall_posts['items']:
                        # Check for video attachments in the post
                        attachments = post.get('attachments', [])
                        for attachment in attachments:
                            if attachment.get('type') == 'video':
                                video_data = attachment.get('video', {})
                                if video_data:
                                    # Ensure we have owner_id and id
                                    if 'owner_id' not in video_data:
                                        video_data['owner_id'] = owner_id
                                    wall_videos.append(video_data)
                    
                    if wall_videos:
                        logger.info(f"Found {len(wall_videos)} videos from wall posts")
                        # Add wall videos that aren't already in all_videos
                        existing_ids = {f"{v['owner_id']}_{v['id']}" for v in all_videos}
                        for wall_video in wall_videos:
                            video_id_str = f"{wall_video['owner_id']}_{wall_video['id']}"
                            if video_id_str not in existing_ids:
                                all_videos.append(wall_video)
            except Exception as e:
                logger.warning(f"Error getting videos from wall posts: {e}")
            
            if not all_videos:
                logger.warning("No videos found in group or access denied")
                return []
            
            logger.info(f"Total unique videos found: {len(all_videos)}")
            return all_videos
            
        except vk_api.exceptions.ApiError as e:
            logger.error(f"VK API error getting group videos: {e}")
            error_code = getattr(e, 'code', None)
            error_code_str = str(error_code) if error_code is not None else None
            request_info = f"video.get/wall.get(group_id={group_id})"
            if self.error_notifier:
                await self.error_notifier("VK API", request_info, error_code_str, str(e))
            raise
        except Exception as e:
            logger.error(f"Error getting group videos: {e}")
            request_info = f"video.get/wall.get(group_id={group_id})"
            if self.error_notifier:
                await self.error_notifier("VK API", request_info, None, str(e))
            raise
    
    def is_live_stream(self, video: Dict) -> bool:
        """
        Check if a video is a live stream.
        
        Args:
            video: Video dictionary from VK API
            
        Returns:
            True if video is a live stream, False otherwise
        """
        live_status = video.get('live')
        live_status_str = video.get('live_status', '')
        is_mobile_live = video.get('is_mobile_live', False)
        
        # Primary check: live field == 1 or live_status == 'started'
        # live can be: None (not a stream), 1 (live), 2 (finished)
        # live_status can be: '' (not a stream), 'started' (live), 'finished' (ended)
        is_live = live_status == 1 or live_status_str == 'started'
        
        # Additional check: is_mobile_live indicates a mobile live stream
        # BUT only trust it if live_status is NOT 'finished' (to avoid false positives on old streams)
        if is_mobile_live and live_status_str != 'finished':
            is_live = True
            logger.info(f"Video {video.get('id')} detected as live via is_mobile_live=True (live_status={live_status_str})")
        
        # Additional check: if live field exists and is 1, it's definitely live
        # Also check if the video type indicates it's a live stream
        video_type = video.get('type', '')
        if video_type == 'live' or (live_status is not None and live_status == 1):
            is_live = True
        
        # If live_status is explicitly 'finished', it's not live (even if is_mobile_live is True)
        if live_status_str == 'finished' and live_status != 1:
            is_live = False
        
        return is_live
    
    def is_stream_ended(self, video: Dict) -> bool:
        """
        Check if a live stream has ended.
        
        Args:
            video: Video dictionary from VK API
            
        Returns:
            True if stream has ended, False otherwise
        """
        live_status = video.get('live')
        live_status_str = video.get('live_status', '')
        
        return live_status == 2 or live_status_str == 'finished'
    
    def get_video_url(self, video: Dict) -> str:
        """
        Generate VK video URL from video dictionary.
        
        Args:
            video: Video dictionary from VK API
            
        Returns:
            VK video URL
        """
        return f"https://vk.com/video{video['owner_id']}_{video['id']}"
    
    def get_video_id(self, video: Dict) -> str:
        """
        Generate video ID string from video dictionary.
        
        Args:
            video: Video dictionary from VK API
            
        Returns:
            Video ID string in format "owner_id_video_id"
        """
        return f"{video['owner_id']}_{video['id']}"
