"""
VK API client wrapper.

This module provides a clean interface to the VK API, handling authentication,
API calls, and error handling for VK-related operations.
"""

import vk_api
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class VKClient:
    """VK API client wrapper."""
    
    def __init__(self, access_token: Optional[str] = None):
        """
        Initialize VK API client.
        
        Args:
            access_token: VK API access token (optional, will use anonymous access if not provided)
        """
        self.access_token = access_token
        self.vk_session = None
        self.vk_api = None
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
    
    def get_video_info(self, owner_id: str, video_id: str) -> Optional[Dict]:
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
            
            video_info = self.vk_api.video.get(
                owner_id=owner_id,
                videos=f"{owner_id}_{video_id}"
            )
            
            if not video_info or 'items' not in video_info or len(video_info['items']) == 0:
                logger.error("Video not found or access denied")
                return None
            
            return video_info['items'][0]
            
        except vk_api.exceptions.ApiError as e:
            logger.error(f"VK API error getting video info: {e}")
            raise
        except Exception as e:
            logger.error(f"Error getting video info: {e}")
            raise
    
    def get_video_comments(self, owner_id: str, video_id: str, count: int = 100) -> List[Dict]:
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
            
            comments = self.vk_api.video.getComments(
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
            raise
        except Exception as e:
            logger.error(f"Error getting comments: {e}")
            raise
    
    def get_group_videos(self, group_id: str, count: int = 20) -> List[Dict]:
        """
        Get videos from a VK group.
        
        Args:
            group_id: VK group ID
            count: Number of videos to retrieve
            
        Returns:
            List of video dictionaries
        """
        try:
            # Check if we have access token for group operations
            if not self.access_token or not self.access_token.strip():
                logger.error("VK_ACCESS_TOKEN required for group video operations")
                raise ValueError("VK_ACCESS_TOKEN is required for group video operations")
            
            # Convert group_id to integer and make it negative for groups
            owner_id = -int(group_id)
            logger.info(f"Getting videos for group {group_id} (owner_id: {owner_id})")
            
            videos = self.vk_api.video.get(
                owner_id=owner_id,  # Negative integer for groups
                count=count,
                sort=2  # Sort by date (newest first)
            )
            
            if not videos or 'items' not in videos:
                logger.warning("No videos found in group or access denied")
                return []
            
            return videos['items']
            
        except vk_api.exceptions.ApiError as e:
            logger.error(f"VK API error getting group videos: {e}")
            raise
        except Exception as e:
            logger.error(f"Error getting group videos: {e}")
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
        
        # Check if it's a live stream
        return live_status == 1 or live_status_str == 'started'
    
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
