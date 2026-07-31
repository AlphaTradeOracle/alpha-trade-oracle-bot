"""Tests fuer Paper-Equity-Kurve und Chart."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.charts.paper_equity_chart import (
    build_equity_curve_points,
    build_paper_equity_chart,
)


def test_equity_curve_tracks_fills_and_live_mtm() -> None:
    start = datetime(2026, 7, 31, 10, 0, tzinfo=UTC)
    t1 = start + timedelta(hours=1)
    t2 = start + timedelta(hours=2)
    as_of = start + timedelta(hours=3)
    points = build_equity_curve_points(
        initial=5000.0,
        start_at=start,
        fills=[
            (t1, 0.0, 1.5),  # entry fee
            (t2, 40.0, 1.2),  # close with pnl
        ],
        as_of=as_of,
        live_equity=5050.0,
    )
    assert points[0] == (start, 5000.0)
    assert points[1] == (t1, 4998.5)
    assert points[2] == (t2, 5037.3)
    assert points[-1] == (as_of, 5050.0)


def test_equity_chart_renders_png() -> None:
    from app.charts.theme import _LOGO_PATH

    assert _LOGO_PATH.is_file()
    start = datetime(2026, 7, 31, 10, 0, tzinfo=UTC)
    points = [
        (start, 5000.0),
        (start + timedelta(hours=2), 4980.0),
        (start + timedelta(hours=5), 5125.5),
    ]
    png = build_paper_equity_chart(points, initial=5000.0)
    assert png is not None
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_equity_chart_renders_with_window_stats() -> None:
    start = datetime(2026, 7, 31, 10, 0, tzinfo=UTC)
    points = [
        (start, 5000.0),
        (start + timedelta(hours=2), 5012.5),
        (start + timedelta(hours=5), 5142.3),
    ]
    png = build_paper_equity_chart(
        points,
        initial=5000.0,
        windows=[("1h", 12.5), ("24h", 40.0), ("7d", 142.3)],
    )
    assert png is not None
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
