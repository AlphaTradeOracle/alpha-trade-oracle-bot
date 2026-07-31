"""Kerzenchart im TradingView-Stil mit Entry/SL/TP-Strukturen fuer Telegram."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from app.charts import theme as tv
from app.core.logging import get_logger
from app.core.time import next_higher_timeframe
from app.market_data.types import CandleSeries

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.models.paper import PaperPosition
    from app.services.analysis_service import AnalysisOutcome

logger = get_logger(__name__)

CHART_CANDLES = 80
FIGURE_SIZE = (11.2, 7.4)
#: Hoehenverhaeltnis Preis-Panel : Volumen-Panel.
_HEIGHT_RATIOS = (3.45, 1.0)


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
    fig, (ax, ax_vol) = plt.subplots(
        2,
        1,
        figsize=FIGURE_SIZE,
        facecolor=tv.BG,
        sharex=True,
        gridspec_kw={"height_ratios": list(_HEIGHT_RATIOS), "hspace": 0.028},
    )
    tv.style_figure(fig)
    tv.style_axes(ax)
    tv.style_axes(ax_vol)
    tv.watermark(ax, alpha=0.70, zoom=0.155, loc="top_left", xycoords="figure fraction")

    n = len(candles)
    last_i = n - 1
    entry_mid = (entry_low + entry_high) / 2.0
    is_long = "SHORT" not in direction.upper()

    # Risk / Reward Zonen (TV-aehnlich, weich)
    zone_left = -0.6
    zone_right = n + 6.5
    risk_top = max(entry_mid, stop_loss)
    risk_bot = min(entry_mid, stop_loss)
    reward_top = max(entry_mid, tp3)
    reward_bot = min(entry_mid, tp3)
    ax.axhspan(risk_bot, risk_top, xmin=0, xmax=1, color=tv.SL, alpha=0.07, zorder=0.5)
    ax.axhspan(reward_bot, reward_top, color=tv.UP, alpha=0.055, zorder=0.5)

    # Gestaffelte TP-Baender
    tp_levels = (tp1, tp2, tp3)
    for idx, price in enumerate(tp_levels):
        lo = min(entry_mid, price)
        hi = max(entry_mid, price)
        ax.axhspan(lo, hi, color=tv.TP[idx], alpha=0.03 + idx * 0.01, zorder=0.6)

    # Entry-Zone
    ax.axhspan(entry_low, entry_high, color=tv.ENTRY, alpha=0.16, zorder=1)
    ax.hlines(
        entry_low,
        zone_left,
        zone_right,
        colors=tv.ENTRY,
        linewidths=1.0,
        linestyles=(0, (4, 3)),
        alpha=0.85,
        zorder=2,
    )
    ax.hlines(
        entry_high,
        zone_left,
        zone_right,
        colors=tv.ENTRY,
        linewidths=1.0,
        linestyles=(0, (4, 3)),
        alpha=0.85,
        zorder=2,
    )
    ax.hlines(
        entry_mid,
        zone_left,
        zone_right,
        colors=tv.ENTRY,
        linewidths=1.55,
        linestyles="solid",
        alpha=0.95,
        zorder=2.2,
    )

    # SL / TP Rays
    ax.hlines(
        stop_loss,
        zone_left,
        zone_right,
        colors=tv.SL,
        linewidths=1.65,
        linestyles=(0, (6, 3)),
        alpha=0.98,
        zorder=2.3,
    )
    for idx, price in enumerate(tp_levels):
        ax.hlines(
            price,
            zone_left,
            zone_right,
            colors=tv.TP[idx],
            linewidths=1.35,
            linestyles=(0, (2, 2.4)),
            alpha=0.95,
            zorder=2.2,
        )

    # Candles
    width = 0.68
    for i, candle in enumerate(candles):
        bullish = candle.close >= candle.open
        color = tv.UP if bullish else tv.DOWN
        body_low = min(candle.open, candle.close)
        body_high = max(candle.open, candle.close)
        ax.plot(
            [i, i],
            [candle.low, candle.high],
            color=color,
            linewidth=1.05,
            solid_capstyle="round",
            zorder=3,
        )
        height = max(body_high - body_low, (candle.high - candle.low) * 0.035)
        ax.add_patch(
            mpatches.Rectangle(
                (i - width / 2, body_low),
                width,
                height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.0,
                zorder=3.1,
            )
        )
        vol_color = tv.VOL_UP if bullish else tv.VOL_DOWN
        ax_vol.add_patch(
            mpatches.Rectangle(
                (i - width / 2, 0.0),
                width,
                max(float(candle.volume), 0.0),
                facecolor=vol_color,
                edgecolor="none",
                alpha=0.55,
                zorder=2,
            )
        )

    # Vertikale "Jetzt"-Linie
    ax.axvline(last_i + 0.5, color=tv.MUTED, linewidth=0.7, linestyle=(0, (2, 3)), alpha=0.55, zorder=1.5)

    levels = [entry_low, entry_high, stop_loss, tp1, tp2, tp3]
    y_min = min(min(c.low for c in candles), min(levels))
    y_max = max(max(c.high for c in candles), max(levels))
    pad = (y_max - y_min) * 0.10 or abs(y_max) * 0.02 or 1.0
    ax.set_ylim(y_min - pad, y_max + pad)

    x_right = n + 7.2
    ax.set_xlim(-1.0, x_right)
    ax_vol.set_xlim(-1.0, x_right)

    max_vol = max((float(c.volume) for c in candles), default=0.0)
    ax_vol.set_ylim(0.0, max_vol * 1.22 if max_vol > 0 else 1.0)

    # TV-Style Price Tags rechts
    tag_x = n + 0.55
    _tv_tag(
        ax,
        tag_x,
        entry_mid,
        f"ENTRY  {tv.fmt_price(entry_low, price_precision)}–{tv.fmt_price(entry_high, price_precision)}"
        if abs(entry_high - entry_low) / max(abs(entry_mid), 1e-9) >= 0.0015
        else f"ENTRY  {tv.fmt_price(entry_mid, price_precision)}",
        tv.ENTRY,
    )
    _tv_tag(ax, tag_x, stop_loss, f"SL  {tv.fmt_price(stop_loss, price_precision)}", tv.SL)
    _tv_tag(ax, tag_x, tp1, f"TP1  {tv.fmt_price(tp1, price_precision)}", tv.TP[0])
    _tv_tag(ax, tag_x, tp2, f"TP2  {tv.fmt_price(tp2, price_precision)}", tv.TP[1])
    _tv_tag(ax, tag_x, tp3, f"TP3  {tv.fmt_price(tp3, price_precision)}", tv.TP[2])

    # Last close tag
    last_close = float(candles[-1].close)
    last_color = tv.UP if candles[-1].close >= candles[-1].open else tv.DOWN
    _tv_tag(ax, tag_x, last_close, tv.fmt_price(last_close, price_precision), last_color, alpha=0.92)

    pretty = symbol.replace("USDT", "/USDT") if symbol.endswith("USDT") else symbol
    direction_label = direction.replace("_", " ").upper()
    side_color = tv.UP if is_long else tv.DOWN

    # Header-Leiste (Text rechts vom Logo oben links)
    ax.set_title("")
    fig.subplots_adjust(left=0.04, right=0.86, top=0.90, bottom=0.07, hspace=0.04)
    fig.text(
        0.225,
        0.955,
        f"{pretty}  ·  {timeframe}",
        color=tv.TEXT,
        fontsize=14.5,
        fontweight="bold",
        ha="left",
        va="center",
    )
    fig.text(
        0.86,
        0.955,
        f" {direction_label} ",
        color="#ffffff",
        fontsize=9.5,
        fontweight="bold",
        ha="right",
        va="center",
        bbox={
            "boxstyle": "round,pad=0.35,rounding_size=0.4",
            "facecolor": side_color,
            "edgecolor": "none",
            "alpha": 0.95,
        },
    )
    _ = score  # Confidence stays in the Telegram caption, not on the chart.

    ax.tick_params(axis="x", labelbottom=False)
    ax_vol.tick_params(axis="x", labelsize=7.5)

    step = max(1, n // 6)
    tick_positions = list(range(0, n, step))
    ax_vol.set_xticks(tick_positions)
    ax_vol.set_xticklabels(
        [candles[i].open_time.strftime("%d.%m %H:%M") for i in tick_positions],
        rotation=0,
        ha="center",
        fontsize=7.5,
        color=tv.MUTED,
    )
    ax.yaxis.set_major_formatter(
        plt.FuncFormatter(lambda value, _: tv.fmt_price(float(value), price_precision))
    )
    ax_vol.yaxis.set_major_formatter(plt.FuncFormatter(lambda value, _: tv.fmt_volume(float(value))))
    ax_vol.text(
        0.012,
        0.90,
        "Volume",
        transform=ax_vol.transAxes,
        color=tv.MUTED,
        fontsize=8,
        va="top",
        ha="left",
    )

    # RR-Hinweis unten links
    risk_dist = abs(entry_mid - stop_loss)
    reward_dist = abs(tp3 - entry_mid)
    rr = (reward_dist / risk_dist) if risk_dist > 0 else 0.0
    ax.text(
        0.012,
        0.035,
        f"R:R to TP3  {rr:.2f}  ·  Risk zone / Reward zone",
        transform=ax.transAxes,
        color=tv.MUTED,
        fontsize=8,
        ha="left",
        va="bottom",
        zorder=5,
    )

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=tv.DPI, facecolor=fig.get_facecolor())
    plt.close(fig)
    buffer.seek(0)
    return buffer.read()


def _tv_tag(
    ax,
    x: float,
    y: float,
    text: str,
    color: str,
    *,
    alpha: float = 1.0,
) -> None:
    """TradingView-aehnliches Preis-Label rechts an der Level-Linie."""
    ax.annotate(
        f" {text} ",
        xy=(x, y),
        xytext=(6, 0),
        textcoords="offset points",
        color="#ffffff",
        fontsize=7.8,
        fontweight="bold",
        va="center",
        ha="left",
        clip_on=False,
        zorder=6,
        bbox={
            "boxstyle": "round,pad=0.28,rounding_size=0.35",
            "facecolor": color,
            "edgecolor": "none",
            "alpha": alpha,
        },
    )
