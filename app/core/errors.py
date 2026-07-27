"""Anwendungsspezifische Fehlertypen mit aussagekraeftigen Meldungen."""

from __future__ import annotations


class AlphaTradeOracleError(Exception):
    """Basisklasse aller fachlichen Fehler dieser Anwendung."""

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.message} ({self.detail})" if self.detail else self.message


class ConfigurationError(AlphaTradeOracleError):
    """Eine benoetigte Konfiguration fehlt oder ist unbrauchbar."""


class MarketDataError(AlphaTradeOracleError):
    """Marktdaten konnten nicht geladen werden."""


class SymbolNotFoundError(MarketDataError):
    """Das angefragte Handelspaar existiert beim Provider nicht."""

    def __init__(self, symbol: str) -> None:
        super().__init__(
            f"Handelspaar '{symbol}' ist beim Marktdaten-Provider nicht verfuegbar.",
            detail="Symbol pruefen, z. B. BTCUSDT",
        )
        self.symbol = symbol


class RateLimitError(MarketDataError):
    """Der Provider hat ein Rate Limit gemeldet."""

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message, detail=f"retry_after={retry_after_seconds}")
        self.retry_after_seconds = retry_after_seconds


class InsufficientDataError(MarketDataError):
    """Zu wenige Kerzen fuer eine belastbare Analyse."""

    def __init__(self, symbol: str, timeframe: str, available: int, required: int) -> None:
        super().__init__(
            f"Zu wenige Kerzen fuer {symbol} {timeframe}: {available} vorhanden, "
            f"{required} benoetigt.",
            detail="Historie zu kurz oder Datenluecken",
        )
        self.symbol = symbol
        self.timeframe = timeframe
        self.available = available
        self.required = required


class IndicatorError(AlphaTradeOracleError):
    """Ein Indikator konnte nicht berechnet werden."""


class SignalGenerationError(AlphaTradeOracleError):
    """Die Signal-Engine konnte kein Ergebnis erzeugen."""


class LLMError(AlphaTradeOracleError):
    """Der LLM-Aufruf ist fehlgeschlagen."""


class LLMValidationError(LLMError):
    """Die LLM-Antwort hat die Schemapruefung nicht bestanden."""


class BacktestError(AlphaTradeOracleError):
    """Der Backtest konnte nicht ausgefuehrt werden."""


class AuthorizationError(AlphaTradeOracleError):
    """Der Aufrufer ist nicht berechtigt."""
