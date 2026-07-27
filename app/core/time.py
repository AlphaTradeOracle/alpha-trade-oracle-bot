"""Zeit-Hilfsfunktionen. Intern gilt ausschliesslich UTC."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

#: Dauer der unterstuetzten Timeframes in Minuten.
TIMEFRAME_MINUTES: dict[str, int] = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "6h": 360,
    "8h": 480,
    "12h": 720,
    "1d": 1440,
    "3d": 4320,
    "1w": 10080,
}


def utc_now() -> datetime:
    """Aktueller Zeitpunkt als timezone-aware UTC-datetime."""
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Naive datetimes als UTC interpretieren, aware datetimes konvertieren."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def timeframe_to_timedelta(timeframe: str) -> timedelta:
    minutes = TIMEFRAME_MINUTES.get(timeframe)
    if minutes is None:
        raise ValueError(
            f"Unbekannter Timeframe: {timeframe!r}. "
            f"Unterstuetzt: {', '.join(sorted(TIMEFRAME_MINUTES))}"
        )
    return timedelta(minutes=minutes)


def timeframe_minutes(timeframe: str) -> int:
    return int(timeframe_to_timedelta(timeframe).total_seconds() // 60)


def ms_to_datetime(milliseconds: int | float) -> datetime:
    """Millisekunden-Epoch (Binance-Format) in UTC-datetime umwandeln."""
    return datetime.fromtimestamp(float(milliseconds) / 1000.0, tz=UTC)


def datetime_to_ms(value: datetime) -> int:
    return int(ensure_utc(value).timestamp() * 1000)


def to_display_timezone(value: datetime, timezone_name: str) -> datetime:
    """Nur fuer die Ausgabe: UTC in die Anzeigezeitzone konvertieren."""
    try:
        target = ZoneInfo(timezone_name)
    except Exception:
        return ensure_utc(value)
    return ensure_utc(value).astimezone(target)


def format_display_time(value: datetime, timezone_name: str) -> str:
    local = to_display_timezone(value, timezone_name)
    return local.strftime("%d.%m.%Y %H:%M %Z")
