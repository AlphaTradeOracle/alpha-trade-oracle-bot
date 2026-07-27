"""Volumen-Indikatoren."""

from __future__ import annotations

import numpy as np
import pandas as pd


def on_balance_volume(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume: kumuliertes, richtungsgewichtetes Volumen."""
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume).cumsum()


def volume_moving_average(volume: pd.Series, period: int = 20) -> pd.Series:
    if period <= 0:
        raise ValueError(f"Periode muss positiv sein, war: {period}")
    return volume.rolling(period, min_periods=period).mean()


def volume_ratio(volume: pd.Series, period: int = 20) -> pd.Series:
    """Aktuelles Volumen im Verhaeltnis zum Durchschnitt.

    Ein Wert von 2.0 bedeutet doppeltes Durchschnittsvolumen — das klassische
    Kriterium fuer eine Volumenspitze bei einem Ausbruch.
    """
    average = volume_moving_average(volume, period)
    return volume / average.replace(0.0, np.nan)


def vwap(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    index: pd.DatetimeIndex | None = None,
) -> pd.Series:
    """Volume Weighted Average Price, taeglich zurueckgesetzt.

    Der VWAP ist per Definition ein Intraday-Indikator. Ohne taeglichen Reset
    wuerde er auf langen Historien zu einem nahezu konstanten Wert degenerieren
    und waere fuer die Analyse wertlos. Fuer Tages-Timeframes ist er nicht
    aussagekraeftig und wird von der Engine dort nicht ausgewertet.
    """
    typical_price = (high + low + close) / 3.0
    pv = typical_price * volume

    idx = index if index is not None else close.index
    if isinstance(idx, pd.DatetimeIndex):
        session = pd.Series(idx.date, index=close.index)
        cumulative_pv = pv.groupby(session).cumsum()
        cumulative_volume = volume.groupby(session).cumsum()
    else:
        cumulative_pv = pv.cumsum()
        cumulative_volume = volume.cumsum()

    return cumulative_pv / cumulative_volume.replace(0.0, np.nan)
