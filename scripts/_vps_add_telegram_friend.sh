#!/usr/bin/env bash
set -euo pipefail
cd /opt/alpha-trade-oracle-bot

FRIEND_ID=337602001
OWNER_ID=448462936

grep -E '^TELEGRAM_ALLOWED_CHAT_IDS=|^TELEGRAM_ADMIN_CHAT_IDS=' .env

sed -i "s/^TELEGRAM_ALLOWED_CHAT_IDS=.*/TELEGRAM_ALLOWED_CHAT_IDS=${OWNER_ID},${FRIEND_ID}/" .env

grep -E '^TELEGRAM_ALLOWED_CHAT_IDS=|^TELEGRAM_ADMIN_CHAT_IDS=' .env

docker compose up -d worker
sleep 3
docker compose exec -T worker python - <<'PY'
from app.core.config import get_settings
s = get_settings()
print("allowed", sorted(s.allowed_chat_ids))
print("admin", sorted(s.admin_chat_ids))
assert 337602001 in s.allowed_chat_ids
assert 448462936 in s.allowed_chat_ids
print("OK")
PY

echo "Friend must open the bot and send /start once so the chat is registered for signal dispatch."
