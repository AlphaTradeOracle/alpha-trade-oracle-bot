"""UTC-Eintrags-Blackout fuer Signale und Paper-Trades."""

from __future__ import annotations

from datetime import datetime


def parse_utc_blackout_minutes(spec: str) -> tuple[int, int] | None:
    """``HH:MM-HH:MM`` in Minuten seit Mitternacht parsen."""
    cleaned = spec.strip()
    if not cleaned:
        return None
    try:
        start_text, end_text = cleaned.split("-", 1)
        start_h, start_m = (int(part) for part in start_text.strip().split(":", 1))
        end_h, end_m = (int(part) for part in end_text.strip().split(":", 1))
    except (ValueError, AttributeError) as exc:
        raise ValueError(
            f"Blackout muss 'HH:MM-HH:MM' sein (UTC), war: {spec!r}"
        ) from exc
    return start_h * 60 + start_m, end_h * 60 + end_m


def is_in_utc_blackout(when: datetime, spec: str) -> bool:
    """True, wenn ``when`` (UTC) im Blackout-Fenster liegt."""
    window = parse_utc_blackout_minutes(spec)
    if window is None:
        return False
    start, end = window
    if when.tzinfo is None:
        raise ValueError("Blackout-Pruefung erfordert timezone-aware UTC-Zeit")
    minutes = when.hour * 60 + when.minute
    if start <= end:
        return start <= minutes < end
    return minutes >= start or minutes < end
