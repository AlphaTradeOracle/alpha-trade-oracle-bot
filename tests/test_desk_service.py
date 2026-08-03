"""Unit tests for Alpha Desk paper → dashboard mapping."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.services.desk_service import (
    _parse_zone,
    desk_status_for,
    exit_fill_price,
    map_position_to_desk_trade,
    map_raw_export_to_snapshot,
)


def test_parse_zone_rejects_atr_multipliers() -> None:
    assert _parse_zone("retest_pending;zone=0.55-1.0ATR;ref_entry=1.2") == (None, None)
    assert _parse_zone("retest_filled;zone=99.0-101.0;atr=1.2") == (99.0, 101.0)


def test_pending_atr_multiplier_notes_do_not_fake_entry_zone() -> None:
    trade = map_position_to_desk_trade(
        {
            "id": 42,
            "symbol": "KASUSDT",
            "direction": "SHORT",
            "status": "pending",
            "entry_price": 0.0265,
            "stop_loss": 0.026724,
            "current_stop": 0.026724,
            "take_profit_1": 0.025,
            "take_profit_2": 0.024,
            "take_profit_3": 0.023,
            "initial_quantity": 1.0,
            "remaining_quantity": 1.0,
            "margin_used": 0.0,
            "notional": 100.0,
            "realized_pnl": 0.0,
            "fees": 0.0,
            "risk_amount": 50.0,
            "signal_score": 28.5,
            "leverage": 5.0,
            "timeframe": "1h",
            "opened_at": "2026-08-03T08:00:00+00:00",
            "notes": (
                "retest_pending;ref_entry=0.0265;orig_sl=0.026724;"
                "zone=0.55-1.0ATR"
            ),
        }
    )
    assert trade is not None
    assert trade.status == "PENDING"
    # Must not surface 0.55–1.0 as a price zone.
    assert trade.entryZoneLow is None
    assert trade.entryZoneHigh is None


def test_pending_zone_from_price_notes() -> None:
    trade = map_position_to_desk_trade(
        {
            "id": 43,
            "symbol": "KASUSDT",
            "direction": "SHORT",
            "status": "pending",
            "entry_price": 0.0265,
            "stop_loss": 0.026724,
            "current_stop": 0.026724,
            "take_profit_1": 0.025,
            "take_profit_2": 0.024,
            "take_profit_3": 0.023,
            "initial_quantity": 1.0,
            "remaining_quantity": 1.0,
            "margin_used": 0.0,
            "notional": 100.0,
            "realized_pnl": 0.0,
            "fees": 0.0,
            "risk_amount": 50.0,
            "signal_score": 28.5,
            "leverage": 5.0,
            "timeframe": "1h",
            "opened_at": "2026-08-03T08:00:00+00:00",
            "notes": (
                "retest_pending;ref_entry=0.0265;orig_sl=0.026724;"
                "zone=0.0268-0.0272;zone_atr=0.55-1.0;atr=0.0004"
            ),
        }
    )
    assert trade is not None
    assert trade.entryZoneLow == pytest.approx(0.0268)
    assert trade.entryZoneHigh == pytest.approx(0.0272)


def test_desk_status_omits_cancelled() -> None:
    assert desk_status_for("open") == "OPEN"
    assert desk_status_for("pending") == "PENDING"
    assert desk_status_for("closed") == "CLOSED"
    assert desk_status_for("cancelled") is None
    assert desk_status_for("retest_skipped") is None


def test_cancelled_position_not_mapped() -> None:
    trade = map_position_to_desk_trade(
        {
            "id": 1,
            "symbol": "BTCUSDT",
            "direction": "SHORT",
            "status": "cancelled",
            "entry_price": 100.0,
            "stop_loss": 105.0,
            "current_stop": 105.0,
            "take_profit_1": 95.0,
            "take_profit_2": 90.0,
            "take_profit_3": 85.0,
            "initial_quantity": 1.0,
            "remaining_quantity": 0.0,
            "margin_used": 0.0,
            "realized_pnl": 0.0,
            "fees": 0.0,
            "risk_amount": 50.0,
            "signal_score": 20.0,
            "leverage": 10.0,
            "exit_reason": "retest_skipped",
            "timeframe": "1h",
            "opened_at": "2026-08-01T12:00:00+00:00",
            "closed_at": "2026-08-01T18:00:00+00:00",
            "notes": "retest_skipped;note=pending_expired",
            "tp1_filled": False,
            "tp2_filled": False,
            "tp3_filled": False,
        }
    )
    assert trade is None


def test_closed_without_exit_fill_dropped() -> None:
    trade = map_position_to_desk_trade(
        {
            "id": 2,
            "symbol": "ETHUSDT",
            "direction": "LONG",
            "status": "closed",
            "entry_price": 3000.0,
            "stop_loss": 2900.0,
            "current_stop": 2900.0,
            "take_profit_1": 3100.0,
            "take_profit_2": 3200.0,
            "take_profit_3": 3300.0,
            "initial_quantity": 1.0,
            "remaining_quantity": 0.0,
            "margin_used": 0.0,
            "realized_pnl": 0.0,
            "fees": 0.0,
            "risk_amount": 50.0,
            "signal_score": 22.0,
            "leverage": 5.0,
            "exit_reason": "stop_loss",
            "timeframe": "1h",
            "opened_at": "2026-08-01T12:00:00+00:00",
            "closed_at": "2026-08-01T13:00:00+00:00",
            "notes": None,
            "tp1_filled": False,
            "tp2_filled": False,
            "tp3_filled": False,
        },
        fills=[{"id": 1, "reason": "entry", "price": 3000.0, "filled_at": "2026-08-01T12:00:00+00:00"}],
    )
    assert trade is None


def test_closed_with_exit_fill_mapped() -> None:
    trade = map_position_to_desk_trade(
        {
            "id": 3,
            "symbol": "SOLUSDT",
            "direction": "SHORT",
            "status": "closed",
            "entry_price": 100.0,
            "stop_loss": 110.0,
            "current_stop": 110.0,
            "take_profit_1": 90.0,
            "take_profit_2": 80.0,
            "take_profit_3": 70.0,
            "initial_quantity": 1.0,
            "remaining_quantity": 0.0,
            "margin_used": 0.0,
            "notional": 1000.0,
            "realized_pnl": -10.5,
            "fees": 1.2,
            "risk_amount": 10.0,
            "signal_score": 24.5,
            "leverage": 10.0,
            "exit_reason": "stop_loss",
            "timeframe": "1h",
            "opened_at": "2026-08-01T12:00:00+00:00",
            "closed_at": "2026-08-01T13:00:00+00:00",
            "notes": "retest_filled;zone=99.0-101.0",
            "tp1_filled": False,
            "tp2_filled": False,
            "tp3_filled": False,
        },
        fills=[
            {"id": 1, "reason": "entry", "price": 100.0, "filled_at": "2026-08-01T12:00:00+00:00"},
            {
                "id": 2,
                "reason": "stop_loss",
                "price": 110.0,
                "filled_at": "2026-08-01T13:00:00+00:00",
            },
        ],
    )
    assert trade is not None
    assert trade.status == "CLOSED"
    assert trade.exit == 110.0
    assert trade.realized == -10.5
    assert trade.r == -1.05
    assert trade.margin == 100.0
    assert trade.notional == 1000.0
    assert trade.stop == 110.0
    assert [tp.size for tp in trade.takeProfits] == [0.5, 0.25, 0.25]


def test_open_after_scale_out_uses_original_stop_and_remaining_size() -> None:
    trade = map_position_to_desk_trade(
        {
            "id": 9,
            "symbol": "KASUSDT",
            "direction": "SHORT",
            "status": "open",
            "entry_price": 0.02730527,
            "stop_loss": 0.02755,
            "current_stop": 0.02730527,
            "take_profit_1": 0.026,
            "take_profit_2": 0.025,
            "take_profit_3": 0.024,
            "initial_quantity": 91557.4288,
            "remaining_quantity": 22889.3572,
            "margin_used": 62.5,
            "notional": 2500.0,
            "realized_pnl": 28.6,
            "fees": 1.5,
            "risk_amount": 250.0,
            "signal_score": 23.7,
            "leverage": 10.0,
            "exit_reason": None,
            "timeframe": "1h",
            "opened_at": "2026-08-01T12:00:00+00:00",
            "closed_at": None,
            "notes": "retest_filled",
            "tp1_filled": True,
            "tp2_filled": True,
            "tp3_filled": False,
        },
        mark=0.0268,
    )
    assert trade is not None
    assert trade.status == "OPEN"
    assert trade.stop == 0.02755
    assert trade.currentStop == 0.02730527
    assert trade.positionSize == pytest.approx(22889.3572)
    assert trade.notional == pytest.approx(625.0)
    assert trade.initialNotional == pytest.approx(2500.0)
    assert trade.realized == pytest.approx(28.6)
    assert trade.r is not None


def test_exit_fill_price_picks_last_non_entry() -> None:
    assert (
        exit_fill_price(
            [
                {"id": 1, "reason": "entry", "price": 1.0, "filled_at": "2026-08-01T10:00:00+00:00"},
                {
                    "id": 2,
                    "reason": "take_profit_1",
                    "price": 1.1,
                    "filled_at": "2026-08-01T11:00:00+00:00",
                },
                {
                    "id": 3,
                    "reason": "stop_loss",
                    "price": 0.95,
                    "filled_at": "2026-08-01T12:00:00+00:00",
                },
            ]
        )
        == 0.95
    )


def test_raw_export_counts_exclude_cancelled() -> None:
    snap = map_raw_export_to_snapshot(
        {
            "account": {
                "initial_balance": 5000,
                "cash_balance": 4900,
                "realized_pnl": -100,
            },
            "positions": [
                {
                    "id": 1,
                    "symbol": "AAAUSDT",
                    "direction": "SHORT",
                    "status": "cancelled",
                    "entry_price": 1.0,
                    "stop_loss": 1.1,
                    "current_stop": 1.1,
                    "take_profit_1": 0.9,
                    "take_profit_2": 0.8,
                    "take_profit_3": 0.7,
                    "initial_quantity": 1.0,
                    "remaining_quantity": 0.0,
                    "margin_used": 0.0,
                    "realized_pnl": 0.0,
                    "fees": 0.0,
                    "risk_amount": 10.0,
                    "signal_score": 20.0,
                    "leverage": 10.0,
                    "exit_reason": "retest_skipped",
                    "timeframe": "1h",
                    "opened_at": datetime(2026, 8, 1, 12, tzinfo=UTC).isoformat(),
                    "closed_at": datetime(2026, 8, 1, 18, tzinfo=UTC).isoformat(),
                    "notes": "retest_skipped",
                    "tp1_filled": False,
                    "tp2_filled": False,
                    "tp3_filled": False,
                },
                {
                    "id": 2,
                    "symbol": "BBBUSDT",
                    "direction": "LONG",
                    "status": "open",
                    "entry_price": 10.0,
                    "stop_loss": 9.0,
                    "current_stop": 9.0,
                    "take_profit_1": 11.0,
                    "take_profit_2": 12.0,
                    "take_profit_3": 13.0,
                    "initial_quantity": 1.0,
                    "remaining_quantity": 1.0,
                    "margin_used": 150.0,
                    "realized_pnl": 0.0,
                    "fees": 0.75,
                    "risk_amount": 10.0,
                    "signal_score": 21.0,
                    "leverage": 10.0,
                    "exit_reason": None,
                    "timeframe": "1h",
                    "opened_at": datetime(2026, 8, 1, 14, tzinfo=UTC).isoformat(),
                    "closed_at": None,
                    "notes": "retest_filled;zone=9.9-10.1",
                    "tp1_filled": False,
                    "tp2_filled": False,
                    "tp3_filled": False,
                },
            ],
            "fills": [
                {
                    "id": 1,
                    "position_id": 2,
                    "reason": "entry",
                    "price": 10.0,
                    "fee": 0.75,
                    "pnl": 0.0,
                    "filled_at": datetime(2026, 8, 1, 14, tzinfo=UTC).isoformat(),
                }
            ],
        }
    )
    assert snap.portfolio.closedTrades == 0
    assert snap.portfolio.openPositions == 1
    assert len(snap.trades) == 1
    assert snap.trades[0].status == "OPEN"
