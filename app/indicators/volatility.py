"""Volatilitaets-Indikatoren."""

from __future__ import annotations

import numpy as np
import pandas as pd


def bollinger_bands(
    series: pd.Series, period: int = 20, std_multiplier: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Bollinger Baender.

    Rueckgabe: ``(upper, middle, lower, width_percent)``. ``width_percent`` ist
    die Bandbreite relativ zum Mittelwert — der uebliche Indikator fuer
    Squeeze (enge Baender) und Expansion (weite Baender).

    Es wird die Populations-Standardabweichung (``ddof=0``) verwendet, wie in
    Bollingers Originaldefinition und in Charting-Software ueblich.
    """
    if period <= 1:
        raise ValueError(f"Periode muss groesser als 1 sein, war: {period}")

    middle = series.rolling(period, min_periods=period).mean()
    std = series.rolling(period, min_periods=period).std(ddof=0)

    upper = middle + std_multiplier * std
    lower = middle - std_multiplier * std
    width = (upper - lower) / middle.replace(0.0, np.nan) * 100.0
    return upper, middle, lower, width
