"""
URL parsing utilities for VK URLs.

This module provides functions to parse and extract information from various
VK URL formats, including video URLs and group URLs.
"""

import re
import logging

logger = logging.getLogger(__name__)


def extract_group_id(group_input: str) -> str:
    """
    Extract group ID from various VK URL formats.
    
    Args:
        group_input: VK group URL or ID in various formats
        
    Returns:
        Extracted group ID as string
        
    Examples:
        >>> extract_group_id("123456789")
        "123456789"
        >>> extract_group_id("https://vk.com/club123456789")
        "123456789"
        >>> extract_group_id("https://vk.com/public123456789")
        "123456789"
    """
    # Remove any whitespace
    group_input = group_input.strip()
    
    # If it's already just a number, return it
    if group_input.isdigit():
        return group_input
    
    # Extract from URL patterns
    # Pattern for vk.com/club123456789 or vk.com/public123456789
    club_match = re.search(r'vk\.com/(?:club|public)(\d+)', group_input)
    if club_match:
        return club_match.group(1)
    
    # Pattern for vk.com/groupname (we'll need to resolve this)
    # For now, assume it's a group ID if it contains vk.com
    if 'vk.com' in group_input:
        # Try to extract any number from the URL
        number_match = re.search(r'(\d+)', group_input)
        if number_match:
            return number_match.group(1)
    
    # If we can't extract, return the original input
    logger.warning(f"Could not extract group ID from: {group_input}")
    return group_input


def parse_video_url(translation_url: str) -> tuple:
    """
    Parse VK translation URL to extract owner_id and video_id.
    
    Args:
        translation_url: VK video URL
        
    Returns:
        Tuple of (owner_id, video_id)
        
    Raises:
        ValueError: If URL format is invalid
        
    Examples:
        >>> parse_video_url("https://vk.com/video-123456789_456123789")
        ("-123456789", "456123789")
    """
    # Example URL: https://vk.com/video-123456789_456123789
    # or https://vk.com/video?z=video-123456789_456123789
    match = re.search(r'video(-?\d+)_(\d+)', translation_url)
    if match:
        owner_id = match.group(1)
        video_id = match.group(2)
        logger.info(f"Parsed video: owner_id={owner_id}, video_id={video_id}")
        return owner_id, video_id
    else:
        raise ValueError("Invalid VK translation URL format")


def is_score_comment(text: str) -> bool:
    """
    Check if comment contains score information in format: {number}-{number} {surname}.
    
    Args:
        text: Comment text to check
        
    Returns:
        True if comment contains score information, False otherwise
        
    Examples:
        >>> is_score_comment("1-0")
        True
        >>> is_score_comment("2-1 богомолов")
        True
        >>> is_score_comment("Hello world")
        False
    """
    return parse_score_comment(text) is not None


def parse_score_comment(text: str) -> tuple:
    """
    Parse score comment and return (our_score, opponent_score, surname).
    
    Args:
        text: Comment text to parse
        
    Returns:
        Tuple of (our_score, opponent_score, surname) or None if not a score comment
        
    Examples:
        >>> parse_score_comment("1-0")
        (1, 0, "")
        >>> parse_score_comment("2-1 богомолов")
        (2, 1, "богомолов")
    """
    # Pattern: digits-digits (optional surname)
    # Examples: "1-0", "0-1", "1-0 богомолов", "2-1 писарев"
    score_pattern = r'^(\d+)-(\d+)(?:\s+(\w+))?$'
    match = re.match(score_pattern, text.strip())
    if match:
        our_score = int(match.group(1))
        opponent_score = int(match.group(2))
        surname = match.group(3) if match.group(3) else ""
        return (our_score, opponent_score, surname)
    return None
