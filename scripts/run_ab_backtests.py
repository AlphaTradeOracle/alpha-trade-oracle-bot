"""A/B-Backtests: Scale-out an/aus + Long/Short-Split.

Ausgabe: JSON-Zusammenfassung auf stdout (fuer Canvas Vorher/Nachher).
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import timedelta

from app.container import build_container
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.time import utc_now

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
]

METRIC_KEYS = (
    "trade_count",
    "win_rate",
    "net_profit",
    "profit_factor",
    "expectancy",
    "max_drawdown",
    "max_drawdown_percent",
    "average_win",
    "average_loss",
    "average_holding_minutes",
    "total_fees",
)


def _pick(metrics: dict[str, float] | None) -> dict[str, float]:
    if not metrics:
        return {key: 0.0 for key in METRIC_KEYS}
    return {key: float(metrics.get(key, 0.0) or 0.0) for key in METRIC_KEYS}


async def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)
    container = build_container(settings)

    end = utc_now()
    start = end - timedelta(days=365)
    timeframe = "1h"
    capital = 10_000.0

    results: list[dict[str, object]] = []

    try:
        for symbol in SYMBOLS:
            for scale_out in (False, True):
                label = "scale_out" if scale_out else "full_exit"
                print(f"Running {symbol} {timeframe} [{label}] ...", file=sys.stderr, flush=True)
                try:
                    report = await container.backtest_service.run(
                        symbol,
                        timeframe,
                        start,
                        end,
                        fee_percent=0.1,
                        slippage_percent=0.05,
                        initial_capital=capital,
                        persist=False,
                        scale_out_enabled=scale_out,
                        move_stop_to_breakeven_after_tp1=scale_out,
                        # Single-TF fuer Runtime; Exit-A/B bleibt fair (gleiche Signale).
                        use_multi_timeframe=False,
                    )
                    overall = _pick(report.metrics.get("overall"))
                    long_m = _pick(report.metrics.get("long"))
                    short_m = _pick(report.metrics.get("short"))
                    results.append(
                        {
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "mode": label,
                            "scale_out_enabled": scale_out,
                            "start": start.date().isoformat(),
                            "end": end.date().isoformat(),
                            "candles_loaded": report.candles_loaded,
                            "signals_generated": report.outcome.signals_generated,
                            "overall": overall,
                            "long": long_m,
                            "short": short_m,
                        }
                    )
                    print(
                        f"  trades={int(overall['trade_count'])} "
                        f"net={overall['net_profit']:.2f} "
                        f"wr={overall['win_rate']*100:.1f}% "
                        f"pf={overall['profit_factor']:.2f}",
                        file=sys.stderr,
                        flush=True,
                    )
                except Exception as exc:
                    print(f"  FAILED: {exc}", file=sys.stderr, flush=True)
                    results.append(
                        {
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "mode": label,
                            "scale_out_enabled": scale_out,
                            "error": str(exc),
                        }
                    )
    finally:
        await container.aclose()

    payload = {
        "generated_at": utc_now().isoformat(),
        "capital": capital,
        "fee_percent": 0.1,
        "slippage_percent": 0.05,
        "days": 365,
        "timeframe": timeframe,
        "gates": {
            "min_score": settings.signal_min_score,
            "short_max_score": settings.signal_short_max_score,
            "require_strong": settings.signal_require_strong,
            "min_rr": settings.min_risk_reward_ratio,
        },
        "results": results,
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
