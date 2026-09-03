"""
Refreshing the VK token from Telegram.

Two modes, picked by whether VK_REDIRECT_URI is configured:

* Code+PKCE flow (VK_REDIRECT_URI set): the bot sends the id.vk.com link, the
  user authorizes and pastes the redirect address back, and the SERVER
  exchanges the code for tokens. The access token is minted to the server (no
  IP binding) and comes with a refresh token, so it renews automatically.

* Implicit flow (no VK_REDIRECT_URI): the classic 24h token. It is bound to
  the IP that opened the link, so it only works when authorized through the
  server's IP (e.g. via an SSH SOCKS proxy).

Either way it is a two-message exchange: /set_vk_token returns the link, and
/set_vk_token <what you pasted> validates, stores and picks it up without a
restart.
"""

import asyncio
import logging
import time
from typing import Optional

import vk_api
from telegram import Update
from telegram.ext import ContextTypes

from api.vk_auth import (
    AuthCode,
    ImplicitToken,
    VKAuthError,
    build_authorize_url,
    build_implicit_authorize_url,
    exchange_code_for_tokens,
    generate_pkce_pair,
    generate_state,
    parse_redirect,
)
from config.settings import Config
from utils.vk_token_store import VKTokens, load_tokens, save_from_response, save_tokens

logger = logging.getLogger(__name__)

# PKCE state for the link handed out last. The verifier must survive between
# the two /set_vk_token messages; a bot restart in between loses it, and the
# user is asked for a fresh link. Only the owner can use the command, so one
# process-wide slot is enough.
_pending_auth: Optional[dict] = None


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
        if tokens.can_refresh:
            return "♻️ Токен истёк, но обновится автоматически при следующем запросе"
        return "❌ Токен истёк — пришли /set_vk_token"

    hours, minutes = divmod(int(left) // 60, 60)
    suffix = ", обновляется автоматически" if tokens.can_refresh else ""
    return f"✅ Токен жив ещё {hours} ч {minutes} мин{suffix}"


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
        await _send_authorize_link(update, config)
        return

    # Argument present: validate and store it.
    try:
        parsed = parse_redirect(" ".join(context.args))
    except ValueError as e:
        await update.message.reply_text(
            f"❌ {e}\n\nПришли /set_vk_token без аргументов, чтобы получить ссылку заново."
        )
        return

    await update.message.reply_text("⏳ Проверяю токен...")

    if isinstance(parsed, AuthCode):
        tokens = await _store_from_code(update, config, parsed)
    else:
        tokens = await _store_from_implicit(update, parsed)
    if tokens is None:
        return

    restarted = _resume_vk_monitoring()

    lifetime = describe_token_state()
    await update.message.reply_text(
        f"✅ <b>Токен сохранён</b>\n\n"
        f"{lifetime}\n"
        f"{'🔄 Мониторинг VK перезапущен.' if restarted else 'Мониторинг подхватит его при следующем запросе.'}",
        parse_mode="HTML",
    )
    logger.info("VK token replaced via /set_vk_token")


async def _send_authorize_link(update: Update, config: Config):
    """Reply with the authorization link for whichever flow is configured."""
    global _pending_auth

    if config.VK_REDIRECT_URI:
        verifier, challenge = generate_pkce_pair()
        state = generate_state()
        _pending_auth = {
            "verifier": verifier,
            "state": state,
            "redirect_uri": config.VK_REDIRECT_URI,
        }
        url = build_authorize_url(
            config.VK_APP_ID, challenge, state, redirect_uri=config.VK_REDIRECT_URI
        )
        flow_note = "Токен выпустит сервер — привязки к твоему IP не будет."
    else:
        _pending_auth = None
        url = build_implicit_authorize_url(config.VK_APP_ID)
        flow_note = (
            "⚠️ Implicit flow: токен привяжется к IP, с которого открыта ссылка. "
            "Открывай её через прокси сервера, иначе VK отклонит токен."
        )

    await update.message.reply_text(
        "🔑 <b>Обновление токена VK</b>\n\n"
        "1. Открой ссылку и нажми «Разрешить»\n"
        "2. Тебя перекинет на страницу редиректа (она может быть пустой или с ошибкой — это нормально)\n"
        "3. Скопируй <b>весь адрес</b> из адресной строки и пришли его командой:\n"
        "<code>/set_vk_token вставь_сюда</code>\n\n"
        f"{url}\n\n"
        f"{flow_note}\n\n"
        f"Сейчас: {describe_token_state()}",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def _store_from_code(
    update: Update, config: Config, parsed: AuthCode
) -> Optional[VKTokens]:
    """Exchange a code-flow redirect for tokens and persist them."""
    global _pending_auth

    pending = _pending_auth
    if not pending:
        await update.message.reply_text(
            "❌ Ссылка устарела (бот перезапускался после её выдачи).\n\n"
            "Пришли /set_vk_token без аргументов и открой свежую."
        )
        return None

    if parsed.state and parsed.state != pending["state"]:
        await update.message.reply_text(
            "❌ Это редирект от другой ссылки.\n\n"
            "Пришли /set_vk_token без аргументов и используй самую свежую."
        )
        return None

    try:
        response = await asyncio.to_thread(
            exchange_code_for_tokens,
            config.VK_APP_ID,
            parsed.code,
            pending["verifier"],
            parsed.device_id or "",
            parsed.state or pending["state"],
            pending["redirect_uri"],
        )
    except VKAuthError as e:
        await update.message.reply_text(
            f"❌ Обмен кода на токен не прошёл: {e}\n\n"
            "Пришли /set_vk_token без аргументов и получи свежую ссылку "
            "(код одноразовый и живёт около минуты)."
        )
        return None

    try:
        await asyncio.to_thread(_verify_token, response["access_token"])
    except Exception as e:
        code = getattr(e, "code", None)
        await update.message.reply_text(
            f"❌ VK отклонил свежевыпущенный токен (code={code}): {e}"
        )
        return None

    _pending_auth = None
    return save_from_response(response, fallback_device_id=parsed.device_id)


async def _store_from_implicit(
    update: Update, parsed: ImplicitToken
) -> Optional[VKTokens]:
    """Validate a pasted implicit-flow token and persist it."""
    try:
        owner = await asyncio.to_thread(_verify_token, parsed.access_token)
    except Exception as e:
        code = getattr(e, "code", None)
        hint = ""
        if code == 5 and "ip address" in str(e).lower():
            hint = (
                "\n\nТокен привязан к IP, с которого ты авторизовался. "
                "Открой ссылку через прокси сервера (например, ssh -D) и попробуй снова."
            )
        await update.message.reply_text(
            f"❌ VK отклонил токен (code={code}): {e}{hint}\n\n"
            "Пришли /set_vk_token без аргументов и получи свежую ссылку."
        )
        return None

    expires_at = 0.0
    if parsed.expires_in:
        expires_at = time.time() + parsed.expires_in

    tokens = VKTokens(
        access_token=parsed.access_token,
        refresh_token=None,      # implicit flow issues none
        device_id=None,
        expires_at=expires_at,
        user_id=parsed.user_id or owner.get("id"),
    )
    save_tokens(tokens)
    return tokens


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
