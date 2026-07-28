#!/usr/bin/env bash
# Alpha Trade Oracle Bot — Erst-Deployment auf einem Ubuntu-VPS.
# Voraussetzung: .env liegt im Repo-Root (nicht committen).
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/AlphaTradeOracle/alpha-trade-oracle-bot.git}"
APP_DIR="${APP_DIR:-/opt/alpha-trade-oracle-bot}"

echo "==> Docker installieren (falls noetig)"
if ! command -v docker >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
  systemctl enable --now docker
fi

echo "==> Repo klonen oder aktualisieren"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

if [ ! -f .env ]; then
  echo "FEHLER: $APP_DIR/.env fehlt."
  echo "Kopiere .env.example nach .env und trage Telegram + sichere Passwoerter ein."
  exit 1
fi

echo "==> Production-Defaults pruefen"
grep -q '^APP_ENV=' .env || echo 'APP_ENV=production' >> .env
grep -q '^ENABLE_SCHEDULER=' .env || echo 'ENABLE_SCHEDULER=true' >> .env
grep -q '^ENABLE_UNIVERSE_SCAN=' .env || echo 'ENABLE_UNIVERSE_SCAN=true' >> .env

echo "==> Stack starten (Postgres, Redis, Migration, Worker, API)"
docker compose pull postgres redis 2>/dev/null || true
docker compose build
docker compose up -d postgres redis
echo "Warte auf Postgres..."
sleep 15
docker compose run --rm migrate
docker compose up -d worker app

echo "==> Universe laden (Top-N Market Cap)"
docker compose run --rm worker python -m app.cli universe refresh

echo "==> Status"
docker compose ps
docker compose logs worker --tail 30

echo ""
echo "Fertig. Worker laeuft 24/7 mit Scheduler (Scan + Universe-Refresh)."
echo "Logs: docker compose -f $APP_DIR/docker-compose.yml logs -f worker"
