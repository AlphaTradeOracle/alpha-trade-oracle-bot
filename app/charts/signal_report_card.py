"""Professionelle Signal-Report-Card (Bild) fuer Telegram."""

from __future__ import annotations

import io
import textwrap
from typing import TYPE_CHECKING

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch, Rectangle
from PIL import Image

from app.charts import theme as tv
from app.core.enums import SignalDirection
from app.core.logging import get_logger
from app.core.time import format_display_time

if TYPE_CHECKING:
    from app.services.analysis_service import AnalysisOutcome

logger = get_logger(__name__)

FIGURE_SIZE = (10.8, 8.6)
_CARD_W = 0.94
_CARD_X = 0.03


def build_signal_report_card(
    outcome: AnalysisOutcome,
    *,
    display_timezone: str = "Europe/Berlin",
) -> bytes | None:
    """PNG-Bytes einer Signal-Briefing-Card; None wenn nicht actionable."""
    result = outcome.result
    if not result.direction.is_actionable or result.risk is None:
        return None
    try:
        return _render(outcome, display_timezone=display_timezone)
    except Exception as exc:
        logger.warning("signal_report_card_failed", symbol=result.symbol, error=str(exc))
        return None


def compose_signal_report(
    report_png: bytes | None,
    chart_png: bytes | None,
) -> bytes | None:
    """Report-Card oben, Kerzenchart darunter — ein Telegram-Bild."""
    if report_png is None and chart_png is None:
        return None
    if report_png is None:
        return chart_png
    if chart_png is None:
        return report_png
    try:
        top = Image.open(io.BytesIO(report_png)).convert("RGB")
        bottom = Image.open(io.BytesIO(chart_png)).convert("RGB")
        width = max(top.width, bottom.width)
        if top.width != width:
            top = top.resize((width, int(top.height * width / top.width)), Image.Resampling.LANCZOS)
        if bottom.width != width:
            bottom = bottom.resize(
                (width, int(bottom.height * width / bottom.width)), Image.Resampling.LANCZOS
            )
        canvas = Image.new("RGB", (width, top.height + bottom.height), tv.BG)
        canvas.paste(top, (0, 0))
        canvas.paste(bottom, (0, top.height))
        buf = io.BytesIO()
        canvas.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception as exc:
        logger.warning("signal_report_compose_failed", error=str(exc))
        return report_png or chart_png


def _pretty_symbol(symbol: str) -> str:
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}/USDT"
    return symbol


def _fmt(value: float, precision: int) -> str:
    return tv.fmt_price(value, precision)


