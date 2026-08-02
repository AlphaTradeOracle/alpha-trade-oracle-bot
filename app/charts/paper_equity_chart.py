"""Equity-Chart im TradingView-Stil fuer das Performance-Dashboard."""

from __future__ import annotations

import io
from datetime import datetime, timedelta
from typing import Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np

from app.charts import theme as tv
from app.core.logging import get_logger

logger = get_logger(__name__)

FIGURE_SIZE = (11.2, 5.6)


def build_equity_curve_points(
    *,
    initial: float,
    start_at: datetime,
    fills: Sequence[tuple[datetime, float, float]],
    as_of: datetime,
    live_equity: float,
) -> list[tuple[datetime, float]]:
    """Equity-Punkte: Start → nach jedem Fill → Live-Equity (MTM).

    ``pnl`` ist immer die Equity-Delta-Komponente (Entry: −fee; Exit: gross−fee).
    Die ``fee``-Spalte ist nur Metadaten — erneut abziehen wuerde Exit-Fees
    doppelt zaehlen (historischer Bug in Desk-/Digest-Fenstern).
    Legacy-Entry-Fills mit ``pnl=0`` werden als −fee interpretiert.
    """
    points: list[tuple[datetime, float]] = [(start_at, float(initial))]
    running = float(initial)
    for filled_at, pnl, fee in fills:
        pnl_f = float(pnl)
        fee_f = float(fee)
        # Legacy entry rows wrote pnl=0 and fee>0; new rows write pnl=-fee.
        if abs(pnl_f) < 1e-12 and fee_f > 0:
            running -= fee_f
        else:
            running += pnl_f
        if points and points[-1][0] == filled_at:
            points[-1] = (filled_at, running)
        else:
            points.append((filled_at, running))

    if not points or points[-1][0] != as_of:
        points.append((as_of, float(live_equity)))
    else:
        points[-1] = (as_of, float(live_equity))

    if len(points) == 1:
        twin = as_of if as_of != points[0][0] else start_at
        points.append((twin, float(live_equity)))
    return points


def build_paper_equity_chart(
    points: Sequence[tuple[datetime, float]],
    *,
    initial: float,
    title: str = "EQUITY",
    subtitle: str = "Cash + Open PnL",
    windows: Sequence[tuple[str, float]] | None = None,
) -> bytes | None:
    """PNG-Bytes fuer Telegram; None bei zu wenigen/ungueltigen Punkten.

    ``windows``: optionale Performance-Fenster ``(label, equity_delta)``,
    z. B. ``(\"1h\", 12.5)`` — werden oben rechts im Header gerendert.
    """
    if len(points) < 2:
        return None
    try:
        return _render(
            points,
            initial=float(initial),
            title=title,
            subtitle=subtitle,
            windows=list(windows or ()),
        )
    except Exception as exc:
        logger.warning("paper_equity_chart_failed", error=str(exc))
        return None


