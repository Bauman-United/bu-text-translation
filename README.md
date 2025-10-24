# VK Translation Monitor Bot

A sophisticated Telegram bot that monitors VK (VKontakte) live streams and automatically detects and forwards sports score updates to a Telegram channel. The bot features intelligent score detection, celebration videos, and automatic stream discovery.

## Features

### 🎯 Core Functionality
- 📹 Monitor VK live streams for sports score comments
- ⚽ Intelligent score detection and parsing (format: "1-0", "2-1 богомолов")
- 🎉 Automatic celebration videos based on player surnames
- 📊 Support for multiple simultaneous stream monitoring
- 🔄 Real-time monitoring with 30-second intervals

### 🤖 Advanced Features
- 🔍 Automatic VK group stream discovery
- 📺 Live stream detection and monitoring
- 🛑 Automatic monitoring termination when streams end
- 📱 Direct user notifications for stream events
- 🎬 Player-specific celebration videos
- 📈 Stream status tracking and reporting

### 🏗️ Modular Architecture
- 🧩 Clean, modular code structure
- 🔧 Centralized configuration management
- 📝 Comprehensive error handling and logging
- 🧪 Testable and maintainable codebase
- 📚 Well-documented API and utilities

## Requirements

- Python 3.7+
- Telegram Bot Token
- Telegram Channel (where the bot can post messages)
- VK Access Token (optional but recommended)
- VK Group ID (for automatic stream discovery)

## Installation

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd bu-text-translation
   ```

2. **Create a virtual environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables:**
   - Copy `env.example` to `.env`:
     ```bash
     cp env.example .env
     ```
   - Edit `.env` and fill in your credentials:
     ```
     TELEGRAM_BOT_TOKEN=your_bot_token
     TELEGRAM_CHANNEL_ID=your_channel_id
     VK_ACCESS_TOKEN=your_vk_token
     VK_GROUP=your_vk_group_id_or_url
     MY_ID=your_telegram_user_id
     ```

## Configuration

### 1. Create a Telegram Bot

1. Open Telegram and search for [@BotFather](https://t.me/BotFather)
2. Send `/newbot` command
3. Follow the instructions to create your bot
4. Copy the bot token and add it to `.env` as `TELEGRAM_BOT_TOKEN`

### 2. Get Telegram Channel ID

1. Create a channel (or use an existing one)
2. Add your bot as an administrator to the channel
3. Add [@RawDataBot](https://t.me/RawDataBot) to your channel
4. The bot will show you the channel ID (usually starts with `-100`)
5. Copy the channel ID and add it to `.env` as `TELEGRAM_CHANNEL_ID`
6. Remove @RawDataBot from the channel

### 3. Get VK Access Token (Optional but Recommended)

1. Go to [VK Token Generator](https://vkhost.github.io/)
2. Select the following permissions:
   - `video` - to access video information
   - `offline` - for permanent access
3. Log in with your VK account
4. Copy the access token and add it to `.env` as `VK_ACCESS_TOKEN`

**Note:** Without a VK access token, you may have limited access to some videos, especially private ones.

### 4. Get VK Group ID (for automatic stream discovery)

1. Find your VK group URL (e.g., `https://vk.com/club123456789`)
2. Add the group ID or URL to `.env` as `VK_GROUP`
3. The bot will automatically monitor this group for new live streams

### 5. Get Your Telegram User ID