def _render(outcome: AnalysisOutcome, *, display_timezone: str) -> bytes:
    result = outcome.result
    risk = result.risk
    assert risk is not None
    precision = outcome.price_precision
    is_long = result.direction.is_long
    side_color = tv.UP if is_long else tv.DOWN
    direction_label = {
        SignalDirection.STRONG_LONG: "STRONG LONG",
        SignalDirection.LONG: "LONG",
        SignalDirection.STRONG_SHORT: "STRONG SHORT",
        SignalDirection.SHORT: "SHORT",
    }.get(result.direction, result.direction.value.replace("_", " "))

    symbol = _pretty_symbol(result.symbol)
    entry_mid = risk.entry_mid
    risk_dist = abs(entry_mid - risk.stop_loss)
    reward_dist = abs(risk.take_profit_3 - entry_mid)
    rr = (reward_dist / risk_dist) if risk_dist > 0 else 0.0
    stamp = format_display_time(result.created_at, display_timezone)
    expires = format_display_time(result.expires_at, display_timezone)

    fig = plt.figure(figsize=FIGURE_SIZE, dpi=tv.DPI, facecolor=tv.BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.set_facecolor(tv.BG)

    # Soft logo watermark
    tv.watermark(ax, alpha=0.10, zoom=0.42)

    # Outer card
    ax.add_patch(
        FancyBboxPatch(
            (_CARD_X, 0.03),
            _CARD_W,
            0.94,
            boxstyle="round,pad=0.012,rounding_size=0.02",
            facecolor="#161b26",
            edgecolor=tv.CROSS,
            linewidth=1.2,
            transform=ax.transAxes,
            zorder=1,
        )
    )

    # Top accent bar
    ax.add_patch(
        Rectangle(
            (_CARD_X, 0.93),
            _CARD_W,
            0.04,
            facecolor=side_color,
            edgecolor="none",
            transform=ax.transAxes,
            zorder=2,
            alpha=0.95,
        )
    )
    ax.text(
        0.5,
        0.95,
        "SIGNAL REPORT  ·  ALPHA TRADE ORACLE",
        transform=ax.transAxes,
        color="#ffffff",
        fontsize=10,
        fontweight="bold",
        ha="center",
        va="center",
        zorder=3,
    )

    # Symbol + direction
    ax.text(
        0.07,
        0.875,
        symbol,
        transform=ax.transAxes,
        color=tv.TEXT,
        fontsize=26,
        fontweight="bold",
        ha="left",
        va="center",
        zorder=3,
    )
    ax.text(
        0.72,
        0.875,
        f" {direction_label} ",
        transform=ax.transAxes,
        color="#ffffff",
        fontsize=12,
        fontweight="bold",
        ha="center",
        va="center",
        zorder=3,
        bbox={
            "boxstyle": "round,pad=0.45,rounding_size=0.5",
            "facecolor": side_color,
            "edgecolor": "none",
        },
    )

    # Score ring-ish label
    ax.text(
        0.07,
        0.815,
        f"Score  {result.score:.0f}/100",
        transform=ax.transAxes,
        color=tv.WARN,
        fontsize=14,
        fontweight="bold",
        ha="left",
        va="center",
        zorder=3,
    )
    ax.text(
        0.32,
        0.815,
        f"Konfidenz  {result.confidence.value}",
        transform=ax.transAxes,
        color=tv.MUTED,
        fontsize=11,
        ha="left",
        va="center",
        zorder=3,
    )
    ax.text(
        0.58,
        0.815,
        f"{result.primary_timeframe}  ·  {result.market_phase.value}",
        transform=ax.transAxes,
        color=tv.MUTED,
        fontsize=11,
        ha="left",
        va="center",
        zorder=3,
    )

    # Divider
    ax.plot([0.07, 0.93], [0.78, 0.78], color=tv.CROSS, linewidth=1.0, transform=ax.transAxes, zorder=3)

    # Levels panel header
    ax.text(
        0.07,
        0.745,
        "TRADE LEVELS",
        transform=ax.transAxes,
        color=tv.MUTED,
        fontsize=9,
        fontweight="bold",
        ha="left",
        va="center",
        zorder=3,
    )
    ax.text(
        0.93,
        0.745,
        f"R:R  {rr:.2f}  ·  Ref {_fmt(result.reference_price, precision)}",
        transform=ax.transAxes,
        color=tv.MUTED,
        fontsize=9,
        ha="right",
        va="center",
        zorder=3,
    )

    levels = [
        ("ENTRY", f"{_fmt(risk.entry_low, precision)} – {_fmt(risk.entry_high, precision)}", tv.ENTRY),
        ("STOP LOSS", _fmt(risk.stop_loss, precision), tv.SL),
        ("TAKE PROFIT 1", _fmt(risk.take_profit_1, precision), tv.TP[0]),
        ("TAKE PROFIT 2", _fmt(risk.take_profit_2, precision), tv.TP[1]),
        ("TAKE PROFIT 3", _fmt(risk.take_profit_3, precision), tv.TP[2]),
    ]
    y0 = 0.69
    for idx, (label, value, color) in enumerate(levels):
        y = y0 - idx * 0.055
        ax.add_patch(
            FancyBboxPatch(
                (0.07, y - 0.018),
                0.86,
                0.042,
                boxstyle="round,pad=0.004,rounding_size=0.008",
                facecolor="#1a2030",
                edgecolor=tv.CROSS,
                linewidth=0.7,
                transform=ax.transAxes,
                zorder=2,
            )
        )
        ax.add_patch(
            Rectangle(
                (0.07, y - 0.018),
                0.008,
                0.042,
                facecolor=color,
                edgecolor="none",
                transform=ax.transAxes,
                zorder=3,
            )
        )
        ax.text(0.10, y, label, transform=ax.transAxes, color=tv.MUTED, fontsize=9, va="center", zorder=3)
        ax.text(
            0.90,
            y,
            value,
            transform=ax.transAxes,
            color=tv.TEXT,
            fontsize=12,
            fontweight="bold",
            ha="right",
            va="center",
            zorder=3,
        )

    # Confirmations
    ax.text(
        0.07,
        0.40,
        "BESTAETIGUNGEN",
        transform=ax.transAxes,
        color=tv.MUTED,
        fontsize=9,
        fontweight="bold",
        ha="left",
        va="center",
        zorder=3,
    )
    reasons = (result.reasons or [])[:4]
    if not reasons:
        ax.text(0.07, 0.36, "—", transform=ax.transAxes, color=tv.MUTED, fontsize=10, zorder=3)
    else:
        y = 0.36
        for reason in reasons:
            wrapped = textwrap.wrap(reason, width=78) or [reason]
            line = wrapped[0] + ("…" if len(wrapped) > 1 or len(reason) > 78 else "")
            ax.text(
                0.07,
                y,
                f"▸  {line}",
                transform=ax.transAxes,
                color=tv.TEXT,
                fontsize=9.5,
                ha="left",
                va="center",
                zorder=3,
            )
            y -= 0.038

    # Risks
    ax.text(
        0.07,
        0.20,
        "RISIKEN",
        transform=ax.transAxes,
        color=tv.MUTED,
        fontsize=9,
        fontweight="bold",
        ha="left",
        va="center",
        zorder=3,
    )
    risks = (result.counter_arguments or [])[:2]
    y = 0.16
    if not risks:
        ax.text(0.07, y, "—", transform=ax.transAxes, color=tv.MUTED, fontsize=10, zorder=3)
    else:
        for risk_line in risks:
            wrapped = textwrap.wrap(risk_line, width=78) or [risk_line]
            line = wrapped[0] + ("…" if len(wrapped) > 1 else "")
            ax.text(
                0.07,
                y,
                f"▸  {line}",
                transform=ax.transAxes,
                color="#c9a0a0",
                fontsize=9.5,
                ha="left",
                va="center",
                zorder=3,
            )
            y -= 0.035

    # Footer meta
    ax.plot([0.07, 0.93], [0.095, 0.095], color=tv.CROSS, linewidth=0.8, transform=ax.transAxes, zorder=3)
    ax.text(
        0.07,
        0.065,
        f"{stamp}  ·  gueltig bis {expires}  ·  Daten {result.data_quality:.0f}/100",
        transform=ax.transAxes,
        color=tv.MUTED,
        fontsize=8.5,
        ha="left",
        va="center",
        zorder=3,
    )
    ax.text(
        0.07,
        0.035,
        "Keine Finanzberatung. Kryptowaehrungen sind hochriskant.",
        transform=ax.transAxes,
        color=tv.MUTED,
        fontsize=8,
        ha="left",
        va="center",
        zorder=3,
        fontstyle="italic",
    )

    # Small logo bottom-right
    logo_base = tv._load_logo_base()
    if logo_base is not None:
        from matplotlib.offsetbox import AnnotationBbox, OffsetImage

        rgba = logo_base.astype(np.float32)
        rgba[..., 3] = np.clip(rgba[..., 3] * 0.55, 0, 255)
        imagebox = OffsetImage(rgba.astype(np.uint8), zoom=0.11)
        ab = AnnotationBbox(
            imagebox,
            (0.90, 0.055),
            xycoords="axes fraction",
            frameon=False,
            pad=0.0,
            zorder=4,
        )
        ax.add_artist(ab)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=tv.DPI, facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()