def _render(
    points: Sequence[tuple[datetime, float]],
    *,
    initial: float,
    title: str,
    subtitle: str,
    windows: list[tuple[str, float]],
) -> bytes:
    xs = [p[0] for p in points]
    ys = np.asarray([p[1] for p in points], dtype=float)
    # Always frame the last 7 days (flat at initial before account/history starts).
    view_end = xs[-1]
    view_start = view_end - timedelta(days=7)
    if xs[0] > view_start:
        xs = [view_start, *xs]
        ys = np.concatenate([[float(initial)], ys])
    last = float(ys[-1])
    up = last >= initial
    accent = tv.UP if up else tv.DOWN
    line_color = "#4fc3f7" if up else "#ff8a80"

    fig, ax = plt.subplots(figsize=FIGURE_SIZE)
    tv.style_figure(fig)
    tv.style_axes(ax)
    tv.watermark(ax, alpha=0.70, zoom=0.140, loc="top_left", xycoords="figure fraction")

    # Soft glow under line
    ax.plot(xs, ys, color=line_color, linewidth=4.8, alpha=0.18, solid_capstyle="round", zorder=2)
    ax.plot(xs, ys, color=line_color, linewidth=2.35, solid_capstyle="round", zorder=3)

    # Gradient-like fill vs start
    above = ys >= initial
    below = ys < initial
    ax.fill_between(xs, ys, initial, where=above, interpolate=True, color=tv.UP, alpha=0.16, zorder=1.5)
    ax.fill_between(xs, ys, initial, where=below, interpolate=True, color=tv.DOWN, alpha=0.16, zorder=1.5)

    # Start / baseline
    ax.axhline(initial, color=tv.WARN, linewidth=1.15, linestyle=(0, (5, 3.5)), alpha=0.9, zorder=2)
    ax.axhline(last, color=accent, linewidth=0.7, linestyle=(0, (1.5, 3)), alpha=0.45, zorder=2)

    y_min = float(min(ys.min(), initial))
    y_max = float(max(ys.max(), initial))
    pad = max((y_max - y_min) * 0.20, abs(initial) * 0.004, 10.0)
    ax.set_ylim(y_min - pad, y_max + pad)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _p: tv.fmt_usd(float(v), decimals=0)))
    ax.tick_params(axis="x", labelsize=8, pad=6)

    # Right-side TV tags; x-range fixed to last 7 days
    x_span = mdates.date2num(view_end) - mdates.date2num(view_start)
    tag_x = mdates.date2num(view_end) + max(x_span * 0.02, 0.01)
    ax.set_xlim(
        mdates.date2num(view_start),
        mdates.date2num(view_end) + max(x_span * 0.22, 0.08),
    )

    delta = last - initial
    delta_pct = (delta / initial * 100.0) if initial else 0.0
    _equity_tag(ax, tag_x, last, f"{tv.fmt_usd(last)}  {delta_pct:+.1f}%", accent)
    _equity_tag(ax, tag_x, initial, f"Start  {tv.fmt_usd(initial)}", tv.WARN, alpha=0.88)

    # End marker
    ax.scatter(
        [xs[-1]],
        [last],
        s=70,
        color=accent,
        edgecolors=tv.BG,
        linewidths=1.6,
        zorder=5,
    )
    ax.scatter([xs[-1]], [last], s=180, color=accent, alpha=0.18, zorder=4)

    # Header: Titel links; 1H|24H|7D|Gesamtwert rechts (Dollar, darunter %)
    fig.subplots_adjust(left=0.05, right=0.84, top=0.80, bottom=0.14)
    fig.text(
        0.225,
        0.945,
        title,
        color=tv.TEXT,
        fontsize=15,
        fontweight="bold",
        ha="left",
        va="center",
    )
    fig.text(
        0.225,
        0.905,
        subtitle,
        color=tv.MUTED,
        fontsize=10,
        ha="left",
        va="center",
    )

    chip_color = tv.UP if up else tv.DOWN

    # 1H | 24H | 7D | 30D | TOTAL — Dollar mit $-Suffix, darunter Prozent
    cols = list(windows[-4:]) if windows else []
    right = 0.985
    win_w = 0.078
    total_w = 0.12
    cluster_left = right - total_w - win_w * len(cols)

    for i, (label, eq_delta) in enumerate(cols):
        cx = cluster_left + win_w * (i + 0.5)
        color = tv.UP if eq_delta >= 0 else tv.DOWN
        pct = (eq_delta / initial * 100.0) if initial else 0.0
        fig.text(
            cx,
            0.955,
            str(label).upper(),
            color=tv.MUTED,
            fontsize=7.5,
            fontweight="bold",
            ha="center",
            va="center",
        )
        fig.text(
            cx,
            0.920,
            _fmt_signed_usd_suffix(eq_delta),
            color=color,
            fontsize=9.5,
            fontweight="bold",
            ha="center",
            va="center",
        )
        fig.text(
            cx,
            0.885,
            _fmt_signed_pct(pct),
            color=color,
            fontsize=8,
            fontweight="bold",
            ha="center",
            va="center",
        )

    # TOTAL: equity · $ PnL since start · return %
    fig.text(
        right,
        0.968,
        "TOTAL",
        color=tv.MUTED,
        fontsize=6.5,
        fontweight="bold",
        ha="right",
        va="center",
    )
    fig.text(
        right,
        0.940,
        _fmt_usd_suffix(last),
        color=tv.TEXT,
        fontsize=9.0,
        fontweight="bold",
        ha="right",
        va="center",
    )
    fig.text(
        right,
        0.910,
        _fmt_signed_usd_suffix(delta),
        color=chip_color,
        fontsize=8.5,
        fontweight="bold",
        ha="right",
        va="center",
    )
    fig.text(
        right,
        0.880,
        _fmt_signed_pct(delta_pct),
        color=chip_color,
        fontsize=7.5,
        fontweight="bold",
        ha="right",
        va="center",
    )

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=tv.DPI, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)
    return buffer.getvalue()


def _fmt_de_number(value: float, *, decimals: int = 2) -> str:
    body = f"{abs(value):,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return body


def _fmt_signed_usd_suffix(value: float) -> str:
    """Kompaktes ``+12,50$`` / ``-3,00$`` fuer Chart-Header."""
    sign = "+" if value >= 0 else "-"
    return f"{sign}{_fmt_de_number(value)}$"


def _fmt_usd_suffix(value: float) -> str:
    """Kompaktes ``5.011,60$`` fuer Chart-Header."""
    return f"{_fmt_de_number(value)}$"


def _fmt_signed_pct(value: float) -> str:
    """Kompaktes ``+0,3%`` / ``-1,2%`` fuer Chart-Header."""
    sign = "+" if value >= 0 else "-"
    return f"{sign}{_fmt_de_number(value, decimals=1)}%"


def _equity_tag(
    ax,
    x: float,
    y: float,
    text: str,
    color: str,
    *,
    alpha: float = 1.0,
) -> None:
    ax.annotate(
        f" {text} ",
        xy=(x, y),
        xytext=(8, 0),
        textcoords="offset points",
        color="#ffffff",
        fontsize=8.2,
        fontweight="bold",
        va="center",
        ha="left",
        clip_on=False,
        zorder=6,
        bbox={
            "boxstyle": "round,pad=0.30,rounding_size=0.35",
            "facecolor": color,
            "edgecolor": "none",
            "alpha": alpha,
        },
    )
