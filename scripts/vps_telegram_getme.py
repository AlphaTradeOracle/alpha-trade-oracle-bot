"""Check Telegram bot token without printing secrets."""

from __future__ import annotations

import httpx

from app.core.config import get_settings


def main() -> None:
    s = get_settings()
    token = s.telegram_bot_token
    if hasattr(token, "get_secret_value"):
        token = token.get_secret_value()
    token = str(token or "")
    print("token_set", bool(token))
    print("token_len", len(token))
    print("token_looks_like_bot", token.count(":") == 1 and token.split(":")[0].isdigit())
    r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=20.0)
    print("http", r.status_code)
    data = r.json()
    print("ok", data.get("ok"))
    if data.get("ok"):
        print("username", data["result"].get("username"))
        print("id", data["result"].get("id"))
    else:
        # safe error description only
        print("error_code", data.get("error_code"))
        print("description", data.get("description"))


if __name__ == "__main__":
    main()
