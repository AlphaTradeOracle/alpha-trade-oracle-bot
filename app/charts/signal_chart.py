"""Kerzenchart mit Entry-, Stop- und Take-Profit-Levels fuer Telegram."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from app.core.logging import get_logger
from app.core.time import next_higher_timeframe
from app.market_data.types import CandleSeries

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.models.paper import PaperPosition
    from app.services.analysis_service import AnalysisOutcome

logger = get_logger(__name__)

CHART_CANDLES = 80
FIGURE_SIZE = (10.5, 6.0)
DPI = 140

_BG = "#0b1016"
_PANEL = "#10161e"
_GRID = "#1c2530"
_TEXT = "#e8eef5"
_MUTED = "#8b98a5"
_UP = "#2ecc8a"
_DOWN = "#ef5b67"
_ENTRY = "#f0c75e"
_SL = "#ff5c6a"
_TP = ("#6bcf8e", "#4caf75", "#2f9e5f")


def resolve_paper_chart_timeframe(primary_timeframe: str, settings: Settings) -> str:
    """Chart-Timeframe fuer Paper-Trades (Setup-TF oder naechst hoeherer)."""
    override = (settings.paper_telegram_chart_timeframe or "").strip()
    if override:
        return override
    return next_higher_timeframe(primary_timeframe, settings.timeframes)


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
        return build_trade_levels_chart(
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
            price_precision=outcome.price_precision,
        )
    except Exception as exc:
        logger.warning("signal_chart_render_failed", symbol=result.symbol, error=str(exc))
        return None


def build_paper_trade_chart(
    position: PaperPosition,
    series: CandleSeries,
    *,
    price_precision: int = 2,
) -> bytes | None:
    """Chart fuer einen eroeffneten Paper-Trade (Entry/SL/TP-Linien)."""
    if series.is_empty:
        return None

    candles = series.candles[-CHART_CANDLES:]
    if len(candles) < 5:
        return None

    entry = float(position.entry_price)
    spread = max(abs(entry) * 0.0005, 10 ** (-price_precision))

    try:
        return build_trade_levels_chart(
            symbol=position.symbol,
            timeframe=series.timeframe,
            direction=position.direction,
            score=float(position.signal_score or 0),
            candles=candles,
            entry_low=entry - spread,
            entry_high=entry + spread,
            stop_loss=float(position.stop_loss),
            tp1=float(position.take_profit_1),
            tp2=float(position.take_profit_2),
            tp3=float(position.take_profit_3),
            price_precision=price_precision,
        )
    except Exception as exc:
        logger.warning("paper_trade_chart_render_failed", symbol=position.symbol, error=str(exc))
        return None


def build_trade_levels_chart(
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
    price_precision: int,
) -> bytes:
    return _render_png(
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        score=score,
        candles=candles,
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=stop_loss,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        price_precision=price_precision,
    )


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
    price_precision: int,
) -> bytes:
    fig, ax = plt.subplots(figsize=FIGURE_SIZE, facecolor=_BG)
    ax.set_facecolor(_PANEL)

    width = 0.62
    for i, candle in enumerate(candles):
        bullish = candle.close >= candle.open
        color = _UP if bullish else _DOWN
        body_low = min(candle.open, candle.close)
        body_high = max(candle.open, candle.close)
        ax.plot([i, i], [candle.low, candle.high], color=color, linewidth=1.0, solid_capstyle="round")
        height = max(body_high - body_low, (candle.high - candle.low) * 0.04)
        ax.add_patch(
            mpatches.Rectangle(
                (i - width / 2, body_low),
                width,
                height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.4,
            )
        )

    ax.axhspan(entry_low, entry_high, color=_ENTRY, alpha=0.14, zorder=0)
    ax.axhline(entry_low, color=_ENTRY, linewidth=1.15, alpha=0.9)
    ax.axhline(entry_high, color=_ENTRY, linewidth=1.15, alpha=0.9)
    ax.axhline(stop_loss, color=_SL, linewidth=1.55, linestyle=(0, (5, 3)), alpha=0.95)
    for price, color in ((tp1, _TP[0]), (tp2, _TP[1]), (tp3, _TP[2])):
        ax.axhline(price, color=color, linewidth=1.25, linestyle=(0, (1.5, 2.5)), alpha=0.95)

    levels = [entry_low, entry_high, stop_loss, tp1, tp2, tp3]
    y_min = min(min(c.low for c in candles), min(levels))
    y_max = max(max(c.high for c in candles), max(levels))
    pad = (y_max - y_min) * 0.09 or abs(y_max) * 0.02 or 1.0
    ax.set_ylim(y_min - pad, y_max + pad)
    ax.set_xlim(-1.2, len(candles) + 8)

    label_x = len(candles) + 0.4
    _level_label(ax, label_x, (entry_low + entry_high) / 2, "Entry", entry_low, entry_high, _ENTRY, price_precision)
    _price_label(ax, label_x, stop_loss, "SL", _SL, price_precision)
    _price_label(ax, label_x, tp1, "TP1", _TP[0], price_precision)
    _price_label(ax, label_x, tp2, "TP2", _TP[1], price_precision)
    _price_label(ax, label_x, tp3, "TP3", _TP[2], price_precision)

    pretty = symbol.replace("USDT", "/USDT") if symbol.endswith("USDT") else symbol
    direction_label = direction.replace("_", " ")
    ax.set_title(
        f"{pretty}   {timeframe}   {direction_label}   {score:.0f}/100",
        color=_TEXT,
        fontsize=13,
        pad=14,
        fontweight="bold",
        loc="left",
    )
    ax.tick_params(colors=_MUTED, labelsize=8, length=0)
    ax.grid(True, color=_GRID, linewidth=0.6, alpha=0.85)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color(_GRID)
        spine.set_linewidth(0.8)

    step = max(1, len(candles) // 6)
    tick_positions = list(range(0, len(candles), step))
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(
        [candles[i].open_time.strftime("%d.%m %H:%M") for i in tick_positions],
        rotation=0,
        ha="center",
        fontsize=7.5,
        color=_MUTED,
    )
    ax.yaxis.set_major_formatter(
        plt.FuncFormatter(lambda value, _: _fmt_price(float(value), price_precision))
    )

    fig.tight_layout(pad=1.1)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=DPI, facecolor=fig.get_facecolor())
    plt.close(fig)
    buffer.seek(0)
    return buffer.read()


def _fmt_price(value: float, precision: int) -> str:
    formatted = f"{value:,.{precision}f}"
    return formatted.replace(",", "\u00a0").replace(".", ",").replace("\u00a0", ".")


def _price_label(ax, x: float, y: float, name: str, color: str, precision: int) -> None:
    ax.text(
        x,
        y,
        f" {name}  {_fmt_price(y, precision)}",
        color=color,
        fontsize=8,
        va="center",
        ha="left",
        fontweight="bold",
        clip_on=False,
    )


def _level_label(
    ax,
    x: float,
    y: float,
    name: str,
    low: float,
    high: float,
    color: str,
    precision: int,
) -> None:
    if abs(high - low) / max(abs(high), 1e-9) < 0.0015:
        _price_label(ax, x, y, name, color, precision)
        return
    ax.text(
        x,
        y,
        f" {name}  {_fmt_price(low, precision)}–{_fmt_price(high, precision)}",
        color=color,
        fontsize=8,
        va="center",
        ha="left",
        fontweight="bold",
        clip_on=False,
    )
