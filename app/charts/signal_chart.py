"""Kerzenchart mit Entry-, Stop- und Take-Profit-Levels fuer Telegram."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.services.analysis_service import AnalysisOutcome

logger = get_logger(__name__)

CHART_CANDLES = 72
FIGURE_SIZE = (10, 5.5)
DPI = 120


def build_signal_chart(outcome: AnalysisOutcome) -> bytes | None:
    """PNG-Bytes fuer Telegram erzeugen, falls Trade-Levels vorhanden sind."""
    result = outcome.result
    risk = result.risk
    series = outcome.chart_series

    if risk is None or not result.direction.is_actionable:
        return None
    if series is None or series.is_empty:
        return None

    candles = series.candles[-CHART_CANDLES:]
    if len(candles) < 5:
        return None

    try:
        return _render_png(
            symbol=result.symbol,
            timeframe=series.timeframe,
            direction=result.direction.value,
            score=result.score,
            candles=candles,
            entry_low=float(risk.entry_low),
            entry_high=float(risk.entry_high),
            stop_loss=float(risk.stop_loss),
            tp1=float(risk.take_profit_1),
            tp2=float(risk.take_profit_2),
            tp3=float(risk.take_profit_3),
        )
    except Exception as exc:
        logger.warning("signal_chart_render_failed", symbol=result.symbol, error=str(exc))
        return None


def _render_png(
    *,
    symbol: str,
    timeframe: str,
    direction: str,
    score: float,
    candles: list,
    entry_low: float,
    entry_high: float,
    stop_loss: float,
    tp1: float,
    tp2: float,
    tp3: float,
) -> bytes:
    closes = [c.close for c in candles]
    xs = list(range(len(candles)))

    fig, ax = plt.subplots(figsize=FIGURE_SIZE, facecolor="#0f1419")
    ax.set_facecolor("#0f1419")

    width = 0.6
    for i, candle in enumerate(candles):
        color = "#26a69a" if candle.close >= candle.open else "#ef5350"
        body_low = min(candle.open, candle.close)
        body_high = max(candle.open, candle.close)
        ax.plot([i, i], [candle.low, candle.high], color=color, linewidth=0.8, alpha=0.9)
        height = max(body_high - body_low, (candle.high - candle.low) * 0.05)
        ax.add_patch(
            mpatches.Rectangle(
                (i - width / 2, body_low),
                width,
                height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.5,
            )
        )

    ax.axhspan(entry_low, entry_high, color="#ffd54f", alpha=0.18, zorder=0)
    ax.axhline(entry_low, color="#ffd54f", linewidth=1.2, linestyle="-", alpha=0.85)
    ax.axhline(entry_high, color="#ffd54f", linewidth=1.2, linestyle="-", alpha=0.85)
    ax.axhline(stop_loss, color="#ef5350", linewidth=1.4, linestyle="--", alpha=0.95)
    ax.axhline(tp1, color="#66bb6a", linewidth=1.2, linestyle=":", alpha=0.95)
    ax.axhline(tp2, color="#43a047", linewidth=1.2, linestyle=":", alpha=0.95)
    ax.axhline(tp3, color="#2e7d32", linewidth=1.2, linestyle=":", alpha=0.95)

    y_min = min(c.low for c in candles + [_Level(stop_loss)])
    y_max = max(c.high for c in candles + [_Level(tp3)])
    pad = (y_max - y_min) * 0.08 or y_max * 0.02
    ax.set_ylim(y_min - pad, y_max + pad)
    ax.set_xlim(-1, len(candles))

    label_x = len(candles) - 0.15
    _label_level(ax, label_x, entry_low, "Entry low", "#ffd54f")
    _label_level(ax, label_x, entry_high, "Entry high", "#ffd54f")
    _label_level(ax, label_x, stop_loss, "SL", "#ef5350")
    _label_level(ax, label_x, tp1, "TP1", "#66bb6a")
    _label_level(ax, label_x, tp2, "TP2", "#43a047")
    _label_level(ax, label_x, tp3, "TP3", "#2e7d32")

    pretty = symbol.replace("USDT", "/USDT") if symbol.endswith("USDT") else symbol
    ax.set_title(
        f"{pretty}  {timeframe}  {direction}  {score:.0f}/100",
        color="#e8eaed",
        fontsize=12,
        pad=12,
        fontweight="bold",
    )
    ax.tick_params(colors="#9aa0a6", labelsize=8)
    ax.grid(True, color="#2a2f36", linewidth=0.5, alpha=0.6)
    for spine in ax.spines.values():
        spine.set_color("#2a2f36")

    step = max(1, len(candles) // 6)
    tick_positions = list(range(0, len(candles), step))
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(
        [candles[i].open_time.strftime("%d.%m %H:%M") for i in tick_positions],
        rotation=25,
        ha="right",
        fontsize=7,
        color="#9aa0a6",
    )

    legend_handles = [
        Line2D([0], [0], color="#ffd54f", linewidth=2, label="Entry"),
        Line2D([0], [0], color="#ef5350", linewidth=2, linestyle="--", label="Stop-Loss"),
        Line2D([0], [0], color="#66bb6a", linewidth=2, linestyle=":", label="TP1-3"),
    ]
    ax.legend(
        handles=legend_handles,
        loc="upper left",
        facecolor="#1a1f26",
        edgecolor="#2a2f36",
        labelcolor="#e8eaed",
        fontsize=8,
    )

    fig.tight_layout()
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=DPI, facecolor=fig.get_facecolor())
    plt.close(fig)
    buffer.seek(0)
    return buffer.read()


class _Level:
    def __init__(self, value: float) -> None:
        self.low = value
        self.high = value


def _label_level(ax, x: float, y: float, text: str, color: str) -> None:
    ax.text(
        x,
        y,
        f" {text}",
        color=color,
        fontsize=7,
        va="center",
        ha="left",
        fontweight="bold",
    )
