"""
Application configuration and settings.

This module handles environment variable loading, configuration validation,
and provides a centralized configuration object for the entire application.
"""

import os
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Config:
    """Application configuration class."""
    
    def __init__(self):
        """Initialize configuration with environment variables."""
        # Telegram Bot Configuration
        self.TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
        self.TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')
        self.MY_ID = os.getenv('MY_ID')
        
        # VK API Configuration
        # VK_ACCESS_TOKEN is only a fallback now: VK ID tokens obtained through
        # the implicit flow expire after 24h. The bot prefers the refreshable
        # token set in data/vk_token.json (see scripts/vk_authorize.py).
        self.VK_ACCESS_TOKEN = os.getenv('VK_ACCESS_TOKEN')
        self.VK_APP_ID = os.getenv('VK_APP_ID')
        self.VK_GROUP = os.getenv('VK_GROUP')
        
        # OpenAI Configuration
        self.OPENAI_KEY = os.getenv('OPENAI_KEY')
        
        # Validate required configuration
        self._validate_config()
        
        # Setup logging
        self._setup_logging()
    
    def _validate_config(self):
        """Validate that all required configuration is present."""
        required_vars = [
            'TELEGRAM_BOT_TOKEN',
            'TELEGRAM_CHANNEL_ID', 
            'MY_ID'
        ]
        
        missing_vars = []
        for var in required_vars:
            if not getattr(self, var):
                missing_vars.append(var)
        
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
        
        # VK credentials are optional at config level, but VK monitoring needs
        # either a stored refreshable token set or a static VK_ACCESS_TOKEN.
        from utils.vk_token_store import load_tokens

        if not load_tokens() and not self.VK_ACCESS_TOKEN:
            logging.warning(
                "No VK tokens available (neither data/vk_token.json nor VK_ACCESS_TOKEN). "
                "Run scripts/vk_authorize.py — VK monitoring is disabled until then."
            )

        if not self.VK_APP_ID:
            logging.warning(
                "VK_APP_ID not set — the bot cannot refresh VK tokens automatically"
            )

        if not self.VK_GROUP:
            logging.warning("VK_GROUP not configured, group stream monitoring will be disabled")
        
        if not self.OPENAI_KEY:
            logging.warning("OPENAI_KEY not provided, GPT commentary will be disabled")
    
    def _setup_logging(self):
        """Configure application logging."""
        logging.basicConfig(
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            level=logging.INFO
        )
        # httpx logs the full request URL, which for Telegram embeds the bot
        # token in every single line. Keep credentials out of the log file.
        logging.getLogger('httpx').setLevel(logging.WARNING)
        self.logger = logging.getLogger(__name__)
    
    @property
    def is_vk_configured(self) -> bool:
        """Check if any VK credentials are available (stored set or static token)."""
        from utils.vk_token_store import load_tokens
        return bool(load_tokens() or self.VK_ACCESS_TOKEN)
    
    @property
    def is_group_monitoring_configured(self) -> bool:
        """Check if VK group monitoring is configured."""
        return bool(self.VK_GROUP)
    
    @property
    def is_openai_configured(self) -> bool:
        """Check if OpenAI is properly configured."""
        return bool(self.OPENAI_KEY)
