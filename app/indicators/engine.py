"""Indicator Engine: berechnet aus OHLCV-Daten einen vollstaendigen Kennzahlensatz.

Die Engine ist eine reine Funktion ueber einem DataFrame. Sie kennt weder
Datenbank noch Netzwerk und wird von Live-Analyse und Backtest identisch
verwendet — das ist die Voraussetzung dafuer, dass Backtestergebnisse ueberhaupt
aussagekraeftig sind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from app.core.enums import TrendDirection
from app.core.errors import InsufficientDataError
from app.indicators.momentum import macd, rate_of_change, rsi, stochastic_rsi
from app.indicators.structure import StructureAnalysis, analyze_structure
from app.indicators.trend import adx, atr, ema, sma, supertrend
from app.indicators.volatility import bollinger_bands
from app.indicators.volume import on_balance_volume, volume_moving_average, volume_ratio, vwap

#: Spalten, die ein OHLCV-DataFrame mindestens enthalten muss.
REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")

#: Kerzen, die fuer den laengsten Indikator (EMA200/SMA200) noetig sind, plus Puffer.
MIN_CANDLES = 210

#: Timeframes, fuer die der VWAP fachlich sinnvoll ist (Intraday).
VWAP_TIMEFRAMES = frozenset({"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h"})


@dataclass
class IndicatorSet:
    """Alle Kennzahlen eines Timeframes zum Zeitpunkt der letzten Kerze."""

    timeframe: str
    candle_open_time: datetime
    close_price: float

    ema_9: float | None = None
    ema_20: float | None = None
    ema_50: float | None = None
    ema_100: float | None = None
    ema_200: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None

    rsi_14: float | None = None
    rsi_previous: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None
    macd_histogram_previous: float | None = None
    stoch_rsi_k: float | None = None
    stoch_rsi_d: float | None = None
    roc_14: float | None = None

    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None
    bb_width: float | None = None
    bb_width_average: float | None = None

    atr_14: float | None = None
    atr_percent: float | None = None

    adx_14: float | None = None
    plus_di: float | None = None
    minus_di: float | None = None

    obv: float | None = None
    obv_slope: float | None = None
    volume: float | None = None
    volume_ma_20: float | None = None
    volume_ratio: float | None = None

    supertrend: float | None = None
    supertrend_direction: int | None = None
    vwap: float | None = None

    trend_direction: TrendDirection = TrendDirection.NEUTRAL
    trend_strength: float = 0.0
    structure: StructureAnalysis = field(default_factory=StructureAnalysis)

    #: Anteil der berechenbaren Indikatoren, 0..100. Fliesst in die Datenqualitaet ein.
    completeness: float = 100.0

    def indicators_used(self) -> list[str]:
        """Namen der tatsaechlich berechneten Indikatoren — fuer die Signalbegruendung."""
        names = {
            "EMA9": self.ema_9,
            "EMA20": self.ema_20,
            "EMA50": self.ema_50,
            "EMA100": self.ema_100,
            "EMA200": self.ema_200,
            "SMA50": self.sma_50,
            "SMA200": self.sma_200,
            "RSI14": self.rsi_14,
            "MACD": self.macd,
            "BollingerBands": self.bb_width,
            "ATR14": self.atr_14,
            "ADX14": self.adx_14,
            "StochRSI": self.stoch_rsi_k,
            "OBV": self.obv,
            "VolumeMA20": self.volume_ma_20,
            "ROC14": self.roc_14,
            "Supertrend": self.supertrend,
            "VWAP": self.vwap,
        }
        return [name for name, value in names.items() if value is not None]

    def to_snapshot_dict(self) -> dict[str, Any]:
        """Flaches Dict fuer die Persistierung als IndicatorSnapshot."""
        return {
            "close_price": self.close_price,
            "ema_9": self.ema_9,
            "ema_20": self.ema_20,
            "ema_50": self.ema_50,
            "ema_100": self.ema_100,
            "ema_200": self.ema_200,
            "sma_50": self.sma_50,
            "sma_200": self.sma_200,
            "rsi_14": self.rsi_14,
            "macd": self.macd,
            "macd_signal": self.macd_signal,
            "macd_histogram": self.macd_histogram,
            "stoch_rsi_k": self.stoch_rsi_k,
            "stoch_rsi_d": self.stoch_rsi_d,
            "roc_14": self.roc_14,
            "bb_upper": self.bb_upper,
            "bb_middle": self.bb_middle,
            "bb_lower": self.bb_lower,
            "bb_width": self.bb_width,
            "atr_14": self.atr_14,
            "atr_percent": self.atr_percent,
            "adx_14": self.adx_14,
            "plus_di": self.plus_di,
            "minus_di": self.minus_di,
            "obv": self.obv,
            "volume_ma_20": self.volume_ma_20,
            "volume_ratio": self.volume_ratio,
            "supertrend": self.supertrend,
            "supertrend_direction": self.supertrend_direction,
            "vwap": self.vwap,
            "trend_direction": self.trend_direction.value,
            "trend_strength": self.trend_strength,
            "structure_state": self.structure.state.value,
            "nearest_support": self.structure.nearest_support,
            "nearest_resistance": self.structure.nearest_resistance,
        }


class IndicatorEngine:
    """Berechnet Indikatoren fuer einen Timeframe."""

    def __init__(self, *, min_candles: int = MIN_CANDLES) -> None:
        self._min_candles = min_candles

    def compute(
        self, df: pd.DataFrame, timeframe: str, *, symbol: str = "?", strict: bool = True
    ) -> IndicatorSet:
        """Kennzahlensatz fuer die letzte abgeschlossene Kerze berechnen.

        Args:
            df: OHLCV-DataFrame mit DatetimeIndex (UTC), aufsteigend sortiert.
            timeframe: Bezeichner wie ``1h`` — steuert unter anderem den VWAP.
            symbol: Nur fuer Fehlermeldungen.
            strict: Bei ``True`` wird zu kurze Historie als Fehler gemeldet.
                Im Backtest steht ``strict=False``, weil dort bewusst Fenster
                iteriert werden, die anfangs kuerzer sind.
        """
        self._validate(df, timeframe, symbol, strict)

        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        volume = df["volume"].astype(float)

        result = IndicatorSet(
            timeframe=timeframe,
            candle_open_time=_index_to_datetime(df.index[-1]),
            close_price=float(close.iloc[-1]),
            volume=float(volume.iloc[-1]),
        )

        available = len(close)
        computed = 0
        possible = 0

        # --- Trend ---------------------------------------------------------
        for period, attr in (
            (9, "ema_9"),
            (20, "ema_20"),
            (50, "ema_50"),
            (100, "ema_100"),
            (200, "ema_200"),
        ):
            possible += 1
            value = _last(ema(close, period)) if available >= period else None
            setattr(result, attr, value)
            computed += value is not None

        for period, attr in ((50, "sma_50"), (200, "sma_200")):
            possible += 1
            value = _last(sma(close, period)) if available >= period else None
            setattr(result, attr, value)
            computed += value is not None

        # --- Momentum ------------------------------------------------------
        possible += 1
        rsi_series = rsi(close, 14) if available >= 15 else None
        if rsi_series is not None:
            result.rsi_14 = _last(rsi_series)
            result.rsi_previous = _last(rsi_series, offset=-2)
            computed += result.rsi_14 is not None

        possible += 1
        if available >= 35:
            macd_line, signal_line, histogram = macd(close)
            result.macd = _last(macd_line)
            result.macd_signal = _last(signal_line)
            result.macd_histogram = _last(histogram)
            result.macd_histogram_previous = _last(histogram, offset=-2)
            computed += result.macd is not None

        possible += 1
        if available >= 34:
            k, d = stochastic_rsi(close)
            result.stoch_rsi_k = _last(k)
            result.stoch_rsi_d = _last(d)
            computed += result.stoch_rsi_k is not None

        possible += 1
        if available >= 15:
            result.roc_14 = _last(rate_of_change(close, 14))
            computed += result.roc_14 is not None

        # --- Volatilitaet ---------------------------------------------------
        possible += 1
        if available >= 20:
            upper, middle, lower, width = bollinger_bands(close)
            result.bb_upper = _last(upper)
            result.bb_middle = _last(middle)
            result.bb_lower = _last(lower)
            result.bb_width = _last(width)
            # Referenz fuer Squeeze-Erkennung: Durchschnitt der letzten 50 Breiten.
            if available >= 70:
                result.bb_width_average = _last(width.rolling(50, min_periods=20).mean())
            computed += result.bb_upper is not None

        possible += 1
        if available >= 15:
            atr_series = atr(high, low, close, 14)
            result.atr_14 = _last(atr_series)
            if result.atr_14 is not None and result.close_price > 0:
                result.atr_percent = result.atr_14 / result.close_price * 100.0
            computed += result.atr_14 is not None

        possible += 1
        if available >= 29:
            adx_series, plus_di, minus_di = adx(high, low, close, 14)
            result.adx_14 = _last(adx_series)
            result.plus_di = _last(plus_di)
            result.minus_di = _last(minus_di)
            computed += result.adx_14 is not None

        # --- Volumen -------------------------------------------------------
        possible += 1
        obv_series = on_balance_volume(close, volume)
        result.obv = _last(obv_series)
        if available >= 20:
            recent = obv_series.iloc[-20:]
            first, last = float(recent.iloc[0]), float(recent.iloc[-1])
            scale = max(abs(first), abs(last), 1.0)
            result.obv_slope = (last - first) / scale
        computed += result.obv is not None

        possible += 1
        if available >= 20:
            result.volume_ma_20 = _last(volume_moving_average(volume, 20))
            result.volume_ratio = _last(volume_ratio(volume, 20))
            computed += result.volume_ma_20 is not None

        # --- Supertrend und VWAP -------------------------------------------
        possible += 1
        if available >= 20:
            st_line, st_direction = supertrend(high, low, close)
            result.supertrend = _last(st_line)
            direction_value = _last(st_direction)
            result.supertrend_direction = int(direction_value) if direction_value else None
            computed += result.supertrend is not None

        possible += 1
        if timeframe in VWAP_TIMEFRAMES:
            result.vwap = _last(vwap(high, low, close, volume, _datetime_index(df)))
            computed += result.vwap is not None
        else:
            # Fuer Tages-Timeframes ist der VWAP nicht aussagekraeftig; das darf
            # die Vollstaendigkeitsquote nicht senken.
            possible -= 1

        # --- Struktur und Trendbewertung -----------------------------------
        result.structure = analyze_structure(high, low, close, rsi_series, atr_value=result.atr_14)
        result.trend_direction, result.trend_strength = self._classify_trend(result)
        result.completeness = round(computed / possible * 100.0, 2) if possible else 0.0

        return result

    def _validate(self, df: pd.DataFrame, timeframe: str, symbol: str, strict: bool) -> None:
        missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
        if missing:
            raise ValueError(
                f"OHLCV-DataFrame fehlen Spalten: {', '.join(missing)}. "
                f"Erwartet: {', '.join(REQUIRED_COLUMNS)}"
            )
        if df.empty:
            raise InsufficientDataError(symbol, timeframe, 0, self._min_candles)
        if strict and len(df) < self._min_candles:
            raise InsufficientDataError(symbol, timeframe, len(df), self._min_candles)

    @staticmethod
    def _classify_trend(indicators: IndicatorSet) -> tuple[TrendDirection, float]:
        """Trendrichtung und -staerke (0..100) aus EMA-Lage, ADX und Supertrend.

        Die Richtung ergibt sich aus mehreren unabhaengigen Stimmen; ADX skaliert
        anschliessend die Staerke, weil eine EMA-Staffelung im Seitwaertsmarkt
        wenig bedeutet.
        """
        votes = 0
        maximum = 0
        price = indicators.close_price

        if indicators.ema_20 is not None and indicators.ema_50 is not None:
            maximum += 1
            votes += 1 if indicators.ema_20 > indicators.ema_50 else -1
        if indicators.ema_50 is not None and indicators.ema_200 is not None:
            maximum += 1
            votes += 1 if indicators.ema_50 > indicators.ema_200 else -1
        if indicators.ema_200 is not None:
            maximum += 1
            votes += 1 if price > indicators.ema_200 else -1
        if indicators.supertrend_direction is not None:
            maximum += 1
            votes += 1 if indicators.supertrend_direction > 0 else -1
        if indicators.plus_di is not None and indicators.minus_di is not None:
            maximum += 1
            votes += 1 if indicators.plus_di > indicators.minus_di else -1

        if maximum == 0:
            return TrendDirection.NEUTRAL, 0.0

        agreement = votes / maximum  # -1..+1
        adx_value = indicators.adx_14 if indicators.adx_14 is not None else 20.0
        # ADX 20 gilt als trendlos, 50 als sehr starker Trend.
        adx_factor = max(0.0, min(1.0, (adx_value - 15.0) / 35.0))
        strength = round(abs(agreement) * (0.45 + 0.55 * adx_factor) * 100.0, 2)

        if agreement >= 0.3:
            return TrendDirection.BULLISH, strength
        if agreement <= -0.3:
            return TrendDirection.BEARISH, strength
        return TrendDirection.NEUTRAL, strength


def _last(series: pd.Series, offset: int = -1) -> float | None:
    """Letzten (oder vorletzten) gueltigen Wert als float, sonst ``None``."""
    if series is None or len(series) < abs(offset):
        return None
    value = series.iloc[offset]
    if pd.isna(value):
        return None
    return float(value)


def _datetime_index(df: pd.DataFrame) -> pd.DatetimeIndex | None:
    return df.index if isinstance(df.index, pd.DatetimeIndex) else None


def _index_to_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return pd.Timestamp(value).to_pydatetime()
