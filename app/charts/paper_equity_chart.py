"""Equity-Chart im TradingView-Stil fuer das Performance-Dashboard."""

from __future__ import annotations

import io
from datetime import datetime
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
    """Equity-Punkte: Start → nach jedem Fill (pnl − fee) → Live-Equity (MTM)."""
    points: list[tuple[datetime, float]] = [(start_at, float(initial))]
    running = float(initial)
    for filled_at, pnl, fee in fills:
        running += float(pnl) - float(fee)
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
    last = float(ys[-1])
    up = last >= initial
    accent = tv.UP if up else tv.DOWN
    line_color = "#4fc3f7" if up else "#ff8a80"

    fig, ax = plt.subplots(figsize=FIGURE_SIZE)
    tv.style_figure(fig)
    tv.style_axes(ax)
    tv.watermark(ax, alpha=0.32, zoom=0.191, loc="top_left", xycoords="figure fraction")

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

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m\n%H:%M"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=7))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _p: tv.fmt_usd(float(v), decimals=0)))
    ax.tick_params(axis="x", labelsize=8, pad=6)

    # Right-side TV tags
    x_span = mdates.date2num(xs[-1]) - mdates.date2num(xs[0])
    tag_x = mdates.date2num(xs[-1]) + max(x_span * 0.02, 0.01)
    ax.set_xlim(
        mdates.date2num(xs[0]) - max(x_span * 0.03, 0.01),
        mdates.date2num(xs[-1]) + max(x_span * 0.22, 0.08),
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

    # Header: Titel links; Gesamtwert mittig-rechts; 1h/24h/7d ganz rechts
    fig.subplots_adjust(left=0.05, right=0.84, top=0.84, bottom=0.14)
    fig.text(
        0.225,
        0.935,
        title,
        color=tv.TEXT,
        fontsize=15,
        fontweight="bold",
        ha="left",
        va="center",
    )
    fig.text(
        0.225,
        0.895,
        subtitle,
        color=tv.MUTED,
        fontsize=10,
        ha="left",
        va="center",
    )

    chip_color = tv.UP if up else tv.DOWN
    delta_label = (
        f"{delta:+,.2f}  ({delta_pct:+.1f}%)"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )

    # 1H | 24H | 7D | Gesamtwert — Gesamtwert am rechten Rand, Fenster gleichmaessig links
    cols = list(windows[-3:]) if windows else []
    right = 0.985
    win_w = 0.095
    total_w = 0.13
    cluster_left = right - total_w - win_w * len(cols)

    for i, (label, eq_delta) in enumerate(cols):
        cx = cluster_left + win_w * (i + 0.5)
        color = tv.UP if eq_delta >= 0 else tv.DOWN
        fig.text(
            cx,
            0.945,
            str(label).upper(),
            color=tv.MUTED,
            fontsize=8,
            fontweight="bold",
            ha="center",
            va="center",
        )
        fig.text(
            cx,
            0.900,
            _fmt_signed_usd(eq_delta),
            color=color,
            fontsize=9.5,
            fontweight="bold",
            ha="center",
            va="center",
        )

    fig.text(
        right,
        0.945,
        tv.fmt_usd(last),
        color=tv.TEXT,
        fontsize=12,
        fontweight="bold",
        ha="right",
        va="center",
    )
    fig.text(
        right,
        0.900,
        delta_label,
        color=chip_color,
        fontsize=9.5,
        fontweight="bold",
        ha="right",
        va="center",
    )

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=tv.DPI, facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)
    return buffer.getvalue()


def _fmt_signed_usd(value: float) -> str:
    """Kompaktes ``+$12,50`` / ``-$3,00`` fuer Chart-Header."""
    sign = "+" if value >= 0 else "-"
    body = f"{abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{sign}${body}"


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
