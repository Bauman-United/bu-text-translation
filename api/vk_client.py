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
from typing import Dict, List, Optional, Callable, Awaitable

from api.vk_auth import VKAuthError, refresh_access_token
from config.settings import Config
from utils.vk_token_store import VKTokens, load_tokens, save_from_response

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
    """
    VK API client wrapper.

    Authentication comes from `data/vk_token.json` when it exists — written
    either by the bot's /set_vk_token command (24h token, replaced by hand) or
    by scripts/vk_authorize.py (refreshable, needs an app that can register a
    redirect URI). A static VK_ACCESS_TOKEN still works as a fallback.
    """

    # Refresh is shared process-wide: several monitors run concurrently and must
    # not race each other into refreshing the same rotating refresh token.
    _refresh_lock = asyncio.Lock()
    # Notify the owner about a dead authorization only once per process.
    _auth_failure_reported = False

    def __init__(
        self,
        access_token: Optional[str] = None,
        error_notifier: Optional[Callable[[str, str, Optional[str], str], Awaitable[None]]] = None,
    ):
        """
        Initialize VK API client.

        Args:
            access_token: Static fallback token, used only when no stored token set exists
            error_notifier: Async callback (service_name, request_info, error_code, error_message)
        """
        self.error_notifier = error_notifier
        self.rate_limiter = VKRateLimiter()

        self._static_token = access_token
        self._tokens: Optional[VKTokens] = load_tokens()
        self._session_token: Optional[str] = None
        self.vk_session = None
        self.vk_api = None

        if self._tokens:
            logger.info(
                "VK auth: using stored token set%s"
                % (" (refreshable)" if self._tokens.can_refresh else " (no refresh token)")
            )
        elif self._static_token:
            logger.warning(
                "VK auth: using static VK_ACCESS_TOKEN — VK ID expires these after "
                "24h. Send the bot /set_vk_token to manage the token instead."
            )
        else:
            logger.error("VK auth: no credentials at all — VK calls will fail")

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    @property
    def access_token(self) -> Optional[str]:
        """Current access token, whichever source it came from."""
        if self._tokens:
            return self._tokens.access_token
        return self._static_token

    def _rebuild_session(self, token: str):
        """(Re)create the vk_api session bound to `token`."""
        self.vk_session = vk_api.VkApi(token=token)
        self.vk_api = self.vk_session.get_api()
        self._session_token = token

    async def _refresh_tokens(self) -> bool:
        """
        Swap the refresh token for a fresh access token.

        Returns:
            True when a new access token is in place, False when we cannot refresh.
        """
        async with self._refresh_lock:
            # Another coroutine may have refreshed while we waited for the lock.
            latest = load_tokens()
            if latest and latest.access_token != (self._tokens.access_token if self._tokens else None):
                if not latest.is_expired:
                    logger.info("VK auth: picked up token refreshed by another monitor")
                    self._tokens = latest
                    return True
                self._tokens = latest

            tokens = self._tokens
            if not tokens or not tokens.can_refresh:
                logger.error(
                    "VK auth: no refresh token available — run scripts/vk_authorize.py"
                )
                return False

            config = Config()
            if not config.VK_APP_ID:
                logger.error("VK auth: VK_APP_ID is not set, cannot refresh")
                return False

            logger.info("VK auth: refreshing access token")
            try:
                response = await _run_in_thread(
                    refresh_access_token,
                    config.VK_APP_ID,
                    tokens.refresh_token,
                    tokens.device_id,
                )
            except VKAuthError as e:
                logger.error(f"VK auth: refresh failed — {e}")
                return False
            except Exception as e:
                logger.error(f"VK auth: unexpected refresh error — {e}")
                return False

            self._tokens = save_from_response(response, fallback_device_id=tokens.device_id)
            logger.info(
                "VK auth: access token refreshed, valid for %s sec"
                % int(self._tokens.seconds_left or 0)
            )
            return True

    async def _ensure_session(self) -> None:
        """Make sure `self.vk_api` is bound to a live, non-expired token."""
        # Pick up a token set written after this client was built — /set_vk_token
        # replaces the file, and without this the client would keep using a dead
        # token (or the static fallback) until the process restarts.
        if self._tokens is None or self._tokens.is_expired:
            latest = load_tokens()
            if latest and not latest.is_expired:
                logger.info("VK auth: adopted token set from store")
                self._tokens = latest

        if self._tokens and self._tokens.is_expired:
            logger.info("VK auth: stored access token expired or about to expire")
            if not await self._refresh_tokens():
                message = (
                    "Токен VK истёк и не обновляется автоматически. "
                    "Пришли боту /set_vk_token, чтобы получить ссылку и вставить новый."
                )
                await self._report_auth_failure("token refresh", message)
                raise VKAuthError(message)

        token = self.access_token
        if not token or not token.strip():
            message = "Токена VK нет. Пришли боту /set_vk_token."
            await self._report_auth_failure("token lookup", message)
            raise VKAuthError(message)

        if token != self._session_token:
            self._rebuild_session(token)

    async def _report_auth_failure(self, request_info: str, message: str) -> None:
        """Tell the owner once that authorization is dead, instead of every 30s."""
        if VKClient._auth_failure_reported:
            return
        VKClient._auth_failure_reported = True
        if self.error_notifier:
            try:
                await self.error_notifier(
                    "VK API",
                    request_info,
                    "auth",
                    message,
                )
            except Exception as e:
                logger.error(f"Failed to report VK auth failure: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Generic call plumbing
    # ------------------------------------------------------------------

    async def _call(self, method_path: str, request_info: str, **params):
        """
        Execute one VK API method with rate limiting, token refresh and retries.

        Handles two recoverable conditions:
          * code 5  (auth failed)  — refresh the token once, then retry
          * code 29 (rate limit)   — exponential backoff, up to 3 attempts

        Args:
            method_path: Dotted VK method name, e.g. "wall.get"
            request_info: Human-readable description used in error notifications

        Raises:
            VKAuthError: authorization is dead and cannot be recovered
            vk_api.exceptions.ApiError: any other VK-side failure
        """
        rate_limit_retries = 0
        max_rate_limit_retries = 3
        auth_retried = False

        while True:
            await self._ensure_session()

            method = self.vk_api
            for part in method_path.split("."):
                method = getattr(method, part)

            logger.info(f"Making VK API request: {request_info}")
            await self.rate_limiter.wait_if_needed()
            try:
                result = await _run_in_thread(method, **params)
                logger.info(f"VK API request completed: {request_info}")
                return result
            except vk_api.exceptions.ApiError as e:
                error_code = getattr(e, "code", None)

                if error_code == 5 and not auth_retried:
                    # Token died mid-flight (expired early, or revoked).
                    auth_retried = True
                    logger.warning(
                        f"VK API auth error on {request_info}, attempting token refresh"
                    )
                    if await self._refresh_tokens():
                        continue
                    await self._report_auth_failure(request_info, str(e))
                    raise VKAuthError(f"VK authorization failed and refresh did not help: {e}")

                if error_code == 5:
                    await self._report_auth_failure(request_info, str(e))
                    raise VKAuthError(f"VK authorization failed: {e}")

                if error_code == 29:
                    logger.error(f"VK API rate limit on {request_info}: {e}")
                    if await self.rate_limiter.handle_rate_limit_error(
                        rate_limit_retries, max_rate_limit_retries
                    ):
                        rate_limit_retries += 1
                        continue
                    await self._notify_error(request_info, str(error_code), str(e))
                    raise

                logger.error(f"VK API error on {request_info}: {e}")
                await self._notify_error(request_info, str(error_code) if error_code else None, str(e))
                raise
            except VKAuthError:
                raise
            except Exception as e:
                logger.error(f"Error on {request_info}: {e}")
                await self._notify_error(request_info, None, str(e))
                raise
            finally:
                await self.rate_limiter.mark_call_complete()

    async def _notify_error(self, request_info: str, error_code: Optional[str], message: str):
        """Send an error notification, never letting the notifier break the call path."""
        if not self.error_notifier:
            logger.warning("Error notifier is not set for VK client")
            return
        try:
            await self.error_notifier("VK API", request_info, error_code, message)
        except Exception as e:
            logger.error(f"Failed to call error notifier: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_video_comments(self, owner_id: str, video_id: str, count: int = 100) -> List[Dict]:
        """
        Get the most recent comments for a video, oldest-first.

        VK caps `count` at 100 per call. Sorting descending and reversing gives us
        the newest 100 in one request — with a 30s poll interval the bot cannot
        fall behind, and long matches no longer get stuck on the first 100
        comments the way ascending order did.

        Args:
            owner_id: Video owner ID
            video_id: Video ID
            count: How many recent comments to fetch (max 100)

        Returns:
            List of comment dictionaries in chronological order
        """
        request_info = f"video.getComments(owner_id={owner_id}, video_id={video_id}, count={count})"
        comments = await self._call(
            "video.getComments",
            request_info,
            owner_id=owner_id,
            video_id=video_id,
            sort="desc",
            count=min(count, 100),
        )

        items = (comments or {}).get("items") or []
        # VK returned newest-first; callers expect chronological order.
        return list(reversed(items))

    async def get_group_wall_posts(self, group_id: str, count: int = 20) -> List[Dict]:
        """
        Get recent wall posts for a VK group.

        Args:
            group_id: VK group ID
            count: Number of posts to retrieve

        Returns:
            List of wall post dictionaries (newest first)
        """
        owner_id = -int(group_id)
        request_info = f"wall.get(owner_id={owner_id}, count={min(count, 100)}, filter=all)"
        wall_posts = await self._call(
            "wall.get",
            request_info,
            owner_id=owner_id,
            count=min(count, 100),
            filter="all",
        )

        items = (wall_posts or {}).get("items") or []
        if not items:
            logger.debug("wall.get returned no items")
        return items

    async def get_group_videos(self, group_id: str, count: int = 20) -> List[Dict]:
        """
        Get videos attached to a group's recent wall posts.

        Args:
            group_id: VK group ID
            count: Number of videos to retrieve

        Returns:
            List of video dictionaries
        """
        posts = await self.get_group_wall_posts(group_id, count=min(count * 2, 100))

        all_videos: List[Dict] = []
        owner_id = -int(group_id)
        for post in posts:
            for video_data in self.extract_videos_from_wall_post(post):
                video_data.setdefault("owner_id", owner_id)
                all_videos.append(video_data)

        if not all_videos:
            logger.warning("No videos found in group or access denied")
            return []

        logger.info(f"Total videos found: {len(all_videos)}")
        return all_videos

    def extract_videos_from_wall_post(self, post: Dict) -> List[Dict]:
        """
        Extract attached videos from a wall post.

        Note: video objects from wall attachments typically already include live fields
        (e.g. live/live_status/is_mobile_live) when applicable.
        """
        videos: List[Dict] = []

        def _extract_from_attachments(attachments: List[Dict]):
            for attachment in attachments or []:
                atype = attachment.get('type')
                if atype == 'video':
                    video_data = attachment.get('video') or {}
                    if video_data:
                        videos.append(video_data)
                elif atype == 'link':
                    # Sometimes a wall post contains a link to a video/live, not a direct video
                    # attachment. Parsing the link into a video object requires additional API
                    # calls (video.get) which we intentionally avoid here.
                    continue

        # Direct attachments on the post
        _extract_from_attachments((post or {}).get('attachments') or [])

        # Reposts: attachments can be inside copy_history (list of nested post objects)
        for parent in (post or {}).get('copy_history') or []:
            _extract_from_attachments((parent or {}).get('attachments') or [])

        return videos

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
