"""
Error notification utility for sending error messages via Telegram.

This module provides functionality to send error notifications to the bot owner
when API errors occur.
"""

import logging
from typing import Optional, Callable, Awaitable
from telegram.ext import Application

logger = logging.getLogger(__name__)


async def send_error_notification(
    app: Optional[Application],
    user_id: Optional[int],
    service_name: str,
    request_info: str,
    error_code: Optional[str],
    error_message: str
):
    """
    Send error notification to the user via Telegram.
    
    Args:
        app: Telegram application instance
        user_id: Telegram user ID to send notification to
        service_name: Name of the service (e.g., "VK API", "OpenAI API")
        request_info: Information about the request that failed
        error_code: Error code if available
        error_message: Error message
    """
    if not app or not user_id:
        logger.warning(f"Cannot send error notification: app={app is not None}, user_id={user_id}")
        return
    
    try:
        message = (
            f"⚠️ <b>API Error Notification</b>\n\n"
            f"🔧 Service: {service_name}\n"
            f"📝 Request: {request_info}\n"
        )
        
        if error_code:
            message += f"🔢 Error Code: {error_code}\n"
        
        message += f"❌ Error: {error_message}"
        
        await app.bot.send_message(
            chat_id=user_id,
            text=message,
            parse_mode='HTML'
        )
        
        logger.info(f"Error notification sent to user {user_id} for {service_name}: {error_message[:50]}...")
    except Exception as e:
        logger.error(f"Failed to send error notification to user {user_id}: {e}", exc_info=True)
        # Don't re-raise - we don't want error notification failures to break the main flow


