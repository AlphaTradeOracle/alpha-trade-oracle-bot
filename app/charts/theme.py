"""Gemeinsames TradingView-inspiriertes Chart-Theme."""

from __future__ import annotations

from matplotlib.axes import Axes
from matplotlib.figure import Figure

# TradingView Dark Palette
BG = "#131722"
PANEL = "#131722"
GRID = "#1e222d"
CROSS = "#2a2e39"
TEXT = "#d1d4dc"
MUTED = "#787b86"
UP = "#26a69a"
DOWN = "#ef5350"
VOL_UP = "#26a69a"
VOL_DOWN = "#ef5350"
ENTRY = "#2962ff"
SL = "#f23645"
TP = ("#089981", "#26a69a", "#4caf50")
ACCENT = "#2962ff"
WARN = "#f5d76e"
BRAND = "Alpha Trade Oracle"

DPI = 160


def style_figure(fig: Figure) -> None:
    fig.patch.set_facecolor(BG)


def style_axes(ax: Axes, *, show_right: bool = True) -> None:
    """TradingView-aehnliche Achsen: dunkles Panel, dezentes Grid, Preis rechts."""
    ax.set_facecolor(PANEL)
    ax.tick_params(colors=MUTED, labelsize=8, length=0, pad=3)
    ax.grid(True, which="major", color=GRID, linewidth=0.7, alpha=1.0)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color(CROSS)
        spine.set_linewidth(0.8)
    ax.spines["top"].set_visible(False)
    if show_right:
        ax.yaxis.tick_right()
        ax.yaxis.set_label_position("right")
        ax.spines["left"].set_visible(False)
        ax.spines["right"].set_visible(True)
    else:
        ax.spines["right"].set_visible(False)


def watermark(ax: Axes, text: str = BRAND, *, alpha: float = 0.055) -> None:
    ax.text(
        0.5,
        0.52,
        text,
        transform=ax.transAxes,
        color=TEXT,
        alpha=alpha,
        fontsize=26,
        fontweight="bold",
        ha="center",
        va="center",
        zorder=0,
        clip_on=True,
    )


def fmt_price(value: float, precision: int) -> str:
    formatted = f"{value:,.{precision}f}"
    return formatted.replace(",", "\u00a0").replace(".", ",").replace("\u00a0", ".")


def fmt_usd(value: float, *, decimals: int = 2) -> str:
    formatted = f"{value:,.{decimals}f}"
    de = formatted.replace(",", "\u00a0").replace(".", ",").replace("\u00a0", ".")
    return f"${de}"


def fmt_volume(value: float) -> str:
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if abs_value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if abs_value >= 1_000:
        return f"{value / 1_000:.1f}K"
    if abs_value >= 10:
        return f"{value:.0f}"
    return f"{value:.2f}"
