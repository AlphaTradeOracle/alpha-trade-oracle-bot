#!/bin/bash
set -eu
cd /opt/alpha-trade-oracle-bot
tr -d '\r' < /tmp/ask_gpt55_strategy_review.py > /tmp/ask_gpt55_lf.py
docker cp /tmp/ask_gpt55_lf.py alpha-trade-oracle-worker:/tmp/ask_gpt55_strategy_review.py
docker compose exec -T worker bash -lc 'export REVIEW_MODEL=openai/gpt-5.5 REVIEW_OUT=/tmp/gpt55_strategy_review.md; python /tmp/ask_gpt55_strategy_review.py'
docker cp alpha-trade-oracle-worker:/tmp/gpt55_strategy_review.md /tmp/gpt55_strategy_review.md
echo "SAVED /tmp/gpt55_strategy_review.md"