1. Send a message to [@userinfobot](https://t.me/userinfobot)
2. Copy your user ID and add it to `.env` as `MY_ID`

## Usage

1. **Start the bot:**
   ```bash
   python main.py
   ```

2. **Send commands to your bot on Telegram:**

   - `/start` - Show welcome message and available commands
   
   - `/monitor <vk_url>` - Start monitoring a VK live stream
     ```
     Example:
     /monitor https://vk.com/video-123456789_456123789
     ```
   
   - `/stop <vk_url>` - Stop monitoring a live stream
     ```
     Example:
     /stop https://vk.com/video-123456789_456123789
     ```
   
   - `/list` - List all active streams being monitored
   
   - `/group_status` - Check VK group monitoring status
   
   - `/catch_existing` - Start monitoring any currently live streams in the group

3. **The bot will:**
   - Automatically discover new live streams in your VK group
   - Monitor streams for score comments (format: "1-0", "2-1 богомолов")
   - Send celebration videos when your team scores
   - Notify you when streams start and end
   - Stop monitoring automatically when streams end

## How It Works

### 🎯 Score Detection System
1. **Stream Monitoring**: Bot monitors VK live streams for new comments
2. **Score Parsing**: Detects score comments in format "1-0", "2-1 богомолов"
3. **Smart Filtering**: Only processes comments with valid score format
4. **Celebration Videos**: Automatically attaches player-specific celebration videos
5. **Real-time Updates**: Checks for new comments every 30 seconds

### 🔍 Automatic Stream Discovery
1. **Group Monitoring**: Continuously monitors VK group for new live streams
2. **Stream Detection**: Automatically detects when new streams go live
3. **Auto-Start**: Automatically begins monitoring new streams
4. **End Detection**: Stops monitoring when streams end

### 📱 Notification System
- **Score Updates**: Sends formatted score messages to Telegram channel
- **Celebration Videos**: Attaches appropriate celebration videos based on player surname
- **Stream Events**: Notifies user when streams start/end
- **System Messages**: Provides monitoring status updates

## Score Comment Format

The bot detects and processes comments in the following formats:

```
1-0                    # Basic score
2-1 богомолов          # Score with player surname
3-0 писарев            # Score with different player
```

### 🎬 Celebration Videos

The bot includes celebration videos for specific players:
- **богомолов/багич** → `celebrations/богомолов.mp4`
- **заночуев** → `celebrations/заночуев.mp4`
- **панферов/панфёров** → `celebrations/панферов.mp4`
- **писарев/писарь** → `celebrations/писарев.mp4`
- **шевченко/шева** → `celebrations/шевченко.mp4`
- **Other players** → `celebrations/другие.mp4`

## Message Format

Score updates are sent to your channel in the following format:

```
⚽ Забиваем! Гол забил Богомолов. Счет: 2-1
[Celebration Video Attachment]
```

For opponent goals:
```
Пропускаем. Счет: 1-1
```

## Troubleshooting

### Bot doesn't respond
- Make sure the bot is running (`python main.py`)
- Check that your `TELEGRAM_BOT_TOKEN` is correct
- Verify that you're sending commands to the correct bot

### Comments not appearing in channel
- Ensure the bot is added as an administrator to your channel
- Verify that `TELEGRAM_CHANNEL_ID` is correct (should start with `-100`)
- Check the bot logs for errors

### "Access denied to video" error
- Add a valid `VK_ACCESS_TOKEN` to your `.env` file
- Make sure the video is public or you have access to it
- Check that your VK token has the `video` permission

### Bot can't find video
- Verify the VK URL format is correct
- Make sure the video/translation exists and is accessible
- Check that the URL is for a video, not a post or other content

## Project Structure

```
bu-text-translation/
├── main.py                    # Application entry point
├── config/                    # Configuration management
│   ├── __init__.py
│   └── settings.py           # Environment variables & settings
├── utils/                     # Common utilities
│   ├── __init__.py
│   └── url_parser.py         # URL parsing & score detection
├── api/                       # External API integrations
│   ├── __init__.py
│   └── vk_client.py          # VK API wrapper
├── handlers/                  # Telegram command handlers
│   ├── __init__.py
│   └── telegram_commands.py  # All bot commands
├── monitors/                  # VK monitoring functionality
│   ├── __init__.py
│   ├── translation_monitor.py    # Individual stream monitoring
│   └── group_stream_monitor.py  # Group stream discovery
├── celebrations/              # Player celebration videos
│   ├── богомолов.mp4
│   ├── заночуев.mp4
│   ├── панферов.mp4
│   ├── писарев.mp4
│   ├── шевченко.mp4
│   └── другие.mp4
├── requirements.txt           # Python dependencies
├── env.example               # Example environment configuration
├── bot_original.py           # Backup of original monolithic code
└── README.md                 # This file
```

### 🏗️ Architecture Overview

- **`main.py`**: Application entry point and bot initialization
- **`config/`**: Centralized configuration management with validation
- **`utils/`**: Reusable utility functions for URL parsing and score detection
- **`api/`**: Clean VK API wrapper with error handling
- **`handlers/`**: All Telegram command implementations
- **`monitors/`**: VK stream monitoring and group discovery logic
- **`celebrations/`**: Player-specific celebration video files

## Dependencies

- `python-telegram-bot` - Telegram Bot API wrapper
- `vk-api` - VK API wrapper
- `python-dotenv` - Environment variable management
- `requests` - HTTP library

## License

This project is provided as-is for personal use.

## Bot Commands Reference

### 📋 Available Commands

| Command | Description | Example |
|---------|-------------|---------|
| `/start` | Show welcome message and available commands | `/start` |
| `/monitor <url>` | Start monitoring a VK live stream | `/monitor https://vk.com/video-123456789_456123789` |
| `/stop <url>` | Stop monitoring a specific stream | `/stop https://vk.com/video-123456789_456123789` |
| `/list` | List all active streams being monitored | `/list` |
| `/group_status` | Check VK group monitoring status | `/group_status` |
| `/catch_existing` | Start monitoring any currently live streams in the group | `/catch_existing` |

### 🎯 Score Detection Features

- **Format Recognition**: Detects scores in format "1-0", "2-1 богомолов"
- **Player Recognition**: Supports multiple player surname variations
- **Smart Filtering**: Only processes valid score comments
- **Celebration Videos**: Automatic video attachment based on player
- **Real-time Updates**: 30-second monitoring intervals

### 🔧 Technical Notes

- The bot checks for comments every 30 seconds (improved from 60 seconds)
- Fetches up to 100 comments per check for optimal performance
- Multiple streams can be monitored simultaneously
- VK API rate limits are respected with proper error handling
- Automatic stream discovery with 15-second group polling
- Comprehensive logging and error handling throughout

## Support

For issues or questions, please check:
- VK API documentation: https://dev.vk.com/
- python-telegram-bot documentation: https://docs.python-telegram-bot.org/
- VK Token permissions: https://dev.vk.com/reference/access-rights

