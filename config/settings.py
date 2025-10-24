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
        self.VK_ACCESS_TOKEN = os.getenv('VK_ACCESS_TOKEN')
        self.VK_GROUP = os.getenv('VK_GROUP')
        
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
        
        # VK_ACCESS_TOKEN and VK_GROUP are optional
        if not self.VK_ACCESS_TOKEN:
            logging.warning("VK_ACCESS_TOKEN not provided, VK API will use anonymous access")
        
        if not self.VK_GROUP:
            logging.warning("VK_GROUP not configured, group stream monitoring will be disabled")
    
    def _setup_logging(self):
        """Configure application logging."""
        logging.basicConfig(
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            level=logging.INFO
        )
        self.logger = logging.getLogger(__name__)
    
    @property
    def is_vk_configured(self) -> bool:
        """Check if VK API is properly configured."""
        return bool(self.VK_ACCESS_TOKEN)
    
    @property
    def is_group_monitoring_configured(self) -> bool:
        """Check if VK group monitoring is configured."""
        return bool(self.VK_GROUP)
