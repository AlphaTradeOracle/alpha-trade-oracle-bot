"""BTC rising-momentum gate for new short entries.

Independent of market-regime / hard-veto labels: blocks SHORT / STRONG_SHORT
when BTC is rising on closed 1h / 3h / 4h / 6h windows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from app.core.logging import get_logger

logger = get_logger(__name__)


class _OhlcLike(Protocol):
    open: float
    close: float

    @property
    def is_closed(self) -> bool: ...


@dataclass(frozen=True)
class BtcRiseThresholds:
    """Configurable OR-triggers for the short pause."""

    enabled: bool = True
    pct_1h: float = 0.15
    pct_3h: float = 0.35
    pct_4h: float = 0.30
    pct_6h: float = 0.50
    use_1h: bool = True
    use_3h: bool = True
    use_4h: bool = True
    use_6h: bool = True


@dataclass(frozen=True)
class BtcRiseMetrics:
    """Measured closed-candle BTC changes (percent). Missing legs are None."""

    pct_1h: float | None
    pct_3h: float | None
    pct_4h: float | None
    pct_6h: float | None
    available: bool
    detail: str


def thresholds_from_settings(settings: object) -> BtcRiseThresholds:
    """Map Settings / duck-typed config onto thresholds."""
    return BtcRiseThresholds(
        enabled=bool(getattr(settings, "btc_rise_short_block_enabled", True)),
        pct_1h=float(getattr(settings, "btc_rise_1h_pct", 0.15)),
        pct_3h=float(getattr(settings, "btc_rise_3h_pct", 0.35)),
        pct_4h=float(getattr(settings, "btc_rise_4h_pct", 0.30)),
        pct_6h=float(getattr(settings, "btc_rise_6h_pct", 0.50)),
        use_1h=bool(getattr(settings, "btc_rise_use_1h", True)),
        use_3h=bool(getattr(settings, "btc_rise_use_3h", True)),
        use_4h=bool(getattr(settings, "btc_rise_use_4h", True)),
        use_6h=bool(getattr(settings, "btc_rise_use_6h", True)),
    )


def _as_closed(candles: Sequence[_OhlcLike] | None) -> list[_OhlcLike]:
    if not candles:
        return []
    closed: list[_OhlcLike] = []
    for candle in candles:
        is_closed = getattr(candle, "is_closed", True)
        if is_closed:
            closed.append(candle)
    return closed


def _bar_pct(open_price: float, close_price: float) -> float | None:
    if open_price <= 0:
        return None
    return (close_price - open_price) / open_price * 100.0


def _close_vs_close_pct(earlier_close: float, later_close: float) -> float | None:
    if earlier_close <= 0:
        return None
    return (later_close - earlier_close) / earlier_close * 100.0


def compute_btc_rise_metrics(
    candles_1h: Sequence[_OhlcLike] | None,
    candles_4h: Sequence[_OhlcLike] | None,
) -> BtcRiseMetrics:
    """Derive rise metrics from *closed* 1h/4h candles only (no open-bar lookahead)."""
    c1 = _as_closed(candles_1h)
    c4 = _as_closed(candles_4h)

    pct_1h: float | None = None
    pct_3h: float | None = None
    pct_6h: float | None = None
    pct_4h: float | None = None

    if c1:
        last = c1[-1]
        pct_1h = _bar_pct(float(last.open), float(last.close))
        if len(c1) >= 3:
            first = c1[-3]
            pct_3h = _bar_pct(float(first.open), float(last.close))
        if len(c1) >= 7:
            # close_now vs close of the candle whose open was 6h earlier
            earlier = c1[-7]
            pct_6h = _close_vs_close_pct(float(earlier.close), float(last.close))

    if c4:
        last4 = c4[-1]
        pct_4h = _bar_pct(float(last4.open), float(last4.close))

    available = bool(c1) or bool(c4)
    detail = (
        f"btc_rise 1h={_fmt(pct_1h)} 3h={_fmt(pct_3h)} "
        f"4h={_fmt(pct_4h)} 6h={_fmt(pct_6h)} "
        f"n1h={len(c1)} n4h={len(c4)}"
    )
    return BtcRiseMetrics(
        pct_1h=pct_1h,
        pct_3h=pct_3h,
        pct_4h=pct_4h,
        pct_6h=pct_6h,
        available=available,
        detail=detail,
    )


def _fmt(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2f}%"


def btc_rising_short_block_reason(
    candles_1h: Sequence[_OhlcLike] | None,
    candles_4h: Sequence[_OhlcLike] | None,
    *,
    thresholds: BtcRiseThresholds | None = None,
) -> str | None:
    """Return NO_TRADE reason when BTC rising triggers fire; else None.

    Missing BTC candles → None (caller should log degrade). Does not inspect
    trade direction — callers must only apply this to short entries.
    """
    cfg = thresholds or BtcRiseThresholds()
    if not cfg.enabled:
        return None

    metrics = compute_btc_rise_metrics(candles_1h, candles_4h)
    if not metrics.available:
        return None

    hits: list[str] = []
    if cfg.use_1h and metrics.pct_1h is not None and metrics.pct_1h >= cfg.pct_1h:
        hits.append(f"1h={_fmt(metrics.pct_1h)}")
    if cfg.use_3h and metrics.pct_3h is not None and metrics.pct_3h >= cfg.pct_3h:
        hits.append(f"3h={_fmt(metrics.pct_3h)}")
    if cfg.use_4h and metrics.pct_4h is not None and metrics.pct_4h >= cfg.pct_4h:
        hits.append(f"4h={_fmt(metrics.pct_4h)}")
    if cfg.use_6h and metrics.pct_6h is not None and metrics.pct_6h >= cfg.pct_6h:
        hits.append(f"6h={_fmt(metrics.pct_6h)}")

    if not hits:
        return None

    detail = (
        f"1h={_fmt(metrics.pct_1h)}, 3h={_fmt(metrics.pct_3h)}, "
        f"4h={_fmt(metrics.pct_4h)}, 6h={_fmt(metrics.pct_6h)}"
    )
    return f"BTC rising momentum — no new short entries ({detail})"


def log_btc_rise_degraded(detail: str) -> None:
    logger.warning("btc_rise_short_block_degraded", detail=detail)


__all__ = [
    "BtcRiseMetrics",
    "BtcRiseThresholds",
    "btc_rising_short_block_reason",
    "compute_btc_rise_metrics",
    "log_btc_rise_degraded",
    "thresholds_from_settings",
]
