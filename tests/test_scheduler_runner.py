"""Scheduler next-run helpers."""

from __future__ import annotations

from datetime import timedelta

from app.core.time import utc_now
from app.scheduler.runner import _next_run_time


def test_next_run_time_overdue_runs_now() -> None:
    overdue = utc_now() - timedelta(days=2)
    due = _next_run_time(overdue)
    assert due <= utc_now()


def test_next_run_time_future_kept() -> None:
    future = utc_now() + timedelta(hours=12)
    due = _next_run_time(future)
    assert abs((due - future).total_seconds()) < 1


def test_next_run_time_none_is_now() -> None:
    due = _next_run_time(None)
    assert abs((due - utc_now()).total_seconds()) < 2
