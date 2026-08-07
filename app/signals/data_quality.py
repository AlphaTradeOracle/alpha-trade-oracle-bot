"""Datenqualitaet fuer Multi-Timeframe-Analysen."""

from __future__ import annotations

#: Hoehere Timeframes fuer die Mindest-Abdeckung (Setup-TF + mindestens einer davon).
HIGHER_TIMEFRAMES = frozenset({"4h", "1d", "1w"})

_TIMEFRAME_ORDER = {"15m": 0, "1h": 1, "4h": 2, "1d": 3, "1w": 4}


def _timeframe_rank(timeframe: str) -> int:
    return _TIMEFRAME_ORDER.get(timeframe, -1)


def has_required_timeframe_coverage(
    indicator_sets: dict[str, object],
    *,
    primary_timeframe: str,
) -> bool:
    """Mindestens Setup-TF und ein hoeherer TF mit genug Kerzen."""
    if primary_timeframe not in indicator_sets:
        return False
    primary_rank = _timeframe_rank(primary_timeframe)
    for timeframe in indicator_sets:
        if _timeframe_rank(timeframe) > primary_rank:
            return True
    return False


def compute_analysis_data_quality(
    qualities: list[float],
    *,
    indicator_sets: dict[str, object],
    primary_timeframe: str,
) -> float:
    """Mittel der verfuegbaren TF-Qualitaeten ohne Strafe fuer fehlende TFs.

    Fehlende Timeframes (z. B. bei jungen Listings ohne 1d-Historie) senken
    die Qualitaet nicht proportional zur Anzahl angefragter TFs. Ohne Setup-TF
    bleibt die Qualitaet 0. Fehlender hoeherer TF ist kein Hard-Fail mehr —
    frueher wurde die Qualitaet kuenstlich auf 59.99 gedrueckt und erzeugte
    widerspruechliche NO_TRADEs („Data quality 60 is below the minimum of 60“).
    """
    if not qualities or not indicator_sets:
        return 0.0
    if primary_timeframe not in indicator_sets:
        return 0.0
    return round(sum(qualities) / len(qualities), 2)
