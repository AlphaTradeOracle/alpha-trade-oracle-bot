"""Trendindikatoren.

Alle Funktionen sind reine Funktionen ueber pandas-Series und enthalten kein I/O.
Sie sind damit einzeln testbar und im Backtest bitgenau identisch zum Live-Betrieb.

Konvention: Der zurueckgegebene Index entspricht immer dem Eingabeindex. Werte,
die aufgrund der Aufwaermphase nicht berechenbar sind, bleiben ``NaN`` — sie
werden nie mit 0 aufgefuellt, weil das Signale verfaelschen wuerde.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponentieller gleitender Durchschnitt.

    ``adjust=False`` entspricht der rekursiven Definition, die Charting-Software
    verwendet: ``EMA_t = alpha * x_t + (1 - alpha) * EMA_{t-1}``.
    """
    _validate_period(period, len(series))
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Einfacher gleitender Durchschnitt."""
    _validate_period(period, len(series))
    return series.rolling(window=period, min_periods=period).mean()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range nach Wilder: das Maximum der drei Spannweiten."""
    prev_close = close.shift(1)
    ranges = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range mit Wilder-Glaettung (RMA)."""
    _validate_period(period, len(close))
    tr = true_range(high, low, close)
    return _wilder_smooth(tr, period)


def adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """ADX mit +DI und -DI nach Wilder.

    Rueckgabe: ``(adx, plus_di, minus_di)``. ADX misst die Trendstaerke ohne
    Richtung; die Richtung liefert das Verhaeltnis von +DI zu -DI.
    """
    _validate_period(period, len(close))

    up_move = high.diff()
    down_move = -low.diff()

    # Ein gerichteter Impuls zaehlt nur, wenn er den Gegenimpuls uebersteigt.
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index
    )

    tr_smooth = _wilder_smooth(true_range(high, low, close), period)
    plus_di = 100.0 * _wilder_smooth(plus_dm, period) / tr_smooth.replace(0.0, np.nan)
    minus_di = 100.0 * _wilder_smooth(minus_dm, period) / tr_smooth.replace(0.0, np.nan)

    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx_series = _wilder_smooth(dx, period)

    return adx_series, plus_di, minus_di


def supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
) -> tuple[pd.Series, pd.Series]:
    """Supertrend-Linie und Richtung (+1 bullisch, -1 baerisch).

    Die Baender werden nachgezogen (Trailing), damit sie sich nur in
    Trendrichtung bewegen. Die Schleife ist bewusst explizit: der Supertrend ist
    pfadabhaengig und laesst sich nicht sinnvoll vektorisieren.
    """
    _validate_period(period, len(close))

    atr_values = atr(high, low, close, period)
    hl2 = (high + low) / 2.0
    upper_basic = hl2 + multiplier * atr_values
    lower_basic = hl2 - multiplier * atr_values

    n = len(close)
    trend = np.full(n, np.nan)
    direction = np.full(n, np.nan)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)

    close_values = close.to_numpy(dtype=float)
    upper_values = upper_basic.to_numpy(dtype=float)
    lower_values = lower_basic.to_numpy(dtype=float)

    started = False
    for i in range(n):
        if np.isnan(upper_values[i]) or np.isnan(lower_values[i]):
            continue

        if not started:
            upper[i] = upper_values[i]
            lower[i] = lower_values[i]
            direction[i] = 1.0
            trend[i] = lower[i]
            started = True
            continue

        prev = i - 1
        prev_upper = upper[prev] if not np.isnan(upper[prev]) else upper_values[i]
        prev_lower = lower[prev] if not np.isnan(lower[prev]) else lower_values[i]

        # Baender nur in Trendrichtung nachziehen.
        upper[i] = (
            min(upper_values[i], prev_upper)
            if close_values[prev] <= prev_upper
            else upper_values[i]
        )
        lower[i] = (
            max(lower_values[i], prev_lower)
            if close_values[prev] >= prev_lower
            else lower_values[i]
        )

        prev_direction = direction[prev] if not np.isnan(direction[prev]) else 1.0
        if close_values[i] > upper[i]:
            direction[i] = 1.0
        elif close_values[i] < lower[i]:
            direction[i] = -1.0
        else:
            direction[i] = prev_direction

        trend[i] = lower[i] if direction[i] > 0 else upper[i]

    return (
        pd.Series(trend, index=close.index, name="supertrend"),
        pd.Series(direction, index=close.index, name="supertrend_direction"),
    )


def _wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    """Wilders geglaetteter Durchschnitt (RMA), Basis von ATR, ADX und RSI."""
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _validate_period(period: int, length: int) -> None:
    if period <= 0:
        raise ValueError(f"Periode muss positiv sein, war: {period}")
    if length == 0:
        raise ValueError("Eingabeserie ist leer")
