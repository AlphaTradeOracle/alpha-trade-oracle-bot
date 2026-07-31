"""Gemeinsames TradingView-inspiriertes Chart-Theme."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.offsetbox import AnnotationBbox, OffsetImage
from PIL import Image

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

_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "alpha-trade-oracle-logo.png"
_logo_base: np.ndarray | None = None


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


def _load_logo_base() -> np.ndarray | None:
    global _logo_base
    if _logo_base is not None:
        return _logo_base
    if not _LOGO_PATH.is_file():
        return None
    image = Image.open(_LOGO_PATH).convert("RGBA")
    image.thumbnail((480, 480), Image.Resampling.LANCZOS)
    _logo_base = np.asarray(image)
    return _logo_base


def watermark(
    ax: Axes,
    text: str = BRAND,
    *,
    alpha: float = 0.22,
    zoom: float = 0.19,
    loc: str = "top_left",
    xycoords: str = "figure fraction",
    zorder: float = 12,
) -> None:
    """Dezentes Logo-Wasserzeichen — Standard: oben links (figure)."""
    positions: dict[str, tuple[tuple[float, float], tuple[float, float]]] = {
        # xy, box_alignment — leicht eingerueckt, oben an der Ecke
        "top_left": ((0.012, 0.988), (0.0, 1.0)),
        "center": ((0.5, 0.52), (0.5, 0.5)),
    }
    xy, align = positions.get(loc, positions["top_left"])
    # Bei axes-Koordinaten etwas Luft zum Rand lassen.
    if xycoords == "axes fraction" and loc == "top_left":
        xy = (0.02, 0.97)

    base = _load_logo_base()
    if base is not None:
        rgba = base.astype(np.float32)
        rgba[..., 3] = np.clip(rgba[..., 3] * float(alpha), 0, 255)
        imagebox = OffsetImage(rgba.astype(np.uint8), zoom=zoom)
        artist = AnnotationBbox(
            imagebox,
            xy,
            xycoords=xycoords,
            box_alignment=align,
            frameon=False,
            pad=0.0,
            zorder=zorder,
        )
        ax.add_artist(artist)
        return

    ax.text(
        xy[0],
        xy[1],
        text,
        transform=ax.transAxes if xycoords == "axes fraction" else ax.figure.transFigure,
        color=TEXT,
        alpha=max(alpha, 0.08),
        fontsize=11,
        fontweight="bold",
        ha="left" if loc == "top_left" else "center",
        va="top" if loc == "top_left" else "center",
        zorder=zorder,
        clip_on=False,
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
