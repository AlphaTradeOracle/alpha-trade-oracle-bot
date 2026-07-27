"""Momentum-Indikatoren."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators.trend import ema


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index nach Wilder.

    Rueckgabe im Bereich 0..100. Bei ausschliesslich steigenden Kursen laeuft der
    RSI gegen 100; die Division durch 0 wird ueber ``np.nan`` abgefangen und
    anschliessend korrekt auf 100 gesetzt.
    """
    if period <= 0:
        raise ValueError(f"Periode muss positiv sein, war: {period}")

    delta = series.diff()
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)

    avg_gain = gains.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = losses.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    result = 100.0 - (100.0 / (1.0 + rs))

    # Kein Verlust in der Periode -> RSI 100; kein Gewinn -> RSI 0.
    result = result.where(avg_loss != 0.0, 100.0)
    result = result.where(~((avg_gain == 0.0) & (avg_loss == 0.0)), 50.0)
    return result.where(avg_gain.notna() & avg_loss.notna())


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD-Linie, Signallinie und Histogramm."""
    if fast >= slow:
        raise ValueError(f"fast ({fast}) muss kleiner als slow ({slow}) sein")

    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def stochastic_rsi(
    series: pd.Series,
    rsi_period: int = 14,
    stoch_period: int = 14,
    k_smooth: int = 3,
    d_smooth: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """Stochastic RSI: %K und %D im Bereich 0..100.

    Misst, wo der RSI innerhalb seiner eigenen Spanne der letzten Perioden liegt —
    reagiert damit deutlich frueher als der RSI selbst.
    """
    rsi_values = rsi(series, rsi_period)
    lowest = rsi_values.rolling(stoch_period, min_periods=stoch_period).min()
    highest = rsi_values.rolling(stoch_period, min_periods=stoch_period).max()

    span = (highest - lowest).replace(0.0, np.nan)
    raw_k = 100.0 * (rsi_values - lowest) / span
    # Flache RSI-Spanne bedeutet weder ueberkauft noch ueberverkauft.
    raw_k = raw_k.where(span.notna(), 50.0).where(rsi_values.notna())

    k = raw_k.rolling(k_smooth, min_periods=k_smooth).mean()
    d = k.rolling(d_smooth, min_periods=d_smooth).mean()
    # Die Division kann durch Fliesskommarundung minimal ueber 100 hinauslaufen.
    return k.clip(0.0, 100.0), d.clip(0.0, 100.0)


def rate_of_change(series: pd.Series, period: int = 14) -> pd.Series:
    """Prozentuale Kursveraenderung ueber ``period`` Kerzen."""
    if period <= 0:
        raise ValueError(f"Periode muss positiv sein, war: {period}")
    shifted = series.shift(period).replace(0.0, np.nan)
    return (series - shifted) / shifted * 100.0
