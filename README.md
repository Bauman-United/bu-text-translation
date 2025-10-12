# VK Translation Monitor Bot

A Telegram bot that monitors VK (VKontakte) video translations (live streams) and automatically sends new comments to a specified Telegram channel. The bot checks for new comments every minute and stops monitoring when the translation ends.

## Features

- 📹 Monitor VK video translations (live streams) for new comments
- 💬 Automatically forward new comments to a Telegram channel
- ⏱️ Checks for new comments every 60 seconds
- 🛑 Automatically stops monitoring when the translation ends
- 👥 Shows comment author name and timestamp
- 📊 Support for multiple simultaneous translations

## Requirements

- Python 3.7+
- Telegram Bot Token
- Telegram Channel (where the bot can post messages)
- VK Access Token (optional but recommended)

## Installation

1. **Clone the repository:**
   ```bash
   cd /Users/alex/WebstormProjects/bu-text-translation
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

## Usage

1. **Start the bot:**
   ```bash
   python bot.py
   ```

2. **Send commands to your bot on Telegram:**

   - `/start` - Show welcome message and available commands
   
   - `/monitor <vk_url>` - Start monitoring a VK translation
     ```
     Example:
     /monitor https://vk.com/video-123456789_456123789
     ```
   
   - `/stop <vk_url>` - Stop monitoring a translation
     ```
     Example:
     /stop https://vk.com/video-123456789_456123789
     ```
   
   - `/list` - List all active translations being monitored

3. **The bot will:**
   - Start checking the translation for new comments every minute
   - Send new comments to your configured Telegram channel
   - Automatically stop monitoring when the translation ends
   - Notify you in the channel when monitoring starts/stops

## How It Works

1. You send the bot a VK translation (live stream) URL
2. The bot parses the URL to extract video ID and owner ID
3. It fetches existing comments to establish a baseline
4. Every 60 seconds, it checks for new comments
5. When new comments are detected, they are formatted and sent to your Telegram channel
6. The bot also checks if the translation is still live
7. When the translation ends, the bot stops monitoring and notifies you

## Comment Format

Comments are sent to your channel in the following format:

```
💬 New Comment

👤 [Author Name]
🕐 [Date and Time]

[Comment Text]
```

## Troubleshooting

### Bot doesn't respond
- Make sure the bot is running (`python bot.py`)
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
├── bot.py              # Main bot application
├── requirements.txt    # Python dependencies
├── env.example        # Example environment configuration
├── .env               # Your environment configuration (not in git)
├── .gitignore         # Git ignore rules
└── README.md          # This file
```

## Dependencies

- `python-telegram-bot` - Telegram Bot API wrapper
- `vk-api` - VK API wrapper
- `python-dotenv` - Environment variable management
- `requests` - HTTP library

## License

This project is provided as-is for personal use.

## Notes

- The bot checks for comments every 60 seconds. You can modify this interval in `bot.py` (line with `await asyncio.sleep(60)`)
- The bot fetches up to 100 comments per check. For very active translations, some comments might be missed
- Multiple translations can be monitored simultaneously
- The bot uses VK API which has rate limits. Don't monitor too many translations at once

## Support

For issues or questions, please check:
- VK API documentation: https://dev.vk.com/
- python-telegram-bot documentation: https://docs.python-telegram-bot.org/
- VK Token permissions: https://dev.vk.com/reference/access-rights

