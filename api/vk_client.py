"""
VK API client wrapper.

This module provides a clean interface to the VK API, handling authentication,
API calls, and error handling for VK-related operations.
"""

import vk_api
import logging
import asyncio
import sys
import time
from typing import Dict, List, Optional, Tuple, Callable, Awaitable

logger = logging.getLogger(__name__)


class VKRateLimiter:
    """
    Shared rate limiter for all VK API calls.
    Ensures all API requests are serialized and spaced out to avoid rate limits.
    """
    _instance = None
    _lock = asyncio.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._last_call_time = 0.0
        self._min_delay = 20.0  # Minimum 20 seconds between calls (0.05 calls/second max, 3 calls/minute max) - VK rate limit is very strict
        self._rate_limit_lock = asyncio.Lock()
        self._call_times = []  # Track call times for per-minute limiting
        self._max_calls_per_minute = 3  # VK typically allows 3 calls per minute
        self._initialized = True
    
    async def wait_if_needed(self):
        """
        Wait if necessary to maintain rate limit.
        Should be called before every VK API call.
        Returns a context manager that updates last_call_time when the call completes.
        """
        async with self._rate_limit_lock:
            current_time = time.time()
            
            # Clean up old call times (older than 60 seconds)
            self._call_times = [t for t in self._call_times if current_time - t < 60]
            
            # Check per-minute limit
            if len(self._call_times) >= self._max_calls_per_minute:
                # We've hit the per-minute limit, wait until the oldest call is 60 seconds old
                oldest_call = min(self._call_times)
                wait_until = oldest_call + 60
                wait_time = max(0, wait_until - current_time)
                if wait_time > 0:
                    logger.warning(f"Rate limiter: per-minute limit reached ({len(self._call_times)} calls in last minute), waiting {wait_time:.2f}s")
                    await asyncio.sleep(wait_time)
                    current_time = time.time()
                    # Clean up again after waiting
                    self._call_times = [t for t in self._call_times if current_time - t < 60]
            
            # Check per-call delay
            time_since_last_call = current_time - self._last_call_time
            if time_since_last_call < self._min_delay:
                wait_time = self._min_delay - time_since_last_call
                logger.info(f"Rate limiter: waiting {wait_time:.2f}s (last call was {time_since_last_call:.2f}s ago, need {self._min_delay}s)")
                await asyncio.sleep(wait_time)
                current_time = time.time()
            
            # Record this call time for per-minute tracking
            self._call_times.append(current_time)
            
            # Update last_call_time when call is allowed to proceed
            self._last_call_time = current_time
            logger.info(f"Rate limiter: allowing API call (next call must wait {self._min_delay}s, {len(self._call_times)} calls in last minute)")
    
    async def mark_call_complete(self):
        """
        Mark that an API call has completed.
        This ensures we track when calls actually finish, not just when they start.
        """
        async with self._rate_limit_lock:
            # Update last_call_time to current time to ensure proper spacing
            self._last_call_time = time.time()
            logger.debug(f"Rate limiter: API call completed, last_call_time updated to {self._last_call_time}")
    
    async def handle_rate_limit_error(self, retry_count: int = 0, max_retries: int = 3):
        """
        Handle rate limit error with exponential backoff.
        
        Args:
            retry_count: Current retry attempt
            max_retries: Maximum number of retries
            
        Returns:
            True if should retry, False otherwise
        """
        if retry_count >= max_retries:
            return False
        
        # Exponential backoff: 10s, 20s, 40s (increased to be more conservative)
        wait_time = 10 * (2 ** retry_count)
        logger.warning(f"Rate limit hit, waiting {wait_time}s before retry {retry_count + 1}/{max_retries}")
        await asyncio.sleep(wait_time)
        
        # Update last call time to prevent immediate new calls
        # Add extra delay after rate limit to be safe
        async with self._rate_limit_lock:
            self._last_call_time = time.time()
            # Add extra buffer - wait at least 10 seconds after rate limit before next call
            self._last_call_time += 10
        
        return True

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
    
    # Shared cache for video info across all instances
    _video_info_cache: Dict[str, Tuple[Dict, float]] = {}
    _cache_ttl = 30  # Cache video info for 30 seconds
    
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
        self.rate_limiter = VKRateLimiter()  # Shared rate limiter instance
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
    
    async def get_video_info(self, owner_id: str, video_id: str, use_cache: bool = True) -> Optional[Dict]:
        """
        Get video information from VK.
        
        Args:
            owner_id: Video owner ID
            video_id: Video ID
            use_cache: Whether to use cached data if available (default: True)
            
        Returns:
            Video information dictionary or None if not found
        """
        cache_key = f"{owner_id}_{video_id}"
        current_time = time.time()
        
        # Check cache first
        if use_cache and cache_key in self._video_info_cache:
            cached_info, cache_time = self._video_info_cache[cache_key]
            if current_time - cache_time < self._cache_ttl:
                logger.debug(f"Using cached video info for {cache_key}")
                return cached_info
            else:
                # Cache expired, remove it
                del self._video_info_cache[cache_key]
        
        retry_count = 0
        max_retries = 3
        
        while True:
            try:
                # Check if we have access token for video operations
                if not self.access_token or not self.access_token.strip():
                    logger.error("VK_ACCESS_TOKEN required for video operations")
                    raise ValueError("VK_ACCESS_TOKEN is required for video operations")
                
                # Wait for rate limiter before making API call
                request_info = f"video.get(owner_id={owner_id}, videos={owner_id}_{video_id})"
                logger.info(f"Making VK API request: {request_info}")
                await self.rate_limiter.wait_if_needed()
                
                try:
                    # Run blocking vk_api call in thread pool to avoid blocking event loop
                    logger.debug(f"Executing VK API request: {request_info}")
                    video_info = await _run_in_thread(
                        self.vk_api.video.get,
                        owner_id=owner_id,
                        videos=f"{owner_id}_{video_id}"
                    )
                    logger.info(f"VK API request completed: {request_info}")
                    
                    if not video_info or 'items' not in video_info or len(video_info['items']) == 0:
                        logger.error("Video not found or access denied")
                        return None
                    
                    result = video_info['items'][0]
                    
                    # Cache the result
                    self._video_info_cache[cache_key] = (result, current_time)
                    
                    # Clean up old cache entries (keep only last 100 entries)
                    if len(self._video_info_cache) > 100:
                        # Remove oldest entries
                        sorted_cache = sorted(self._video_info_cache.items(), key=lambda x: x[1][1])
                        for key, _ in sorted_cache[:-100]:
                            del self._video_info_cache[key]
                    
                    return result
                finally:
                    # Mark call as complete to update rate limiter timing
                    await self.rate_limiter.mark_call_complete()
                
            except vk_api.exceptions.ApiError as e:
                error_code = getattr(e, 'code', None)
                error_code_str = str(error_code) if error_code is not None else None
                request_info = f"video.get(owner_id={owner_id}, videos={owner_id}_{video_id})"
                
                # Handle rate limit errors with retry
                if error_code == 29:  # Rate limit error
                    logger.error(f"VK API rate limit error on request: {request_info} - Error: {e}")
                    if await self.rate_limiter.handle_rate_limit_error(retry_count, max_retries):
                        retry_count += 1
                        logger.info(f"Retrying VK API request: {request_info} (attempt {retry_count + 1}/{max_retries + 1})")
                        continue
                    else:
                        logger.error(f"VK API rate limit error after {max_retries} retries: {e} - Request: {request_info}")
                        if self.error_notifier:
                            try:
                                await self.error_notifier("VK API", request_info, error_code_str, str(e))
                            except Exception as notifier_error:
                                logger.error(f"Failed to call error notifier: {notifier_error}", exc_info=True)
                        raise
                else:
                    logger.error(f"VK API error getting video info: {e} - Request: {request_info}")
                    if self.error_notifier:
                        try:
                            await self.error_notifier("VK API", request_info, error_code_str, str(e))
                            logger.debug(f"Error notifier called for VK API error: {error_code_str}")
                        except Exception as notifier_error:
                            logger.error(f"Failed to call error notifier: {notifier_error}", exc_info=True)
                    else:
                        logger.warning("Error notifier is not set for VK client")
                    raise
            except Exception as e:
                request_info = f"video.get(owner_id={owner_id}, videos={owner_id}_{video_id})"
                logger.error(f"Error getting video info: {e} - Request: {request_info}")
                if self.error_notifier:
                    try:
                        await self.error_notifier("VK API", request_info, None, str(e))
                        logger.debug(f"Error notifier called for general error")
                    except Exception as notifier_error:
                        logger.error(f"Failed to call error notifier: {notifier_error}", exc_info=True)
                else:
                    logger.warning("Error notifier is not set for VK client")
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
        retry_count = 0
        max_retries = 3
        
        while True:
            try:
                # Check if we have access token for comment operations
                if not self.access_token or not self.access_token.strip():
                    logger.error("VK_ACCESS_TOKEN required for comment operations")
                    raise ValueError("VK_ACCESS_TOKEN is required for comment operations")
                
                # Wait for rate limiter before making API call
                request_info = f"video.getComments(owner_id={owner_id}, video_id={video_id}, count={count})"
                logger.info(f"Making VK API request: {request_info}")
                await self.rate_limiter.wait_if_needed()
                
                try:
                    # Run blocking vk_api call in thread pool to avoid blocking event loop
                    logger.debug(f"Executing VK API request: {request_info}")
                    comments = await _run_in_thread(
                        self.vk_api.video.getComments,
                        owner_id=owner_id,
                        video_id=video_id,
                        sort='asc',
                        count=count
                    )
                    logger.info(f"VK API request completed: {request_info}")
                    
                    if 'items' not in comments:
                        return []
                    
                    return comments['items']
                finally:
                    # Mark call as complete to update rate limiter timing
                    await self.rate_limiter.mark_call_complete()
                
            except vk_api.exceptions.ApiError as e:
                error_code = getattr(e, 'code', None)
                error_code_str = str(error_code) if error_code is not None else None
                request_info = f"video.getComments(owner_id={owner_id}, video_id={video_id})"
                
                # Handle rate limit errors with retry
                if error_code == 29:  # Rate limit error
                    logger.error(f"VK API rate limit error on request: {request_info} - Error: {e}")
                    if await self.rate_limiter.handle_rate_limit_error(retry_count, max_retries):
                        retry_count += 1
                        logger.info(f"Retrying VK API request: {request_info} (attempt {retry_count + 1}/{max_retries + 1})")
                        continue
                    else:
                        logger.error(f"VK API rate limit error after {max_retries} retries: {e} - Request: {request_info}")
                        if self.error_notifier:
                            await self.error_notifier("VK API", request_info, error_code_str, str(e))
                        raise
                else:
                    logger.error(f"VK API error getting comments: {e}")
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
            
            # Get videos from wall posts (live streams are often posted on wall)
            try:
                # Wait for rate limiter before making API call
                request_info = f"wall.get(owner_id={owner_id}, count={min(count * 2, 100)}, filter=all)"
                logger.info(f"Making VK API request: {request_info}")
                await self.rate_limiter.wait_if_needed()
                
                try:
                    # Run blocking vk_api call in thread pool to avoid blocking event loop
                    logger.debug(f"Executing VK API request: {request_info}")
                    wall_posts = await _run_in_thread(
                        self.vk_api.wall.get,
                        owner_id=owner_id,
                        count=min(count * 2, 100),  # Get more posts to find videos
                        filter='all'  # Get all posts, not just owner's
                    )
                    logger.info(f"VK API request completed: {request_info}")
                finally:
                    # Mark call as complete to update rate limiter timing
                    await self.rate_limiter.mark_call_complete()
                
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
                        all_videos.extend(wall_videos)
            except Exception as e:
                logger.warning(f"Error getting videos from wall posts: {e}")
            
            if not all_videos:
                logger.warning("No videos found in group or access denied")
                return []
            
            logger.info(f"Total unique videos found: {len(all_videos)}")
            return all_videos
            
        except vk_api.exceptions.ApiError as e:
            error_code = getattr(e, 'code', None)
            error_code_str = str(error_code) if error_code is not None else None
            request_info = f"wall.get(group_id={group_id})"
            
            # Handle rate limit errors - don't retry here, let caller handle it
            if error_code == 29:
                logger.warning(f"VK API rate limit error getting group videos: {e}")
            else:
                logger.error(f"VK API error getting group videos: {e}")
            
            if self.error_notifier:
                await self.error_notifier("VK API", request_info, error_code_str, str(e))
            raise
        except Exception as e:
            logger.error(f"Error getting group videos: {e}")
            request_info = f"wall.get(group_id={group_id})"
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
