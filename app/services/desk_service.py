"""Map paper ledger rows into Alpha Desk dashboard payloads.

Cancelled / retest-skipped positions are never exposed as CLOSED trades.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.enums import ExitReason, SignalDirection
from app.core.time import utc_now
from app.models.paper import PaperFill, PaperPosition
from app.repositories.paper_repository import PaperRepository
from app.schemas.desk import (
    DeskEquityPoint,
    DeskPortfolio,
    DeskSnapshot,
    DeskTakeProfit,
    DeskTrade,
)
from app.services.paper_trading_service import PaperTradingService

# Price zones only — skip ATR-multiplier notes like ``zone=0.55-1.0ATR``.
_ZONE_RE = re.compile(
    r"zone=(?P<lo>-?\d+(?:\.\d+)?)-(?P<hi>-?\d+(?:\.\d+)?)(?!ATR)"
)

_EXIT_NOTE = {
    ExitReason.STOP_LOSS.value: "Stop Loss",
    ExitReason.TAKE_PROFIT_1.value: "Take-Profit 1",
    ExitReason.TAKE_PROFIT_2.value: "Take-Profit 2",
    ExitReason.TAKE_PROFIT_3.value: "Take-Profit 3",
    ExitReason.EXPIRED.value: "Expired",
    ExitReason.EARLY_SCRATCH.value: "Early Scratch",
    ExitReason.END_OF_DATA.value: "End of data",
    ExitReason.RETEST_SKIPPED.value: "Retest skipped",
}


def desk_status_for(paper_status: str) -> str | None:
    """Return desk status, or ``None`` when the row must be omitted."""
    key = (paper_status or "").lower()
    if key == "open":
        return "OPEN"
    if key == "pending":
        return "PENDING"
    if key == "closed":
        return "CLOSED"
    # cancelled / unknown — not part of the active trade book
    return None


def _iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.replace(" ", "T")
        if text.endswith("+00"):
            text = text + ":00"
        if text.endswith("+00:00"):
            return text.replace("+00:00", "Z")
        if not text.endswith("Z") and "+" not in text[10:]:
            return text + "Z" if "T" in text else text
        return text.replace("+00:00", "Z")
    if value.tzinfo is None:
        return value.isoformat() + "Z"
    return value.astimezone().isoformat().replace("+00:00", "Z")


def _f(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    return float(value)


def _round_money(value: float) -> float:
    return round(value, 2)


def exit_fill_price(fills: list[Any] | None) -> float | None:
    """Last non-entry fill price — None when the position never exited."""
    if not fills:
        return None
    exit_fills = [
        f
        for f in fills
        if str(getattr(f, "reason", None) or (f.get("reason") if isinstance(f, dict) else "")).lower()
        != "entry"
    ]
    if not exit_fills:
        return None

    def _when(fill: Any) -> tuple:
        ts = getattr(fill, "filled_at", None)
        if ts is None and isinstance(fill, dict):
            ts = fill.get("filled_at")
        fid = getattr(fill, "id", None)
        if fid is None and isinstance(fill, dict):
            fid = fill.get("id")
        return (str(ts or ""), int(fid or 0))

    last = max(exit_fills, key=_when)
    price = getattr(last, "price", None)
    if price is None and isinstance(last, dict):
        price = last.get("price")
    return _f(price) if price is not None else None


def _parse_zone(notes: str | None) -> tuple[float | None, float | None]:
    if not notes:
        return None, None
    match = _ZONE_RE.search(notes)
    if not match:
        return None, None
    return float(match.group("lo")), float(match.group("hi"))


def _strategy_label(direction: str, status: str) -> str:
    is_long = SignalDirection(direction).is_long if direction else True
    if status == "pending":
        return "Retest Entry"
    return "Uptrend Continuation" if is_long else "Downtrend Continuation"


def _notes_for(position: PaperPosition | dict[str, Any], desk_status: str) -> str:
    if isinstance(position, dict):
        raw_notes = position.get("notes")
        exit_reason = position.get("exit_reason")
        timeframe = position.get("timeframe") or "1h"
    else:
        raw_notes = position.notes
        exit_reason = position.exit_reason
        timeframe = position.timeframe or "1h"

    if desk_status == "PENDING":
        return f"Waiting for retest · TF {timeframe}"
    if desk_status == "OPEN":
        if raw_notes and "retest_filled" in str(raw_notes):
            return f"Retest filled · TF {timeframe}"
        return f"Open · TF {timeframe}"
    label = _EXIT_NOTE.get(str(exit_reason or ""), str(exit_reason or "Closed"))
    return f"{label} · TF {timeframe}"


def _take_profits(
    position: PaperPosition | dict[str, Any],
    *,
    exit_price: float | None,
    side: str,
    scale_out: tuple[float, float, float] | None = None,
) -> list[DeskTakeProfit]:
    if isinstance(position, dict):
        tps = [
            ("TP1", position.get("take_profit_1"), bool(position.get("tp1_filled"))),
            ("TP2", position.get("take_profit_2"), bool(position.get("tp2_filled"))),
            ("TP3", position.get("take_profit_3"), bool(position.get("tp3_filled"))),
        ]
    else:
        tps = [
            ("TP1", position.take_profit_1, position.tp1_filled),
            ("TP2", position.take_profit_2, position.tp2_filled),
            ("TP3", position.take_profit_3, position.tp3_filled),
        ]
    fractions = scale_out or get_settings().parsed_scale_out_fractions
    out: list[DeskTakeProfit] = []
    for index, (label, price, filled) in enumerate(tps):
        if price is None:
            continue
        px = _f(price)
        hit = filled
        if not hit and exit_price is not None:
            if side == "LONG":
                hit = exit_price >= px
            else:
                hit = exit_price <= px
        size = fractions[index] if index < len(fractions) else fractions[-1]
        out.append(DeskTakeProfit(label=label, price=px, size=size, hit=hit))
    return out


def map_position_to_desk_trade(
    position: PaperPosition | dict[str, Any],
    *,
    fills: list[Any] | None = None,
    mark: float | None = None,
    scale_out: tuple[float, float, float] | None = None,
) -> DeskTrade | None:
    """Map one paper position. Returns ``None`` for cancelled / non-book rows."""
    if isinstance(position, dict):
        status = str(position.get("status") or "")
        direction = str(position.get("direction") or "long")
        pos_id = position.get("id")
        symbol = str(position.get("symbol") or "")
        entry = _f(position.get("entry_price"))
        stop = _f(position.get("current_stop") or position.get("stop_loss"))
        realized = _f(position.get("realized_pnl"))
        risk = _f(position.get("risk_amount"))
        margin = _f(position.get("margin_used"))
        score = _f(position.get("signal_score"), 0.0)
        leverage = _f(position.get("leverage"), 1.0)
        fees = _f(position.get("fees"))
        qty = _f(position.get("initial_quantity") or position.get("remaining_quantity"))
        opened_at = position.get("opened_at")
        closed_at = position.get("closed_at")
        notes = position.get("notes")
        fill_list = fills
    else:
        status = position.status
        direction = position.direction
        pos_id = position.id
        symbol = position.symbol
        entry = _f(position.entry_price)
        stop = _f(position.current_stop or position.stop_loss)
        realized = _f(position.realized_pnl)
        risk = _f(position.risk_amount)
        margin = _f(position.margin_used)
        score = _f(position.signal_score, 0.0)
        leverage = _f(position.leverage, 1.0)
        fees = _f(position.fees)
        qty = _f(position.initial_quantity or position.remaining_quantity)
        opened_at = position.opened_at
        closed_at = position.closed_at
        notes = position.notes
        fill_list = list(position.fills) if fills is None else fills

    desk_status = desk_status_for(status)
    if desk_status is None:
        return None

    side = "LONG" if SignalDirection(direction).is_long else "SHORT"
    exit_px = exit_fill_price(fill_list) if desk_status == "CLOSED" else None
    # Guard: a CLOSED desk row without an exit fill is a mapping bug — drop it.
    if desk_status == "CLOSED" and exit_px is None:
        return None

    r_mult: float | None = None
    if desk_status == "CLOSED" and risk > 0:
        r_mult = round(realized / risk, 2)

    upnl: float | None = None
    mark_out = mark
    if desk_status == "OPEN" and mark is not None:
        direction_sign = 1.0 if side == "LONG" else -1.0
        rem = _f(
            position.remaining_quantity
            if not isinstance(position, dict)
            else position.get("remaining_quantity")
        )
        upnl = _round_money((mark - entry) * rem * direction_sign)
    elif desk_status == "PENDING":
        mark_out = mark if mark is not None else entry
    elif desk_status == "CLOSED":
        mark_out = None

    zone_lo, zone_hi = _parse_zone(str(notes) if notes else None)
    opened = _iso(opened_at) or utc_now().isoformat().replace("+00:00", "Z")

    return DeskTrade(
        id=str(pos_id),
        symbol=symbol.upper(),
        side=side,
        entry=entry,
        mark=mark_out,
        exit=exit_px,
        stop=stop,
        upnl=upnl,
        realized=_round_money(realized) if desk_status == "CLOSED" else None,
        r=r_mult,
        margin=_round_money(margin) if desk_status != "CLOSED" else 0.0,
        score=round(score, 1),
        status=desk_status,
        openedAt=opened,
        closedAt=_iso(closed_at) if desk_status == "CLOSED" else None,
        entryZoneLow=zone_lo if desk_status == "PENDING" else None,
        entryZoneHigh=zone_hi if desk_status == "PENDING" else None,
        strategy=_strategy_label(direction, status.lower()),
        takeProfits=_take_profits(
            position, exit_price=exit_px, side=side, scale_out=scale_out
        ),
        positionSize=qty if qty else None,
        leverage=leverage,
        fees=_round_money(fees),
        notes=_notes_for(position, desk_status),
    )


def build_equity_curve(
    *,
    initial_balance: float,
    fills: list[PaperFill] | list[dict[str, Any]],
    live_equity: float | None = None,
    start_at: datetime | None = None,
    as_of: datetime | None = None,
) -> list[DeskEquityPoint]:
    """Fill-level equity curve ending at live mark-to-market equity."""
    from app.charts.paper_equity_chart import build_equity_curve_points

    now = as_of or utc_now()
    fill_rows: list[tuple[datetime, float, float]] = []
    for fill in fills:
        if isinstance(fill, dict):
            ts = fill.get("filled_at")
            if ts is None:
                continue
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            fill_rows.append((ts, _f(fill.get("pnl")), _f(fill.get("fee"))))
        else:
            fill_rows.append((fill.filled_at, _f(fill.pnl), _f(fill.fee)))

    curve_start = start_at or now
    if fill_rows and fill_rows[0][0] < curve_start:
        curve_start = fill_rows[0][0]
    live = float(live_equity) if live_equity is not None else float(initial_balance)
    points = build_equity_curve_points(
        initial=float(initial_balance),
        start_at=curve_start,
        fills=fill_rows,
        as_of=now,
        live_equity=live,
    )
    return [
        DeskEquityPoint(t=_iso(at) or now.isoformat(), equity=_round_money(eq))
        for at, eq in points
    ]


class DeskService:
    """Build public desk snapshots from the paper ledger."""

    def __init__(self, paper: PaperTradingService) -> None:
        self._paper = paper

    async def snapshot(
        self,
        session: AsyncSession,
        *,
        prices: dict[str, float] | None = None,
    ) -> DeskSnapshot:
        account = await self._paper.get_or_create_account(session)
        repo = PaperRepository(session)
        positions = await repo.list_positions(account.id)
        fills = await repo.list_fills_for_account(account.id)
        summary = await self._paper.summary(session, prices=prices)

        marks = prices or {}
        trades: list[DeskTrade] = []
        open_upnl = 0.0
        open_r = 0.0
        for pos in positions:
            trade = map_position_to_desk_trade(
                pos,
                mark=marks.get(pos.symbol.upper()),
            )
            if trade is None:
                continue
            trades.append(trade)
            if trade.status == "OPEN" and trade.upnl is not None:
                open_upnl += trade.upnl
            if trade.status == "OPEN" and trade.r is not None:
                open_r += trade.r
            elif trade.status == "OPEN" and float(pos.risk_amount) > 0 and trade.upnl is not None:
                open_r += trade.upnl / float(pos.risk_amount)

        trades.sort(key=lambda t: t.openedAt, reverse=True)

        initial = float(account.initial_balance)
        equity = float(summary.equity)
        portfolio = DeskPortfolio(
            totalCapital=_round_money(initial),
            equity=_round_money(equity),
            cash=_round_money(float(account.cash_balance)),
            marginLocked=_round_money(float(summary.open_margin)),
            realizedPnl=_round_money(float(account.realized_pnl)),
            openUpnl=_round_money(open_upnl),
            openR=round(open_r, 2),
            totalReturnPct=round(((equity - initial) / initial) * 100.0, 2) if initial else 0.0,
            openPositions=int(summary.open_positions),
            pendingOrders=int(summary.pending_positions),
            closedTrades=int(summary.closed_trades),
            equityChangePct=0.0,
            realizedChangePct=0.0,
        )
        start_at = getattr(account, "created_at", None) or utc_now()
        return DeskSnapshot(
            portfolio=portfolio,
            trades=trades,
            equity=build_equity_curve(
                initial_balance=initial,
                fills=fills,
                live_equity=equity,
                start_at=start_at,
            ),
            generatedAt=_iso(utc_now()) or utc_now().isoformat(),
        )


def map_raw_export_to_snapshot(payload: dict[str, Any]) -> DeskSnapshot:
    """Offline helper: SQL JSON export → desk snapshot (no DB session)."""
    account = payload.get("account") or {}
    positions = payload.get("positions") or []
    fills = payload.get("fills") or []
    fills_by_pos: dict[int, list[dict[str, Any]]] = {}
    for fill in fills:
        fills_by_pos.setdefault(int(fill["position_id"]), []).append(fill)

    trades: list[DeskTrade] = []
    open_n = pending_n = closed_n = 0
    open_margin = 0.0
    for pos in positions:
        status = str(pos.get("status") or "").lower()
        trade = map_position_to_desk_trade(
            pos,
            fills=fills_by_pos.get(int(pos["id"]), []),
        )
        if trade is None:
            continue
        trades.append(trade)
        if trade.status == "OPEN":
            open_n += 1
            open_margin += _f(pos.get("margin_used"))
        elif trade.status == "PENDING":
            pending_n += 1
        elif trade.status == "CLOSED":
            closed_n += 1

    trades.sort(key=lambda t: t.openedAt, reverse=True)
    initial = _f(account.get("initial_balance"), 5000.0)
    cash = _f(account.get("cash_balance"), initial)
    realized = _f(account.get("realized_pnl"))
    equity = cash + open_margin
    portfolio = DeskPortfolio(
        totalCapital=_round_money(initial),
        equity=_round_money(equity),
        cash=_round_money(cash),
        marginLocked=_round_money(open_margin),
        realizedPnl=_round_money(realized),
        openUpnl=0.0,
        openR=0.0,
        totalReturnPct=round(((equity - initial) / initial) * 100.0, 2) if initial else 0.0,
        openPositions=open_n,
        pendingOrders=pending_n,
        closedTrades=closed_n,
        equityChangePct=0.0,
        realizedChangePct=0.0,
    )
    return DeskSnapshot(
        portfolio=portfolio,
        trades=trades,
        equity=build_equity_curve(
            initial_balance=initial,
            fills=fills,
            live_equity=equity,
        ),
        generatedAt=_iso(utc_now()) or utc_now().isoformat(),
    )
