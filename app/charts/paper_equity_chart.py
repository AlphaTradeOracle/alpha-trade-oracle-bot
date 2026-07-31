"""Equity-Chart fuer den stuendlichen Paper-Digest (Cash + Open PnL)."""

from __future__ import annotations

import io
from datetime import datetime
from typing import Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from app.core.logging import get_logger

logger = get_logger(__name__)

FIGURE_SIZE = (10.5, 5.2)
DPI = 140

_BG = "#0b1016"
_PANEL = "#10161e"
_GRID = "#1c2530"
_TEXT = "#e8eef5"
_MUTED = "#8b98a5"
_UP = "#2ecc8a"
_DOWN = "#ef5b67"
_LINE = "#5eb8ff"
_START = "#f0c75e"


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
        # Matplotlib braucht mind. 2 Punkte fuer eine Linie.
        twin = as_of if as_of != points[0][0] else start_at
        points.append((twin, float(live_equity)))
    return points


def build_paper_equity_chart(
    points: Sequence[tuple[datetime, float]],
    *,
    initial: float,
    title: str = "Paper Equity",
    subtitle: str = "Cash + Open PnL",
) -> bytes | None:
    """PNG-Bytes fuer Telegram; None bei zu wenigen/ungueltigen Punkten."""
    if len(points) < 2:
        return None
    try:
        return _render(points, initial=float(initial), title=title, subtitle=subtitle)
    except Exception as exc:
        logger.warning("paper_equity_chart_failed", error=str(exc))
        return None


def _render(
    points: Sequence[tuple[datetime, float]],
    *,
    initial: float,
    title: str,
    subtitle: str,
) -> bytes:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    last = ys[-1]
    up = last >= initial
    accent = _UP if up else _DOWN
    fill_alpha = 0.22

    fig, ax = plt.subplots(figsize=FIGURE_SIZE, dpi=DPI)
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_PANEL)

    ax.plot(xs, ys, color=_LINE, linewidth=2.4, solid_capstyle="round", zorder=3)
    ax.fill_between(xs, ys, initial, where=[y >= initial for y in ys],
                     interpolate=True, color=_UP, alpha=fill_alpha, zorder=2)
    ax.fill_between(xs, ys, initial, where=[y < initial for y in ys],
                     interpolate=True, color=_DOWN, alpha=fill_alpha, zorder=2)
    ax.axhline(initial, color=_START, linewidth=1.1, linestyle="--", alpha=0.85, zorder=2)

    y_min = min(min(ys), initial)
    y_max = max(max(ys), initial)
    pad = max((y_max - y_min) * 0.18, abs(initial) * 0.004, 8.0)
    ax.set_ylim(y_min - pad, y_max + pad)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m\n%H:%M"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=7))
    ax.tick_params(colors=_MUTED, labelsize=9)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _p: f"${v:,.0f}"))
    ax.grid(True, color=_GRID, linewidth=0.8, alpha=0.9)
    for spine in ax.spines.values():
        spine.set_color(_GRID)

    ax.set_title(title, color=_TEXT, fontsize=15, fontweight="bold", loc="left", pad=14)
    ax.text(
        0.0,
        1.02,
        subtitle,
        transform=ax.transAxes,
        color=_MUTED,
        fontsize=10,
        ha="left",
        va="bottom",
    )

    delta = last - initial
    delta_pct = (delta / initial * 100.0) if initial else 0.0
    badge = f"${last:,.2f}  ({delta:+,.2f} · {delta_pct:+.1f}%)"
    ax.annotate(
        badge,
        xy=(xs[-1], last),
        xytext=(-8, 12),
        textcoords="offset points",
        color=accent,
        fontsize=11,
        fontweight="bold",
        ha="right",
        va="bottom",
        zorder=4,
        bbox={
            "boxstyle": "round,pad=0.35",
            "facecolor": _BG,
            "edgecolor": accent,
            "linewidth": 1.0,
            "alpha": 0.92,
        },
    )
    ax.scatter([xs[-1]], [last], color=accent, s=36, zorder=4, edgecolors=_BG, linewidths=1.2)

    ax.text(
        0.0,
        -0.14,
        f"Start ${initial:,.2f}",
        transform=ax.transAxes,
        color=_START,
        fontsize=9,
        ha="left",
        va="top",
    )

    fig.tight_layout(pad=1.2)
    # Platz fuer Title/Subtitle
    fig.subplots_adjust(top=0.86, bottom=0.16)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)
    return buf.getvalue()
