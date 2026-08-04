#!/usr/bin/env bash
# Deprecated wrapper — use _vps_strategy_fresh_start.sh (current 16/16/8 + short_max=30).
set -euo pipefail
exec bash /opt/alpha-trade-oracle-bot/scripts/_vps_strategy_fresh_start.sh
