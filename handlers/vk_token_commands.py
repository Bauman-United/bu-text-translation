"""
Refreshing the VK token from Telegram.

VK ID caps implicit-flow tokens at 24 hours and its refresh flow needs a
redirect URI this application cannot register, so the token has to be replaced
by hand. This makes that a two-message exchange in the bot instead of an SSH
session: /set_vk_token returns the link, and /set_vk_token <what you pasted>
validates, stores and picks it up without a restart.
"""

import asyncio
import logging
import time

import vk_api
from telegram import Update
from telegram.ext import ContextTypes

from api.vk_auth import build_implicit_authorize_url, parse_implicit_redirect
from config.settings import Config
from utils.vk_token_store import VKTokens, load_tokens, save_tokens

logger = logging.getLogger(__name__)


def _is_owner(update: Update, config: Config) -> bool:
    """The VK token is a credential — only the owner may replace it."""
    user = update.effective_user
    if not user or not config.MY_ID:
        return False
    try:
        return int(user.id) == int(config.MY_ID)
    except (TypeError, ValueError):
        return False


def _verify_token(token: str) -> dict:
    """
    Call users.get to prove the token works before we store it.

    Blocking; call through a thread.
    """
    api = vk_api.VkApi(token=token, api_version="5.199").get_api()
    return api.users.get()[0]


def describe_token_state() -> str:
    """One-line human summary of the stored token, for status messages."""
    tokens = load_tokens()
    if not tokens:
        return "❌ Токена нет — пришли /set_vk_token"

    left = tokens.seconds_left
    if left is None:
        return "✅ Токен сохранён (срок неизвестен)"
    if left <= 0:
        return "❌ Токен истёк — пришли /set_vk_token"

    hours, minutes = divmod(int(left) // 60, 60)
    return f"✅ Токен жив ещё {hours} ч {minutes} мин"


async def set_vk_token_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /set_vk_token — send the link, or accept the token that came back."""
    config = Config()
    if not _is_owner(update, config):
        await update.message.reply_text("⛔ Эта команда доступна только владельцу бота.")
        return

    if not config.VK_APP_ID:
        await update.message.reply_text(
            "❌ Не задан VK_APP_ID — без него не собрать ссылку авторизации."
        )
        return

    # No argument: hand over the link.
    if not context.args:
        url = build_implicit_authorize_url(config.VK_APP_ID)
        await update.message.reply_text(
            "🔑 <b>Обновление токена VK</b>\n\n"
            "1. Открой ссылку и нажми «Разрешить»\n"
            "2. Тебя перекинет на пустую страницу\n"
            "3. Скопируй <b>весь адрес</b> из адресной строки и пришли его командой:\n"
            "<code>/set_vk_token вставь_сюда</code>\n\n"
            f"{url}\n\n"
            f"Сейчас: {describe_token_state()}",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return

    # Argument present: validate and store it.
    try:
        parsed = parse_implicit_redirect(" ".join(context.args))
    except ValueError as e:
        await update.message.reply_text(
            f"❌ {e}\n\nПришли /set_vk_token без аргументов, чтобы получить ссылку заново."
        )
        return

    await update.message.reply_text("⏳ Проверяю токен...")

    try:
        owner = await asyncio.to_thread(_verify_token, parsed.access_token)
    except Exception as e:
        code = getattr(e, "code", None)
        await update.message.reply_text(
            f"❌ VK отклонил токен (code={code}): {e}\n\n"
            "Пришли /set_vk_token без аргументов и получи свежую ссылку."
        )
        return

    expires_at = 0.0
    if parsed.expires_in:
        expires_at = time.time() + parsed.expires_in

    save_tokens(VKTokens(
        access_token=parsed.access_token,
        refresh_token=None,      # implicit flow issues none
        device_id=None,
        expires_at=expires_at,
        user_id=parsed.user_id or owner.get("id"),
    ))

    restarted = _resume_vk_monitoring()

    lifetime = describe_token_state()
    await update.message.reply_text(
        f"✅ <b>Токен сохранён</b>\n\n"
        f"Владелец: {owner.get('first_name', '')} {owner.get('last_name', '')}\n"
        f"{lifetime}\n"
        f"{'🔄 Мониторинг VK перезапущен.' if restarted else 'Мониторинг подхватит его при следующем запросе.'}",
        parse_mode="HTML",
    )
    logger.info("VK token replaced via /set_vk_token")


def _resume_vk_monitoring() -> bool:
    """
    Bring group polling back after it stopped on a dead token.

    Returns:
        True when polling was (re)started.
    """
    try:
        from api.vk_client import VKClient
        from handlers.telegram_commands import get_group_stream_monitor

        # Allow a future failure to notify again.
        VKClient._auth_failure_reported = False

        monitor = get_group_stream_monitor()
        if not monitor:
            return False
        monitor.is_active = True
        return monitor.ensure_polling()
    except Exception as e:
        logger.error(f"Could not resume VK monitoring after token update: {e}")
        return False
