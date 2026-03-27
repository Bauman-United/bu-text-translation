"""
Main application entry point for the VK Translation Monitor Bot.

This module initializes the bot, sets up command handlers, and starts
the monitoring services.
"""

import asyncio
import logging
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters

from config.settings import Config
from handlers.telegram_commands import (
    start_command,
    monitor_command,
    stop_command,
    list_command,
    group_status_command,
    catch_existing_command,
    set_game_command,
    games_command,
    match_command,
    set_game_day_callback,
    delete_game_callback,
    game_type_callback,
    set_parse_mode_callback,
    game_time_input_handler,
    remove_translation_callback,
    set_group_stream_monitor,
    start_pending_site_monitors,
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
        application.add_handler(CommandHandler("set_game", set_game_command))
        application.add_handler(CommandHandler("games", games_command))
        application.add_handler(CommandHandler("match", match_command))
        
        # Callback query handlers
        application.add_handler(CallbackQueryHandler(remove_translation_callback, pattern="^remove:"))
        application.add_handler(CallbackQueryHandler(set_game_day_callback, pattern="^set_game_day:"))
        application.add_handler(CallbackQueryHandler(delete_game_callback, pattern="^del_game:"))
        application.add_handler(CallbackQueryHandler(game_type_callback, pattern="^game_type:"))
        application.add_handler(CallbackQueryHandler(set_parse_mode_callback, pattern="^set_parse:"))

        # Catch-all plain-text handler (game time + match URL input)
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, game_time_input_handler)
        )
        
        # Error handler
        async def error_handler(update, context):
            logger.error(f"Update {update} caused error {context.error}")
        
        application.add_error_handler(error_handler)
        
        # Post-initialization
        async def post_init(application):
            commands = [
                BotCommand("start", "Start the bot and see available commands"),
                BotCommand("monitor", "Start monitoring a VK translation URL"),
                BotCommand("stop", "Stop monitoring a translation URL"),
                BotCommand("list", "List all active translations being monitored"),
                BotCommand("group_status", "Check VK group monitoring status"),
                BotCommand("catch_existing", "Start monitoring any currently live streams"),
                BotCommand("set_game", "Schedule a game time (VK monitoring window)"),
                BotCommand("games", "List scheduled games and delete them"),
                BotCommand("match", "Parse match page and post goal commentary"),
            ]
            try:
                await application.bot.set_my_commands(commands)
                logger.info("Bot commands menu set successfully")
            except Exception as e:
                logger.error(f"Error setting bot commands menu: {e}")
            
            # Start VK group stream monitoring
            if config.is_group_monitoring_configured:
                try:
                    gsm = VKGroupStreamMonitor(
                        config.VK_GROUP, 
                        config.TELEGRAM_CHANNEL_ID, 
                        application, 
                        int(config.MY_ID)
                    )
                    set_group_stream_monitor(gsm)
                    asyncio.create_task(gsm.start_polling())
                    logger.info(f"Started VK group stream monitoring for group {config.VK_GROUP}")
                except Exception as e:
                    logger.error(f"Error starting group stream monitoring: {e}")
            else:
                logger.warning("VK_GROUP not configured, group stream monitoring disabled")

            # Resume site monitors for existing site-mode schedules
            try:
                start_pending_site_monitors(application, int(config.MY_ID))
            except Exception as e:
                logger.error(f"Error starting pending site monitors: {e}")
        
        application.post_init = post_init
        
        # Start the bot
        logger.info("Bot started successfully")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        raise


if __name__ == '__main__':
    main()
