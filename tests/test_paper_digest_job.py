"""Tests fuer den stuendlichen Paper-Digest-Scheduler-Job."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.scheduler.jobs import paper_digest_job, run_paper_digest
from app.services.paper_trading_service import (
    PaperDigestSnapshot,
    PaperSummary,
)


def test_paper_digest_job_definition() -> None:
    definition = paper_digest_job(60)
    assert definition.key == "paper_digest:60m"
    assert definition.job_type == "paper_digest"
    assert definition.interval_seconds == 3600


@pytest.mark.asyncio
async def test_run_paper_digest_skips_when_claim_refused() -> None:
    paper = AsyncMock()
    provider = AsyncMock()
    notifier = AsyncMock()

    claim_repo = AsyncMock()
    claim_repo.claim = AsyncMock(return_value=False)

    with (
        patch("app.scheduler.jobs.session_scope") as scope_mock,
        patch("app.scheduler.jobs.ScheduledJobRepository", return_value=claim_repo),
    ):
        session = MagicMock()
        scope_mock.return_value.__aenter__ = AsyncMock(return_value=session)
        scope_mock.return_value.__aexit__ = AsyncMock(return_value=None)

        await run_paper_digest(paper, provider, notifier, "paper_digest:60m")

    claim_repo.claim.assert_awaited_once_with("paper_digest:60m")
    paper.build_digest.assert_not_awaited()
    notifier.notify_paper_digest.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_paper_digest_sends_when_claimed() -> None:
    snapshot = PaperDigestSnapshot(
        as_of=datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
        summary=PaperSummary(
            cash_balance=4500.0,
            initial_balance=5000.0,
            realized_pnl=0.0,
            open_positions=0,
            open_margin=0.0,
            equity=5000.0,
            win_rate=0.0,
            closed_trades=0,
            profit_factor=0.0,
            pending_positions=0,
        ),
        equity_return_pct=0.0,
        hour_closed_count=0,
        hour_closed_r=0.0,
        hour_closed_pnl=0.0,
        hour_opened_count=0,
        open_rows=[],
        hour_closes=[],
        total_open_upnl_usd=0.0,
        total_open_upnl_r=0.0,
        risk_per_trade=50.0,
        leverage=10.0,
        max_notional=1500.0,
        max_open=20,
    )

    paper = AsyncMock()
    paper.get_or_create_account = AsyncMock(return_value=MagicMock(id=1))
    paper.build_digest = AsyncMock(return_value=snapshot)
    provider = AsyncMock()
    notifier = AsyncMock()
    notifier.notify_paper_digest = AsyncMock(return_value=2)

    claim_repo = AsyncMock()
    claim_repo.claim = AsyncMock(return_value=True)
    claim_repo.complete = AsyncMock()
    paper_repo = AsyncMock()
    paper_repo.list_open_positions = AsyncMock(return_value=[])

    with (
        patch("app.scheduler.jobs.session_scope") as scope_mock,
        patch("app.scheduler.jobs.ScheduledJobRepository", return_value=claim_repo),
        patch("app.scheduler.jobs.PaperRepository", return_value=paper_repo),
        patch("app.scheduler.jobs.EventRepository"),
    ):
        session = MagicMock()
        scope_mock.return_value.__aenter__ = AsyncMock(return_value=session)
        scope_mock.return_value.__aexit__ = AsyncMock(return_value=None)

        await run_paper_digest(paper, provider, notifier, "paper_digest:60m")

    paper.build_digest.assert_awaited()
    notifier.notify_paper_digest.assert_awaited_once_with(snapshot)
    claim_repo.complete.assert_awaited()
