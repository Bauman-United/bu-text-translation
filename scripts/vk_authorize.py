#!/usr/bin/env python3
"""
One-time VK ID authorization.

Obtains a refreshable token set via the VK ID code+PKCE flow.

REQUIRES an application that can register a redirect URI. VK "mini app" type
apps cannot — every redirect_uri comes back as "redirect_uri is missing or
invalid" — so with such an app use the bot's /set_vk_token command instead,
which uses the classic implicit flow (24h tokens, replaced by hand).

Run this once by hand. It prints a link, you approve it in the browser, paste
the redirect URL back, and the bot gets a refresh token it can renew on its own
from then on.

    python scripts/vk_authorize.py

Re-run it only if the refresh chain breaks (data/vk_token.json lost, or VK
invalidated the refresh token).
"""

import os
import sys
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from api.vk_auth import (  # noqa: E402
    DEFAULT_REDIRECT_URI,
    DEFAULT_SCOPE,
    VKAuthError,
    build_authorize_url,
    exchange_code_for_tokens,
    generate_pkce_pair,
    generate_state,
)
from utils.vk_token_store import save_from_response  # noqa: E402


def _extract_params(redirect_url: str) -> dict:
    """Pull code/state/device_id out of the pasted redirect URL."""
    parsed = urlparse(redirect_url.strip())
    params = parse_qs(parsed.query)
    # VK sometimes puts them in the fragment instead of the query string.
    if parsed.fragment:
        for key, value in parse_qs(parsed.fragment).items():
            params.setdefault(key, value)
    return {k: v[0] for k, v in params.items() if v}


def main() -> int:
    app_id = os.getenv("VK_APP_ID")
    if not app_id:
        print("❌ VK_APP_ID не задан в .env")
        print("   Это ID приложения из vk.com/apps?act=manage → Настройки")
        return 1

    verifier, challenge = generate_pkce_pair()
    state = generate_state()
    url = build_authorize_url(app_id, challenge, state)

    print("=" * 78)
    print("ШАГ 1. Открой эту ссылку в браузере и нажми «Разрешить»:\n")
    print(url)
    print()
    print("ШАГ 2. Тебя перекинет на пустую страницу blank.html.")
    print("       Скопируй ВЕСЬ адрес из адресной строки и вставь сюда.")
    print("=" * 78)
    print()

    try:
        redirect_url = input("Адрес после редиректа: ").strip()
    except EOFError:
        print("\n❌ Ввод недоступен — запусти скрипт в интерактивном терминале")
        return 1
    if not redirect_url:
        print("❌ Ничего не введено")
        return 1

    params = _extract_params(redirect_url)
    code = params.get("code")
    device_id = params.get("device_id")
    returned_state = params.get("state")

    if not code:
        print(f"❌ В адресе нет параметра `code`. Разобрано: {sorted(params)}")
        return 1
    if not device_id:
        print(f"❌ В адресе нет параметра `device_id`. Разобрано: {sorted(params)}")
        return 1
    if returned_state and returned_state != state:
        print("❌ `state` не совпадает — возможна подмена, начни заново")
        return 1

    print("\n⏳ Меняю code на токены...")
    try:
        response = exchange_code_for_tokens(
            client_id=app_id,
            code=code,
            code_verifier=verifier,
            device_id=device_id,
            state=state,
            redirect_uri=DEFAULT_REDIRECT_URI,
        )
    except VKAuthError as e:
        print(f"❌ {e}")
        return 1

    tokens = save_from_response(response, fallback_device_id=device_id)

    print("\n✅ Готово.")
    print(f"   scope:         {DEFAULT_SCOPE}")
    print(f"   user_id:       {tokens.user_id}")
    print(f"   access_token:  живёт {int(tokens.seconds_left or 0)} сек")
    print(f"   refresh_token: {'есть' if tokens.refresh_token else 'НЕТ — обновление невозможно'}")
    print("\nБот теперь обновляет токен сам. Файл data/vk_token.json не удаляй —")
    print("в нём refresh_token, без него придётся авторизоваться заново.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
