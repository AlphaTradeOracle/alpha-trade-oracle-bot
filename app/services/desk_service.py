"""Map paper ledger rows into Alpha Desk dashboard payloads.

Cancelled / retest-skipped positions are never exposed as CLOSED trades.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.enums import ExitReason, SignalDirection
from app.core.time import utc_now
from app.models.paper import PaperFill, PaperPosition
from app.repositories.paper_repository import PaperRepository
from app.schemas.desk import (
    DeskEquityPoint,
    DeskMarketRegime,
    DeskPortfolio,
    DeskSnapshot,
    DeskTakeProfit,
    DeskTrade,
)
from app.services.paper_trading_service import PaperTradingService

# Price zones in notes: ``zone=99.0-101.0``. Reject ATR-multiplier form
# ``zone=0.55-1.0ATR`` (the old ``(?!ATR)`` lookahead failed via backtracking
# on ``1.0ATR`` → matched hi=``1``).
_ZONE_PX_RE = re.compile(
    r"(?:^|;)zone=(?P<lo>-?\d+(?:\.\d+)?)-(?P<hi>-?\d+(?:\.\d+)?)(?!ATR)(?:;|$)"
)
_ZONE_ATR_RE = re.compile(r"(?:^|;)zone=-?\d+(?:\.\d+)?--?\d+(?:\.\d+)?ATR(?:;|$)")
_REF_ENTRY_RE = re.compile(r"(?:^|;)ref_entry=(?P<v>-?\d+(?:\.\d+)?)(?:;|$)")
_ATR_RE = re.compile(r"(?:^|;)atr=(?P<v>-?\d+(?:\.\d+)?)(?:;|$)")
_ZONE_ATR_MULT_RE = re.compile(
    r"(?:^|;)zone_atr=(?P<near>-?\d+(?:\.\d+)?)-(?P<far>-?\d+(?:\.\d+)?)(?:;|$)"
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
    """Return price zone bounds; ignore ATR-multiplier ``zone=0.55-1.0ATR``."""
    if not notes:
        return None, None
    if _ZONE_ATR_RE.search(notes):
        return None, None
    match = _ZONE_PX_RE.search(notes)
    if not match:
        return None, None
    return float(match.group("lo")), float(match.group("hi"))


def _pending_retest_zone(
    notes: str | None,
    *,
    entry: float,
    direction: str,
) -> tuple[float | None, float | None]:
    """Price zone for pending retest rows (notes or reconstructed from ATR)."""
    lo, hi = _parse_zone(notes)
    if lo is not None and hi is not None:
        return lo, hi
    if not notes:
        return None, None

    ref_m = _REF_ENTRY_RE.search(notes)
    atr_m = _ATR_RE.search(notes)
    ref = float(ref_m.group("v")) if ref_m else entry
    atr = float(atr_m.group("v")) if atr_m else None
    if atr is None or atr <= 0:
        return None, None

    near, far = 0.55, 1.0
    mult = _ZONE_ATR_MULT_RE.search(notes)
    if mult:
        near, far = float(mult.group("near")), float(mult.group("far"))
    else:
        settings = get_settings()
        near = float(settings.paper_retest_zone_near)
        far = float(settings.paper_retest_zone_far)

    try:
        is_long = SignalDirection(direction).is_long
    except ValueError:
        is_long = True
    if is_long:
        return ref - far * atr, ref - near * atr
    return ref + near * atr, ref + far * atr


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
        return f"Open · TF {timeframe}"
    notes = str(raw_notes or "")
    if "broke_falling_resistance" in notes:
        return f"blocked: broke falling resistance · TF {timeframe}"
    if "broke_rising_support" in notes:
        return f"blocked: broke rising support · TF {timeframe}"
    if "too_close_falling_resistance" in notes:
        return f"blocked: too close to falling resistance · TF {timeframe}"
    if "too_close_rising_support" in notes:
        return f"blocked: too close to rising support · TF {timeframe}"
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
        orig_stop = _f(position.get("stop_loss"))
        current_stop = _f(position.get("current_stop") or position.get("stop_loss"))
        realized = _f(position.get("realized_pnl"))
        risk = _f(position.get("risk_amount"))
        margin = _f(position.get("margin_used"))
        notional = _f(position.get("notional"))
        score = _f(position.get("signal_score"), 0.0)
        leverage = _f(position.get("leverage"), 1.0)
        fees = _f(position.get("fees"))
        initial_qty = _f(position.get("initial_quantity") or position.get("remaining_quantity"))
        remaining_qty = _f(position.get("remaining_quantity") or initial_qty)
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
        orig_stop = _f(position.stop_loss)
        current_stop = _f(position.current_stop or position.stop_loss)
        realized = _f(position.realized_pnl)
        risk = _f(position.risk_amount)
        margin = _f(position.margin_used)
        notional = _f(position.notional)
        score = _f(position.signal_score, 0.0)
        leverage = _f(position.leverage, 1.0)
        fees = _f(position.fees)
        initial_qty = _f(position.initial_quantity or position.remaining_quantity)
        remaining_qty = _f(position.remaining_quantity or initial_qty)
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

    if notional <= 0 and initial_qty > 0 and entry > 0:
        notional = initial_qty * entry
    initial_notional = notional
    lev = leverage if leverage > 0 else 1.0
    initial_margin = initial_notional / lev if initial_notional > 0 else 0.0
    # Closed rows zero ``margin_used`` in the ledger — restore entry margin for desk UI.
    margin_out = margin if margin > 0 else initial_margin

    # Risk/Unit always uses original SL; currentStop may be fee-aware BE after TP1.
    stop_out = orig_stop if orig_stop > 0 else current_stop

    display_qty = remaining_qty if desk_status == "OPEN" else initial_qty
    if desk_status == "OPEN" and initial_qty > 0 and remaining_qty >= 0:
        share = min(1.0, remaining_qty / initial_qty)
        display_notional = initial_notional * share
    else:
        display_notional = initial_notional

    r_mult: float | None = None
    if desk_status == "CLOSED" and risk > 0:
        r_mult = round(realized / risk, 2)
    elif desk_status == "OPEN" and risk > 0 and initial_qty > 0 and remaining_qty >= 0:
        risk_remaining = risk * min(1.0, remaining_qty / initial_qty)
        if risk_remaining > 0:
            # Open R later filled from uPnL in snapshot; store None here.
            r_mult = None

    upnl: float | None = None
    mark_out = mark
    if desk_status == "OPEN" and mark is not None:
        direction_sign = 1.0 if side == "LONG" else -1.0
        upnl = _round_money((mark - entry) * remaining_qty * direction_sign)
        if risk > 0 and initial_qty > 0:
            risk_remaining = risk * min(1.0, remaining_qty / initial_qty)
            if risk_remaining > 0:
                r_mult = round(upnl / risk_remaining, 2)
    elif desk_status == "PENDING":
        mark_out = mark if mark is not None else entry
    elif desk_status == "CLOSED":
        mark_out = None

    if desk_status == "PENDING":
        zone_lo, zone_hi = _pending_retest_zone(
            str(notes) if notes else None,
            entry=entry,
            direction=direction,
        )
    else:
        zone_lo, zone_hi = _parse_zone(str(notes) if notes else None)
    opened = _iso(opened_at) or utc_now().isoformat().replace("+00:00", "Z")

    market_context = None
    if isinstance(position, dict):
        market_context = position.get("market_context")
    else:
        market_context = getattr(position, "market_context", None)

    realized_out: float | None
    if desk_status == "CLOSED":
        realized_out = _round_money(realized)
    elif desk_status == "OPEN" and abs(realized) > 1e-12:
        realized_out = _round_money(realized)
    else:
        realized_out = None

    return DeskTrade(
        id=str(pos_id),
        symbol=symbol.upper(),
        side=side,
        entry=entry,
        mark=mark_out,
        exit=exit_px,
        stop=stop_out,
        currentStop=current_stop if current_stop > 0 else None,
        upnl=upnl,
        realized=realized_out,
        r=r_mult,
        margin=_round_money(margin_out),
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
        positionSize=display_qty if display_qty else None,
        notional=_round_money(display_notional) if display_notional > 0 else None,
        initialNotional=_round_money(initial_notional) if initial_notional > 0 else None,
        leverage=leverage,
        fees=_round_money(fees),
        notes=_notes_for(position, desk_status),
        marketContext=dict(market_context) if isinstance(market_context, dict) else None,
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

    # Prefer first fill so pre-reset flat pads (account.created_at ≪ activity) disappear.
    fill_rows.sort(key=lambda row: row[0])
    if fill_rows:
        curve_start = fill_rows[0][0]
    else:
        curve_start = start_at or now
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


def _parse_desk_ts(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    text = str(value).replace("Z", "+00:00")
    try:
        ts = datetime.fromisoformat(text)
    except ValueError:
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def equity_change_pct(
    points: list[DeskEquityPoint], *, hours: float = 24.0, as_of: datetime | None = None
) -> float:
    """Percent equity change vs the last curve point at/before ``hours`` ago."""
    if len(points) < 2:
        return 0.0
    now = as_of or utc_now()
    cutoff = now - timedelta(hours=hours)
    baseline = points[0].equity
    for point in points:
        ts = _parse_desk_ts(point.t)
        if ts is not None and ts <= cutoff:
            baseline = point.equity
    if abs(baseline) < 1e-12:
        return 0.0
    return round(((points[-1].equity - baseline) / abs(baseline)) * 100.0, 2)


def realized_change_pct(
    fills: list[PaperFill] | list[dict[str, Any]],
    *,
    initial_balance: float,
    hours: float = 24.0,
    as_of: datetime | None = None,
) -> float:
    """Net fill PnL over the window as percent of starting capital."""
    if initial_balance <= 0:
        return 0.0
    now = as_of or utc_now()
    cutoff = now - timedelta(hours=hours)
    total = 0.0
    for fill in fills:
        if isinstance(fill, dict):
            ts = _parse_desk_ts(fill.get("filled_at"))
            pnl = _f(fill.get("pnl"))
        else:
            ts = _parse_desk_ts(fill.filled_at)
            pnl = _f(fill.pnl)
        if ts is None or ts < cutoff:
            continue
        total += pnl
    return round((total / initial_balance) * 100.0, 2)


class DeskService:
    """Build public desk snapshots from the paper ledger."""

    def __init__(self, paper: PaperTradingService) -> None:
        self._paper = paper

    async def snapshot(
        self,
        session: AsyncSession,
        *,
        prices: dict[str, float] | None = None,
        market_regime: DeskMarketRegime | dict[str, Any] | None = None,
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
        closed_realized = 0.0
        open_realized = 0.0
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
            if trade.status == "CLOSED" and trade.realized is not None:
                closed_realized += trade.realized
            elif trade.status == "OPEN" and trade.realized is not None:
                open_realized += trade.realized

        trades.sort(key=lambda t: t.openedAt, reverse=True)

        initial = float(account.initial_balance)
        equity = float(summary.equity)
        account_realized = float(account.realized_pnl)
        # Equity curve: start at first fill when present (avoid flat pre-reset pad).
        fill_times = [f.filled_at for f in fills if getattr(f, "filled_at", None) is not None]
        start_at = min(fill_times) if fill_times else (getattr(account, "created_at", None) or utc_now())
        curve = build_equity_curve(
            initial_balance=initial,
            fills=fills,
            live_equity=equity,
            start_at=start_at,
        )
        portfolio = DeskPortfolio(
            totalCapital=_round_money(initial),
            equity=_round_money(equity),
            cash=_round_money(float(account.cash_balance)),
            marginLocked=_round_money(float(summary.open_margin)),
            realizedPnl=_round_money(closed_realized),
            openRealizedPnl=_round_money(open_realized),
            accountRealizedPnl=_round_money(account_realized),
            openUpnl=_round_money(open_upnl),
            openR=round(open_r, 2),
            totalReturnPct=round(((equity - initial) / initial) * 100.0, 2) if initial else 0.0,
            openPositions=int(summary.open_positions),
            pendingOrders=int(summary.pending_positions),
            closedTrades=int(summary.closed_trades),
            winRatePct=round(float(summary.win_rate) * 100.0, 1),
            equityChangePct=equity_change_pct(curve, hours=24.0),
            realizedChangePct=realized_change_pct(
                fills, initial_balance=initial, hours=24.0
            ),
        )
        regime_out: DeskMarketRegime | None = None
        if isinstance(market_regime, DeskMarketRegime):
            regime_out = market_regime
        elif isinstance(market_regime, dict):
            try:
                regime_out = DeskMarketRegime.model_validate(market_regime)
            except Exception:  # noqa: BLE001
                regime_out = None

        return DeskSnapshot(
            portfolio=portfolio,
            trades=trades,
            equity=curve,
            generatedAt=_iso(utc_now()) or utc_now().isoformat(),
            marketRegime=regime_out,
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
        winRatePct=0.0,
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
