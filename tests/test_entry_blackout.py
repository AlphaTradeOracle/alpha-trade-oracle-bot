"""Tests fuer UTC-Entry-Blackouts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.entry_blackout import is_in_utc_blackout, parse_utc_blackout_minutes


class TestEntryBlackout:
    def test_parses_window(self) -> None:
        assert parse_utc_blackout_minutes("21:00-01:00") == (21 * 60, 60)

    def test_empty_spec_is_disabled(self) -> None:
        assert parse_utc_blackout_minutes("") is None
        assert not is_in_utc_blackout(datetime(2026, 7, 31, 22, 0, tzinfo=UTC), "")

    def test_midnight_wrap(self) -> None:
        spec = "21:00-01:00"
        assert is_in_utc_blackout(datetime(2026, 7, 31, 22, 0, tzinfo=UTC), spec)
        assert is_in_utc_blackout(datetime(2026, 7, 31, 0, 30, tzinfo=UTC), spec)
        assert not is_in_utc_blackout(datetime(2026, 7, 31, 12, 0, tzinfo=UTC), spec)

    def test_requires_timezone_aware(self) -> None:
        with pytest.raises(ValueError):
            is_in_utc_blackout(datetime(2026, 7, 31, 22, 0), "21:00-01:00")
