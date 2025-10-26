"""
Main application entry point for the VK Translation Monitor Bot.

This module initializes the bot, sets up command handlers, and starts
the monitoring services.
"""

import asyncio
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler

from config.settings import Config
from handlers.telegram_commands import (
    start_command,
    monitor_command,
    stop_command,
    list_command,
    group_status_command,
    catch_existing_command,
    set_group_stream_monitor
)
from monitors.group_stream_monitor import VKGroupStreamMonitor

logger = logging.getLogger(__name__)


def main():
    """Start the bot."""
    try:
        # Initialize configuration
        config = Config()
        logger.info("Configuration loaded successfully")
        
        # Create application
        application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
        
        # Add command handlers
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("monitor", monitor_command))
        application.add_handler(CommandHandler("stop", stop_command))
        application.add_handler(CommandHandler("list", list_command))
        application.add_handler(CommandHandler("group_status", group_status_command))
        application.add_handler(CommandHandler("catch_existing", catch_existing_command))
        
        # Add error handler
        async def error_handler(update, context):
            """Handle errors."""
            logger.error(f"Update {update} caused error {context.error}")
        
        application.add_error_handler(error_handler)
        
        # Setup post-initialization
        async def post_init(application):
            """Post-initialization setup for group monitoring."""
            if config.is_group_monitoring_configured:
                try:
                    group_stream_monitor = VKGroupStreamMonitor(
                        config.VK_GROUP, 
                        config.TELEGRAM_CHANNEL_ID, 
                        application, 
                        int(config.MY_ID)
                    )
                    set_group_stream_monitor(group_stream_monitor)
                    
                    # Start group monitoring in background
                    asyncio.create_task(group_stream_monitor.start_polling())
                    logger.info(f"Started VK group stream monitoring for group {config.VK_GROUP}")
                except Exception as e:
                    logger.error(f"Error starting group stream monitoring: {e}")
            else:
                logger.warning("VK_GROUP not configured, group stream monitoring disabled")
        
        # Add post initialization handler
        application.post_init = post_init
        
        # Start the bot
        logger.info("Bot started successfully")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        raise


if __name__ == '__main__':
    main()
