"""
Utilities module for the VK Translation Monitor Bot.

This module contains common utility functions used throughout the application,
including URL parsing, text processing, and other helper functions.
"""

from .url_parser import extract_group_id
from .error_notifier import send_error_notification

__all__ = ['extract_group_id', 'send_error_notification']
