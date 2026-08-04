"""PaperTradingService — virtuelles Depot mit konfigurierbarem Scale-out."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Iterator, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.entry_blackout import is_in_utc_blackout
from app.core.enums import Confidence, ExitReason, MarketPhase, SignalDirection
from app.core.logging import get_logger
from app.core.time import ensure_utc, timeframe_minutes, timeframe_to_timedelta, utc_now
from app.models.paper import PaperAccount, PaperFill, PaperPosition
from app.models.signal import Signal
from app.repositories.asset_repository import AssetRepository
from app.repositories.paper_repository import PaperRepository
from app.repositories.signal_repository import SignalRepository
from app.services.analysis_service import AnalysisOutcome
from app.indicators.engine import IndicatorEngine
from app.market_regime import MarketRegimeEngine, to_legacy_regime_snapshot
from app.signals.regime import (
    MarketRegime,
    RegimeSnapshot,
    direction_allowed_by_regime,
    log_regime_degraded,
    regime_from_indicators,
)
from app.signals.retest_entry import (
    RetestArmResult,
    RetestEntryConfig,
    arm_retest_entry,
    levels_from_entry_sl,
    wilder_atr,
)
from app.signals.risk import RiskManager, tp_multipliers_from_settings
from app.signals.types import RiskParameters, SignalResult

if TYPE_CHECKING:
    from app.bot.notifier import PaperTradeNotifier

logger = get_logger(__name__)

SKIP_PORTFOLIO_RISK = "skipped_portfolio_risk"
SKIP_MAX_POSITIONS = "skipped_max_positions"
SKIP_DIRECTION_CAP = "skipped_direction_cap"
SKIP_SYMBOL_CIRCUIT = "skipped_symbol_circuit"
SKIP_ENTRY_BLACKOUT = "skipped_entry_blackout"
SKIP_ZONE_STOP_OVERLAP = "skipped_zone_stop_overlap"
SKIP_REGIME = "skipped_regime"
PORTFOLIO_LIMIT_SKIPS = frozenset(
    {
        SKIP_PORTFOLIO_RISK,
        SKIP_MAX_POSITIONS,
        SKIP_DIRECTION_CAP,
        SKIP_SYMBOL_CIRCUIT,
        SKIP_ENTRY_BLACKOUT,
        SKIP_REGIME,
    }
)


def _parse_note_kv(notes: str | None, key: str) -> str | None:
    if not notes:
        return None
    prefix = f"{key}="
    for part in notes.split(";"):
        part = part.strip()
        if part.startswith(prefix):
            return part[len(prefix) :]
    return None


def _set_note_kv(notes: str | None, key: str, value: str) -> str:
    prefix = f"{key}="
    parts = [p for p in (notes or "").split(";") if p and not p.strip().startswith(prefix)]
    parts.append(f"{key}={value}")
    return ";".join(parts)


@dataclass
class PaperSummary:
    cash_balance: float
    initial_balance: float
    realized_pnl: float
    open_positions: int
    open_margin: float
    equity: float
    win_rate: float
    closed_trades: int
    profit_factor: float
    pending_positions: int = 0
    #: Risikonormierte Kennzahlen. Nur Positionen mit hinterlegtem 1R zaehlen.
    total_r: float = 0.0
    expectancy_r: float = 0.0
    fees_r: float = 0.0
    r_trades: int = 0


@dataclass
class PaperDigestOpenRow:
    symbol: str
    direction: str
    unrealized_usd: float | None
    unrealized_r: float | None
    mark: float | None
    current_stop: float
    rem_pct: float
    tp1_filled: bool
    tp2_filled: bool
    tp3_filled: bool


@dataclass
class PaperDigestCloseRow:
    symbol: str
    direction: str
    realized_usd: float
    realized_r: float | None
    exit_reason: str | None


@dataclass
class PaperDigestWindowStats:
    """Aggregierte Paper-Performance fuer ein Zeitfenster."""

    label: str
    closed_count: int
    closed_pnl: float
    closed_r: float
    opened_count: int
    win_count: int = 0
    equity_delta: float | None = None


@dataclass
class PaperDigestSnapshot:
    as_of: datetime
    summary: PaperSummary
    equity_return_pct: float
    hour_closed_count: int
    hour_closed_r: float
    hour_closed_pnl: float
    hour_opened_count: int
    open_rows: list[PaperDigestOpenRow]
    hour_closes: list[PaperDigestCloseRow]
    total_open_upnl_usd: float
    total_open_upnl_r: float
    risk_per_trade: float
    leverage: float
    max_notional: float
    max_open: int
    #: Zeitreihe Equity = Cash + Margin + Open PnL (rekonstruiert + Live-Punkt).
    equity_curve: list[tuple[datetime, float]] | None = None
        #: Fenster: 1h / 24h / 7d / 30d.
    windows: list[PaperDigestWindowStats] | None = None


@dataclass
class PaperBackfillResult:
    considered: int = 0
    opened: int = 0
    pending: int = 0
    skipped_existing: int = 0
    skipped_filters: int = 0
    skipped_cash: int = 0
    skipped_limits: int = 0
    opened_symbols: list[str] | None = None

    def __post_init__(self) -> None:
        if self.opened_symbols is None:
            self.opened_symbols = []


@dataclass
class PaperRebuildResult:
    reset_positions: int = 0
    backfill: PaperBackfillResult | None = None
    retest_filled: int = 0
    retest_skipped: int = 0
    retest_still_pending: int = 0
    replayed: int = 0
    still_open: int = 0


@dataclass
class PendingResolveResult:
    filled: int = 0
    skipped: int = 0
    still_pending: int = 0


def _equity_delta_since(
    equity_curve: list[tuple[datetime, float]],
    since: datetime,
    live_equity: float,
) -> float | None:
    """Equity-Aenderung seit ``since`` anhand der rekonstruierten Kurve."""
    if not equity_curve:
        return None
    baseline: float | None = None
    for at, equity in equity_curve:
        if at <= since:
            baseline = equity
        else:
            break
    if baseline is None:
        baseline = equity_curve[0][1]
    return float(live_equity) - float(baseline)


@dataclass(frozen=True)
class PositionSizing:
    """Ergebnis der Positionsberechnung fuer einen Paper-Trade."""

    quantity: Decimal
    notional: Decimal
    margin: Decimal
    risk_amount: Decimal


class _DispatchRecorder(Protocol):
    async def record_dispatch(self, result: object) -> None: ...


class PaperTradingService:
    """Oeffnet und verwaltet Paper-Positionen aus Signalen."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        notifier: PaperTradeNotifier | None = None,
        deduplicator: _DispatchRecorder | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._notifier = notifier
        self._deduplicator = deduplicator
        self._notify_enabled = True
        self._last_skip_reason: str | None = None
        self._regime_engine = MarketRegimeEngine(self._settings)

    @property
    def _scale_out_fractions(self) -> tuple[Decimal, Decimal, Decimal]:
        parts = self._settings.parsed_scale_out_fractions
        return (Decimal(str(parts[0])), Decimal(str(parts[1])), Decimal(str(parts[2])))

    @property
    def _tp_multipliers(self) -> tuple[float, float, float]:
        return tp_multipliers_from_settings(self._settings)

    def _entry_blackout_active(self, when: datetime) -> bool:
        spec = self._settings.paper_entry_blackout_utc.strip()
        if not spec:
            return False
        return is_in_utc_blackout(ensure_utc(when), spec)

    async def _symbol_circuit_breach(
        self,
        session: AsyncSession,
        account: PaperAccount,
        symbol: str,
        *,
        when: datetime | None = None,
    ) -> bool:
        """True wenn Symbol nach aufeinanderfolgenden Verlusten pausiert ist."""
        threshold = int(self._settings.paper_symbol_circuit_breaker_losses)
        pause_hours = int(self._settings.paper_symbol_circuit_breaker_hours)
        if threshold <= 0 or pause_hours <= 0:
            return False

        repo = PaperRepository(session)
        recent = await repo.list_recent_closed_by_symbol(
            account.id, symbol.upper(), limit=threshold
        )
        if len(recent) < threshold:
            return False
        if any(float(p.realized_pnl) >= 0 for p in recent):
            return False

        last_closed = ensure_utc(recent[0].closed_at or recent[0].opened_at)
        reference = ensure_utc(when or utc_now())
        return reference < last_closed + timedelta(hours=pause_hours)

    def set_notifier(self, notifier: PaperTradeNotifier | None) -> None:
        self._notifier = notifier

    def set_deduplicator(self, deduplicator: _DispatchRecorder | None) -> None:
        self._deduplicator = deduplicator

    @property
    def last_skip_reason(self) -> str | None:
        """Grund des letzten abgelehnten Entry-Versuchs (siehe ``skipped_*``)."""
        return self._last_skip_reason

    @contextmanager
    def _without_notifications(self) -> Iterator[None]:
        previous = self._notify_enabled
        self._notify_enabled = False
        try:
            yield
        finally:
            self._notify_enabled = previous

    async def _notify_open(
        self,
        position: PaperPosition,
        *,
        retest_fill: bool = False,
        reasons: list[str] | None = None,
    ) -> None:
        if not self._notify_enabled or self._notifier is None:
            return
        try:
            await self._notifier.notify_open(
                position, retest_fill=retest_fill, reasons=reasons
            )
        except Exception as exc:
            logger.warning(
                "paper_trade_open_notify_error",
                symbol=position.symbol,
                error=str(exc),
            )

    @property
    def enabled(self) -> bool:
        return self._settings.enable_paper_trading

    @property
    def retest_enabled(self) -> bool:
        return bool(self._settings.paper_retest_entry_enabled)

    def _retest_config(self) -> RetestEntryConfig:
        return RetestEntryConfig(
            zone_near=Decimal(str(self._settings.paper_retest_zone_near)),
            zone_far=Decimal(str(self._settings.paper_retest_zone_far)),
            pending_multiplier=int(self._settings.paper_retest_pending_multiplier),
            min_bars_in_zone=int(self._settings.paper_retest_min_bars_in_zone),
            trendline_gate_enabled=bool(self._settings.signal_trendline_gate_enabled),
            trendline_buffer_atr=float(self._settings.signal_trendline_buffer_atr),
            trendline_lookback=int(self._settings.signal_trendline_lookback),
            trendline_min_points=int(self._settings.signal_trendline_min_points),
            trendline_min_r2=float(self._settings.signal_trendline_min_r2),
            trendline_min_clearance_atr=float(
                self._settings.signal_trendline_min_clearance_atr
            ),
        )

    def _regime_blocks_direction(
        self,
        direction: SignalDirection,
        regime_snapshot: RegimeSnapshot | None,
    ) -> bool:
        if not self._settings.regime_filter_enabled:
            return False
        if not self._settings.market_regime_hard_veto:
            return False
        if regime_snapshot is None or not regime_snapshot.available:
            if regime_snapshot is not None and regime_snapshot.detail:
                log_regime_degraded(regime_snapshot.detail)
            return False
        if direction_allowed_by_regime(regime_snapshot.regime, direction):
            return False
        return True

    def _update_peak_price(self, position: PaperPosition, price: float) -> None:
        peak = position.peak_price
        is_long = SignalDirection(position.direction).is_long
        if peak is None:
            position.peak_price = Decimal(str(price))
            return
        current = float(peak)
        if is_long and price > current:
            position.peak_price = Decimal(str(price))
        elif not is_long and price < current:
            position.peak_price = Decimal(str(price))

    def _mfe_r(self, position: PaperPosition) -> float:
        risk_amount = float(position.risk_amount)
        if risk_amount <= 0:
            return 0.0
        entry = float(position.entry_price)
        stop = float(position.stop_loss)
        r = abs(entry - stop)
        if r <= 0:
            return 0.0
        peak = float(position.peak_price or position.entry_price)
        is_long = SignalDirection(position.direction).is_long
        if is_long:
            return max(0.0, (peak - entry) / r)
        return max(0.0, (entry - peak) / r)

    async def _maybe_early_scratch(
        self,
        session: AsyncSession,
        account: PaperAccount,
        position: PaperPosition,
        *,
        price: float,
        when: datetime,
    ) -> bool:
        hours = int(self._settings.paper_early_scratch_hours)
        if hours <= 0 or position.status != "open" or position.tp1_filled:
            return False
        if position.opened_at is None:
            return False
        elapsed_h = (ensure_utc(when) - ensure_utc(position.opened_at)).total_seconds() / 3600.0
        if elapsed_h < hours:
            return False
        mfe = self._mfe_r(position)
        if mfe >= float(self._settings.paper_early_scratch_mfe_r):
            return False
        is_long = SignalDirection(position.direction).is_long
        slip_px = self._slip_price(price, is_long=is_long, side="exit")
        await self._close_remaining(
            session,
            account,
            position,
            price=slip_px,
            reason=ExitReason.EARLY_SCRATCH,
            when=when,
        )
        logger.info(
            "paper_early_scratch",
            symbol=position.symbol,
            hours=round(elapsed_h, 2),
            mfe_r=round(mfe, 3),
            price=slip_px,
        )
        return True

    async def _fetch_regime_snapshot(self, provider) -> RegimeSnapshot:
        if not self._settings.regime_filter_enabled and not self._settings.market_regime_enabled:
            return RegimeSnapshot(None, "regime_filter_disabled", False)
        if self._settings.market_regime_enabled:
            try:
                market = await self._regime_engine.resolve(provider, refresh=True)
                legacy = to_legacy_regime_snapshot(market)
                if not legacy.available:
                    log_regime_degraded(legacy.detail)
                return legacy
            except Exception as exc:
                log_regime_degraded(str(exc))
                return RegimeSnapshot(None, f"btc_regime_error: {exc}", False)
        symbol = self._settings.regime_btc_symbol.upper()
        timeframe = self._settings.regime_timeframe
        try:
            series = await provider.get_candles(
                symbol,
                timeframe,
                limit=self._settings.candle_limit,
            )
            if series is None or series.is_empty:
                log_regime_degraded("btc_candles_empty")
                return RegimeSnapshot(None, "btc_candles_empty", False)
            indicators = IndicatorEngine(
                min_candles=self._settings.min_candles_required
            ).compute(series.to_dataframe(), timeframe, symbol=symbol)
            snapshot = regime_from_indicators(indicators)
            if not snapshot.available:
                log_regime_degraded(snapshot.detail)
            return snapshot
        except Exception as exc:
            log_regime_degraded(str(exc))
            return RegimeSnapshot(None, f"btc_regime_error: {exc}", False)

    def _market_context_for(self, result: SignalResult) -> dict | None:
        ctx = getattr(result, "market_context", None)
        return dict(ctx) if isinstance(ctx, dict) else None

    @staticmethod
    def _primary_atr(result: SignalResult) -> float | None:
        """ATR14 from the primary TF assessment (for pending retest zone notes)."""
        assessment = result.assessments.get(result.primary_timeframe)
        if assessment is None and result.assessments:
            assessment = next(iter(result.assessments.values()), None)
        if assessment is None or assessment.indicators is None:
            return None
        atr = assessment.indicators.atr_14
        if atr is None or atr <= 0:
            return None
        return float(atr)

    def _slip_price(self, price: float, *, is_long: bool, side: str) -> float:
        """Adverse slippage for market-like fills (entry / stop / expiry / scratch).

        ``side='entry'``: long pays up, short sells down.
        ``side='exit'``: long sells down, short covers up.
        TP limit fills should not call this.
        """
        slip = float(self._settings.paper_slippage_percent) / 100.0
        if slip <= 0 or price <= 0:
            return price
        if side == "entry":
            return price * (1.0 + slip) if is_long else price * (1.0 - slip)
        return price * (1.0 - slip) if is_long else price * (1.0 + slip)

    def _remaining_notional(self, position: PaperPosition) -> Decimal:
        qty = Decimal(str(position.remaining_quantity or 0))
        entry = Decimal(str(position.entry_price or 0))
        if qty <= 0 or entry <= 0:
            return Decimal("0")
        return qty * entry

    def _size_position(self, entry: Decimal, stop: Decimal) -> PositionSizing | None:
        """Stueckzahl aus Risikobetrag und Stop-Abstand statt aus fixer Margin.

        Bei fixer Margin bestimmt allein der Stop-Abstand, wie viel Dollar ein
        Stop-Treffer kostet — im Ledger schwankte das um den Faktor 15. Ueber
        ``paper_risk_per_trade_usd`` kostet jeder Stop-Treffer gleich viel, die
        Trades werden dadurch ueberhaupt erst untereinander vergleichbar (1R).
        """
        if entry <= 0:
            return None

        leverage = Decimal(str(self._settings.paper_leverage))
        risk_budget = Decimal(str(self._settings.paper_risk_per_trade_usd))
        stop_distance = abs(entry - stop)

        if risk_budget <= 0 or stop_distance <= 0:
            margin = Decimal(str(self._settings.paper_margin_per_trade))
            notional = margin * leverage
            quantity = notional / entry
            # Fixed-margin mode: 1R := margin so Trade-R / openR stay comparable
            # across stop distances (qty×stop would swing 1R by 10×+).
            return PositionSizing(
                quantity=quantity,
                notional=notional,
                margin=margin,
                risk_amount=margin if stop_distance > 0 else Decimal("0"),
            )

        quantity = Decimal(
            str(RiskManager.position_size_for_risk(float(risk_budget), float(stop_distance)))
        )
        notional = quantity * entry
        cap = Decimal(str(self._settings.paper_max_notional_usd))
        if cap > 0 and notional > cap:
            quantity = cap / entry
            notional = cap
        if quantity <= 0:
            return None

        return PositionSizing(
            quantity=quantity,
            notional=notional,
            margin=notional / leverage,
            risk_amount=quantity * stop_distance,
        )

    @staticmethod
    def _open_risk_used(positions: list[PaperPosition]) -> Decimal:
        """Offenes Restrisiko: 1R skaliert mit der noch offenen Stueckzahl.

        Nach einem TP-Teilverkauf steht nur noch der Rest im Feuer, sonst wuerde
        eine zu drei Vierteln geschlossene Position das Budget voll blockieren.
        """
        used = Decimal("0")
        for position in positions:
            risk = Decimal(str(position.risk_amount or 0))
            initial = Decimal(str(position.initial_quantity or 0))
            if risk <= 0 or initial <= 0:
                continue
            share = Decimal(str(position.remaining_quantity or 0)) / initial
            if share <= 0:
                continue
            used += risk * min(share, Decimal("1"))
        return used

    @staticmethod
    def _equity_base(account: PaperAccount, positions: list[PaperPosition]) -> Decimal:
        return Decimal(str(account.cash_balance)) + sum(
            (Decimal(str(p.margin_used)) for p in positions), Decimal("0")
        )

    def _per_direction_cap(self, regime_snapshot: RegimeSnapshot | None) -> int:
        """Direction cap: 8/8 in neutral, aligned-side cap in bull/bear.

        Unknown/unavailable regime falls back to the aligned-side cap; global
        ``paper_max_open_positions`` still binds the book.
        """
        aligned = int(self._settings.paper_max_open_per_direction)
        neutral = int(self._settings.paper_max_open_per_direction_neutral)
        if regime_snapshot is None or not regime_snapshot.available:
            return aligned
        if regime_snapshot.regime is MarketRegime.NEUTRAL:
            return neutral
        return aligned

    async def _portfolio_limit_breach(
        self,
        session: AsyncSession,
        account: PaperAccount,
        *,
        direction: str,
        risk_amount: Decimal,
        at: datetime | None = None,
        regime_snapshot: RegimeSnapshot | None = None,
    ) -> str | None:
        """``skipped_*``-Grund, wenn der Entry ein Portfolio-Limit reissen wuerde.

        Pending Retest-Entries zaehlen nicht mit: sie binden weder Margin noch
        Risiko, geprueft wird erst beim Fill.

        ``at`` = Fill-/Entry-Zeit fuer as-of Book (Rebuild replayed Trades bereits
        auf ``closed``, obwohl ihr Open-Fenster den Fill noch ueberlappt).
        """
        # Flush so same-batch / same-timestamp fills see each other in as-of queries.
        await session.flush()
        repo = PaperRepository(session)
        if at is not None:
            open_positions = await repo.list_filled_open_at(account.id, ensure_utc(at))
        else:
            open_positions = await repo.list_open_positions(account.id)

        max_open = int(self._settings.paper_max_open_positions)
        if max_open > 0 and len(open_positions) >= max_open:
            return SKIP_MAX_POSITIONS

        per_direction = self._per_direction_cap(regime_snapshot)
        if per_direction > 0:
            is_long = SignalDirection(direction).is_long
            same_side = sum(
                1
                for p in open_positions
                if SignalDirection(p.direction).is_long == is_long
            )
            if same_side >= per_direction:
                return SKIP_DIRECTION_CAP

        # Fixed-margin mode sets risk_amount := margin for R-accounting. Treating
        # that as portfolio "risk %" double-counts the cash/margin constraint and
        # would cap the book at ~equity*pct/margin (≈5 on $5k / 30% / $300).
        risk_budget_mode = float(self._settings.paper_risk_per_trade_usd) > 0
        risk_pct = Decimal(str(self._settings.paper_max_portfolio_risk_pct))
        # >=100% = full book allowed; cash/margin + max_open remain the hard caps.
        if risk_budget_mode and 0 < risk_pct < 100 and risk_amount > 0:
            budget = self._equity_base(account, open_positions) * risk_pct / Decimal("100")
            if self._open_risk_used(open_positions) + risk_amount > budget:
                return SKIP_PORTFOLIO_RISK

        return None

    async def _cash_available_at(
        self,
        session: AsyncSession,
        account: PaperAccount,
        at: datetime,
    ) -> Decimal:
        """Cash free at ``at`` after re-locking margin of as-of-open fills.

        Rebuild settles each trade before the next signal, so ``cash_balance``
        has already received margin back from positions that would still be open
        at a later fill time. Subtract that ghost-locked margin.
        """
        at = ensure_utc(at)
        await session.flush()
        open_at = await PaperRepository(session).list_filled_open_at(account.id, at)
        ghost = Decimal("0")
        for p in open_at:
            if str(p.status) != "closed":
                continue  # still open → margin already deducted from cash_balance
            ghost += Decimal(str(p.margin_used or 0))
        return Decimal(str(account.cash_balance)) - ghost

    @staticmethod
    def _slot_priority(position: PaperPosition) -> tuple:
        """Higher-priority fills first when the book is contested.

        When more qualified fills compete than cash/caps allow: best score
        first (long high / short low), then RR as tiebreaker. Chronology
        (fill_time) is applied by the caller before this key.
        """
        rr = 0.0
        try:
            entry = float(position.entry_price or 0)
            stop = float(position.stop_loss or 0)
            tp2 = float(position.take_profit_2 or 0)
            dist = abs(entry - stop)
            if entry > 0 and dist > 0 and tp2 > 0:
                rr = abs(tp2 - entry) / dist
        except (TypeError, ValueError):
            rr = 0.0
        score = float(position.signal_score or 50.0)
        try:
            is_long = SignalDirection(position.direction).is_long
        except ValueError:
            is_long = True
        extremity = score if is_long else (100.0 - score)
        return (-extremity, -rr, int(position.id or 0))

    async def get_or_create_account(self, session: AsyncSession) -> PaperAccount:
        repo = PaperRepository(session)
        return await repo.get_or_create_account(
            name="default",
            initial_balance=Decimal(str(self._settings.paper_initial_balance)),
            margin_per_trade=Decimal(str(self._settings.paper_margin_per_trade)),
            leverage=self._settings.paper_leverage,
        )

    async def open_from_signal(
        self,
        session: AsyncSession,
        outcome: AnalysisOutcome,
        *,
        opened_at: datetime | None = None,
        regime_snapshot: RegimeSnapshot | None = None,
    ) -> PaperPosition | None:
        if not self.enabled:
            return None
        self._last_skip_reason = None
        result = outcome.result
        if not result.direction.is_actionable or result.risk is None:
            return None

        account = await self.get_or_create_account(session)
        repo = PaperRepository(session)

        existing = await repo.get_active_by_symbol(account.id, result.symbol)
        if existing is not None:
            self._last_skip_reason = "skipped_existing"
            logger.info(
                "paper_skip_already_open",
                symbol=result.symbol,
                status=existing.status,
            )
            return None

        opened_at = opened_at or utc_now()
        if self._entry_blackout_active(opened_at):
            self._last_skip_reason = SKIP_ENTRY_BLACKOUT
            logger.info("paper_skip_entry_blackout", symbol=result.symbol, at=opened_at.isoformat())
            return None
        if await self._symbol_circuit_breach(session, account, result.symbol, when=opened_at):
            self._last_skip_reason = SKIP_SYMBOL_CIRCUIT
            logger.info("paper_skip_symbol_circuit", symbol=result.symbol)
            return None

        if self._regime_blocks_direction(result.direction, regime_snapshot):
            self._last_skip_reason = SKIP_REGIME
            regime_val = regime_snapshot.regime.value if regime_snapshot and regime_snapshot.regime else "unknown"
            logger.info(
                "paper_skip_regime",
                symbol=result.symbol,
                direction=result.direction.value,
                regime=regime_val,
            )
            return None

        if self.retest_enabled:
            return await self._open_pending_retest(
                session, account, outcome, opened_at=opened_at
            )

        leverage = Decimal(str(self._settings.paper_leverage))
        # Fill at the same reference RiskManager used for SL/TP/RR (zone edge),
        # not mid — otherwise stop distance widens at fill and gated R:R lies.
        if result.direction.is_long and result.risk.entry_low is not None:
            raw_entry = float(result.risk.entry_low)
        elif (not result.direction.is_long) and result.risk.entry_high is not None:
            raw_entry = float(result.risk.entry_high)
        else:
            raw_entry = float(result.risk.entry_mid or result.reference_price)
        entry = Decimal(
            str(
                self._slip_price(
                    raw_entry,
                    is_long=result.direction.is_long,
                    side="entry",
                )
            )
        )
        stop = Decimal(str(result.risk.stop_loss))
        sizing = self._size_position(entry, stop)
        if sizing is None:
            return None

        breach = await self._portfolio_limit_breach(
            session,
            account,
            direction=result.direction.value,
            risk_amount=sizing.risk_amount,
            at=opened_at,
            regime_snapshot=regime_snapshot,
        )
        if breach is not None:
            self._last_skip_reason = breach
            logger.info(
                "paper_skip_portfolio_limit",
                symbol=result.symbol,
                direction=result.direction.value,
                reason=breach,
                risk_amount=float(sizing.risk_amount),
            )
            return None

        notional = sizing.notional
        quantity = sizing.quantity
        fee_rate = Decimal(str(self._settings.paper_fee_percent)) / Decimal("100")
        entry_fee = notional * fee_rate
        margin = sizing.margin
        # Harte Regel: ohne volle Trade-Margin ($300) + Fee kein Fill.
        cash_needed = margin + entry_fee
        cash_free = await self._cash_available_at(session, account, opened_at)
        min_margin = Decimal(str(self._settings.paper_margin_per_trade))
        if cash_free < min_margin or cash_free < cash_needed:
            self._last_skip_reason = "skipped_cash"
            logger.warning(
                "paper_insufficient_cash",
                cash=float(account.cash_balance),
                cash_as_of=float(cash_free),
                needed=float(cash_needed),
                min_margin=float(min_margin),
                at=opened_at.isoformat(),
            )
            return None

        account.cash_balance -= cash_needed
        account.realized_pnl -= entry_fee
        now = opened_at or utc_now()

        position = PaperPosition(
            account_id=account.id,
            signal_id=outcome.signal_id,
            asset_id=outcome.asset_id,
            symbol=result.symbol.upper(),
            direction=result.direction.value,
            status="open",
            timeframe=result.primary_timeframe,
            entry_price=entry,
            stop_loss=Decimal(str(result.risk.stop_loss)),
            current_stop=Decimal(str(result.risk.stop_loss)),
            take_profit_1=Decimal(str(result.risk.take_profit_1)),
            take_profit_2=Decimal(str(result.risk.take_profit_2)),
            take_profit_3=Decimal(str(result.risk.take_profit_3)),
            initial_quantity=quantity,
            remaining_quantity=quantity,
            margin_used=margin,
            notional=notional,
            leverage=float(leverage),
            fees=entry_fee,
            realized_pnl=-entry_fee,
            risk_amount=sizing.risk_amount,
            signal_score=result.score,
            opened_at=now,
            expires_at=result.expires_at,
            peak_price=entry,
            market_context=self._market_context_for(result),
        )
        await repo.add_position(position)
        await repo.add_fill(
            PaperFill(
                position_id=position.id,
                reason="entry",
                price=entry,
                quantity=quantity,
                fee=entry_fee,
                pnl=-entry_fee,
                filled_at=now,
            )
        )

        logger.info(
            "paper_position_opened",
            symbol=position.symbol,
            direction=position.direction,
            margin=float(margin),
            notional=float(notional),
            quantity=float(quantity),
            risk_amount=float(sizing.risk_amount),
        )
        await self._notify_open(position, reasons=result.reasons)
        return position

    async def _open_pending_retest(
        self,
        session: AsyncSession,
        account: PaperAccount,
        outcome: AnalysisOutcome,
        *,
        opened_at: datetime | None = None,
    ) -> PaperPosition | None:
        """Pending-Entry anlegen; Fill erst nach ATR-Pullback in die Retest-Zone."""
        result = outcome.result
        assert result.risk is not None
        repo = PaperRepository(session)
        leverage = Decimal(str(self._settings.paper_leverage))
        # Same edge reference RiskManager used for SL/TP — not mid — so retest R
        # distance matches the gated signal geometry.
        if result.direction.is_long and result.risk.entry_low is not None:
            entry = Decimal(str(result.risk.entry_low))
        elif (not result.direction.is_long) and result.risk.entry_high is not None:
            entry = Decimal(str(result.risk.entry_high))
        else:
            entry = Decimal(str(result.risk.entry_mid or result.reference_price))
        stop = Decimal(str(result.risk.stop_loss))
        if entry <= 0:
            return None

        armed_at = ensure_utc(opened_at or result.created_at or utc_now())
        pending_mult = int(self._settings.paper_retest_pending_multiplier)
        expires_at = armed_at + pending_mult * timeframe_to_timedelta(result.primary_timeframe)

        # Vorlaeufige Groesse auf Basis des Referenz-Entries; beim Fill wird sie
        # mit dem tatsaechlichen Fill-Preis und -Stop neu berechnet.
        sizing = self._size_position(entry, stop)
        if sizing is None:
            return None

        near = Decimal(str(self._settings.paper_retest_zone_near))
        far = Decimal(str(self._settings.paper_retest_zone_far))
        atr_f = self._primary_atr(result)
        zone_note = f"zone_atr={float(near)}-{float(far)}"
        # Annotate only — authoritative zone∩SL gate is arm_retest_entry at
        # resolve (candle ATR). Assessment ATR here can disagree and would
        # falsely skip or falsely arm vs the resolve path.
        if atr_f is not None and atr_f > 0:
            from app.signals.retest_entry import retest_zone

            zone_lo, zone_hi = retest_zone(
                entry,
                Decimal(str(atr_f)),
                is_long=result.direction.is_long,
                zone_near=near,
                zone_far=far,
            )
            zone_note = (
                f"zone={float(zone_lo)}-{float(zone_hi)};"
                f"zone_atr={float(near)}-{float(far)};atr={atr_f}"
            )

        position = PaperPosition(
            account_id=account.id,
            signal_id=outcome.signal_id,
            asset_id=outcome.asset_id,
            symbol=result.symbol.upper(),
            direction=result.direction.value,
            status="pending",
            timeframe=result.primary_timeframe,
            entry_price=entry,
            stop_loss=stop,
            current_stop=stop,
            take_profit_1=Decimal(str(result.risk.take_profit_1)),
            take_profit_2=Decimal(str(result.risk.take_profit_2)),
            take_profit_3=Decimal(str(result.risk.take_profit_3)),
            initial_quantity=sizing.quantity,
            remaining_quantity=sizing.quantity,
            margin_used=Decimal("0"),
            notional=sizing.notional,
            leverage=float(leverage),
            fees=Decimal("0"),
            risk_amount=sizing.risk_amount,
            signal_score=result.score,
            opened_at=armed_at,
            expires_at=expires_at,
            notes=(
                f"retest_pending;ref_entry={float(entry)};orig_sl={float(stop)};"
                f"armed_at={armed_at.isoformat()};{zone_note}"
            ),
            market_context=self._market_context_for(result),
        )
        await repo.add_position(position)
        logger.info(
            "paper_position_pending_retest",
            symbol=position.symbol,
            direction=position.direction,
            armed_at=armed_at.isoformat(),
            expires_at=expires_at.isoformat(),
            ref_entry=float(entry),
            orig_sl=float(stop),
        )
        return position

    async def resolve_pending_retest(
        self,
        session: AsyncSession,
        provider,
        *,
        end_time: datetime | None = None,
        historical: bool = False,
    ) -> PendingResolveResult:
        """Pending-Entries gegen Primary-TF-Kerzen pruefen (Fill / Skip / weiter warten)."""
        out = PendingResolveResult()
        if not self.enabled or not self.retest_enabled:
            return out

        account = await self.get_or_create_account(session)
        repo = PaperRepository(session)
        pending = await repo.list_pending_positions(account.id)
        if not pending:
            return out

        cfg = self._retest_config()
        cutoff = ensure_utc(end_time or utc_now())
        # Historical rebuild must not apply *current* BTC regime to past fills.
        regime_snapshot = (
            None if historical else await self._fetch_regime_snapshot(provider)
        )
        # ATR braucht Warmup; etwas Historie vor Armed-Zeit laden.
        lookback_pad = timedelta(days=14)
        # Arm first, activate later: fill_time order, then best score within a bar.
        fill_jobs: list[tuple[datetime, PaperPosition, RetestArmResult]] = []

        for position in pending:
            tf = position.timeframe or "1h"
            # Wall-clock expiry: do not wait for candle timestamps when the
            # pending window is already over (stale/missing feeds included).
            # Strict ``>`` matches arm_retest_entry candle expiry semantics.
            if position.expires_at is not None and cutoff > ensure_utc(position.expires_at):
                await self._cancel_pending_retest(
                    session,
                    position,
                    RetestArmResult(
                        status="skipped_expiry",
                        resolved_at=ensure_utc(position.expires_at),
                        note="pending_expired_wall_clock",
                    ),
                )
                out.skipped += 1
                continue

            try:
                series = await provider.get_candles(
                    position.symbol,
                    tf,
                    limit=100_000,
                    start_time=ensure_utc(position.opened_at) - lookback_pad,
                    end_time=cutoff,
                )
                candles = (
                    list(series.candles)
                    if series is not None and not series.is_empty
                    else []
                )
            except Exception as exc:
                logger.warning(
                    "paper_retest_candles_failed",
                    symbol=position.symbol,
                    error=str(exc),
                )
                out.still_pending += 1
                continue

            arm = arm_retest_entry(
                direction=position.direction,
                arm_time=position.opened_at,
                reference_entry=float(position.entry_price),
                original_stop=float(position.stop_loss),
                timeframe=tf,
                candles=candles,
                config=cfg,
            )
            if (
                arm.filled
                and arm.fill_price is not None
                and arm.fill_time is not None
                and arm.stop is not None
            ):
                fill_jobs.append((ensure_utc(arm.fill_time), position, arm))
            elif arm.status == "pending":
                out.still_pending += 1
            else:
                await self._cancel_pending_retest(session, position, arm)
                out.skipped += 1

        fill_jobs.sort(
            key=lambda job: (
                job[0],
                self._slot_priority(job[1]),
                int(job[1].id or 0),
            )
        )
        for _fill_time, position, arm in fill_jobs:
            activated = await self._activate_pending_retest(
                session, account, position, arm, regime_snapshot=regime_snapshot
            )
            if activated:
                out.filled += 1
            else:
                out.skipped += 1

        return out

    async def _activate_pending_retest(
        self,
        session: AsyncSession,
        account: PaperAccount,
        position: PaperPosition,
        arm: RetestArmResult,
        *,
        regime_snapshot: RegimeSnapshot | None = None,
    ) -> bool:
        assert arm.fill_price is not None and arm.fill_time is not None and arm.stop is not None
        direction = SignalDirection(position.direction)
        fill_time = ensure_utc(arm.fill_time)
        if self._entry_blackout_active(fill_time):
            self._last_skip_reason = SKIP_ENTRY_BLACKOUT
            await self._cancel_pending_retest(
                session,
                position,
                RetestArmResult(
                    status=SKIP_ENTRY_BLACKOUT,
                    resolved_at=fill_time,
                    note="entry_blackout_at_fill",
                    zone_lo=arm.zone_lo,
                    zone_hi=arm.zone_hi,
                ),
            )
            return False
        if await self._symbol_circuit_breach(
            session, account, position.symbol, when=fill_time
        ):
            self._last_skip_reason = SKIP_SYMBOL_CIRCUIT
            await self._cancel_pending_retest(
                session,
                position,
                RetestArmResult(
                    status=SKIP_SYMBOL_CIRCUIT,
                    resolved_at=fill_time,
                    note="symbol_circuit_at_fill",
                    zone_lo=arm.zone_lo,
                    zone_hi=arm.zone_hi,
                ),
            )
            return False
        if self._regime_blocks_direction(direction, regime_snapshot):
            self._last_skip_reason = SKIP_REGIME
            await self._cancel_pending_retest(
                session,
                position,
                RetestArmResult(
                    status=SKIP_REGIME,
                    resolved_at=fill_time,
                    note="regime_blocked_at_fill",
                    zone_lo=arm.zone_lo,
                    zone_hi=arm.zone_hi,
                ),
            )
            return False
        is_long = SignalDirection(position.direction).is_long
        entry = Decimal(
            str(
                self._slip_price(
                    float(arm.fill_price),
                    is_long=is_long,
                    side="entry",
                )
            )
        )
        stop = Decimal(str(arm.stop))
        tp1, tp2, tp3 = levels_from_entry_sl(
            entry, stop, is_long=is_long, multipliers=tuple(Decimal(str(m)) for m in self._tp_multipliers)
        )

        sizing = self._size_position(entry, stop)
        if sizing is None:
            await self._cancel_pending_retest(
                session,
                position,
                RetestArmResult(
                    status="skipped_sizing",
                    resolved_at=fill_time,
                    note="no_valid_size_at_fill",
                    zone_lo=arm.zone_lo,
                    zone_hi=arm.zone_hi,
                ),
            )
            return False

        breach = await self._portfolio_limit_breach(
            session,
            account,
            direction=position.direction,
            risk_amount=sizing.risk_amount,
            at=fill_time,
            regime_snapshot=regime_snapshot,
        )
        if breach is not None:
            self._last_skip_reason = breach
            logger.info(
                "paper_retest_activate_portfolio_limit",
                symbol=position.symbol,
                direction=position.direction,
                reason=breach,
                risk_amount=float(sizing.risk_amount),
                at=fill_time.isoformat(),
            )
            await self._cancel_pending_retest(
                session,
                position,
                RetestArmResult(
                    status=breach,
                    resolved_at=fill_time,
                    note="portfolio_limit_at_fill",
                    zone_lo=arm.zone_lo,
                    zone_hi=arm.zone_hi,
                ),
            )
            return False

        notional = sizing.notional
        quantity = sizing.quantity
        fee_rate = Decimal(str(self._settings.paper_fee_percent)) / Decimal("100")
        entry_fee = notional * fee_rate
        margin = sizing.margin
        cash_needed = margin + entry_fee
        cash_free = await self._cash_available_at(session, account, fill_time)
        min_margin = Decimal(str(self._settings.paper_margin_per_trade))
        if cash_free < min_margin or cash_free < cash_needed:
            logger.warning(
                "paper_retest_activate_insufficient_cash",
                symbol=position.symbol,
                cash=float(account.cash_balance),
                cash_as_of=float(cash_free),
                needed=float(cash_needed),
                min_margin=float(min_margin),
                at=fill_time.isoformat(),
            )
            await self._cancel_pending_retest(
                session,
                position,
                RetestArmResult(
                    status="skipped_cash",
                    resolved_at=fill_time,
                    note="insufficient_cash_at_fill",
                    zone_lo=arm.zone_lo,
                    zone_hi=arm.zone_hi,
                ),
            )
            return False

        tf = position.timeframe or "1h"
        mult = int(self._settings.signal_expiry_multiplier)
        # Ab Fill, nicht ab Arm-Zeit: sonst frisst die Wartezeit auf den Retest
        # einen Teil der Haltedauer und der Trade laeuft frueher aus.
        signal_expiry = fill_time + mult * timeframe_to_timedelta(tf)

        account.cash_balance -= cash_needed
        account.realized_pnl -= entry_fee

        position.status = "open"
        position.entry_price = entry
        position.stop_loss = stop
        position.current_stop = stop
        position.take_profit_1 = tp1
        position.take_profit_2 = tp2
        position.take_profit_3 = tp3
        position.initial_quantity = quantity
        position.remaining_quantity = quantity
        position.margin_used = margin
        position.notional = notional
        position.fees = entry_fee
        position.realized_pnl = -entry_fee
        position.risk_amount = sizing.risk_amount
        position.opened_at = fill_time
        position.expires_at = signal_expiry
        position.peak_price = entry
        position.notes = (
            f"retest_filled;zone={arm.zone_lo}-{arm.zone_hi};atr={arm.atr};"
            f"bars={arm.bars_waited};note={arm.note};"
            f"fill={float(entry)};stop={float(stop)}"
        )

        repo = PaperRepository(session)
        await repo.add_fill(
            PaperFill(
                position_id=position.id,
                reason="entry",
                price=entry,
                quantity=quantity,
                fee=entry_fee,
                pnl=-entry_fee,
                filled_at=fill_time,
            )
        )
        logger.info(
            "paper_position_retest_filled",
            symbol=position.symbol,
            entry=float(entry),
            stop=float(stop),
            zone_lo=arm.zone_lo,
            zone_hi=arm.zone_hi,
            bars_waited=arm.bars_waited,
            fill_time=fill_time.isoformat(),
            risk_amount=float(sizing.risk_amount),
            expires_at=signal_expiry.isoformat(),
        )
        reasons: list[str] | None = None
        if position.signal_id is not None:
            signal = await SignalRepository(session).get_by_id(position.signal_id)
            if signal is not None and signal.reasons:
                reasons = list(signal.reasons)
        await self._notify_open(position, retest_fill=True, reasons=reasons)
        if position.signal_id is not None:
            await SignalRepository(session).mark_dispatched(position.signal_id)
        await self._record_dispatch_for_position(position, reasons=reasons)
        return True

    async def _record_dispatch_for_position(
        self,
        position: PaperPosition,
        *,
        reasons: list[str] | None = None,
    ) -> None:
        """Seed dedup cooldown after a real entry (IST open or retest fill)."""
        if self._deduplicator is None:
            return
        try:
            from app.bot.formatting import signal_result_from_paper_position

            result = signal_result_from_paper_position(position, reasons=reasons)
            await self._deduplicator.record_dispatch(result)
        except Exception as exc:
            logger.warning(
                "paper_dispatch_record_failed",
                symbol=position.symbol,
                error=str(exc),
            )

    async def _cancel_pending_retest(
        self,
        session: AsyncSession,
        position: PaperPosition,
        arm: RetestArmResult,
    ) -> None:
        position.status = "cancelled"
        # Prefer the skip-bar / expiry event time. Wall-clock "now" during a
        # historical rebuild would mark closed_at in the future relative to
        # every later signal and falsely busy-lock the symbol for the rest of
        # the stream (missed re-arms after skipped_sl / skipped_expiry).
        closed_at = arm.resolved_at or position.expires_at or utc_now()
        position.closed_at = ensure_utc(closed_at)
        position.exit_reason = ExitReason.RETEST_SKIPPED.value
        position.remaining_quantity = Decimal("0")
        position.margin_used = Decimal("0")
        position.notes = (
            f"retest_skipped;status={arm.status};note={arm.note};"
            f"zone={arm.zone_lo}-{arm.zone_hi};bars={arm.bars_waited}"
        )
        logger.info(
            "paper_position_retest_skipped",
            symbol=position.symbol,
            status=arm.status,
            note=arm.note,
            bars_waited=arm.bars_waited,
            closed_at=position.closed_at.isoformat() if position.closed_at else None,
        )

    async def backfill_from_signals(
        self,
        session: AsyncSession,
        *,
        since: datetime,
        dispatched_only: bool = False,
        one_per_symbol: bool = True,
        symbols: set[str] | None = None,
    ) -> PaperBackfillResult:
        """Qualifizierende Signale ab ``since`` als Paper-Trades nachziehen."""
        result = PaperBackfillResult()
        if not self.enabled:
            return result

        allowed = {s.upper() for s in symbols} if symbols else None

        with self._without_notifications():
            signals = await SignalRepository(session).list_since(
                since,
                actionable_only=True,
                dispatched_only=dispatched_only,
                limit=1000,
            )
            if not signals:
                return result

            asset_ids = list({signal.asset_id for signal in signals})
            symbols_by_id = await AssetRepository(session).get_symbols_by_ids(asset_ids)

            seen_symbols: set[str] = set()
            # one_per_symbol: neueste zuerst; sonst chronologisch (Retro-Replay).
            ordered = sorted(
                signals,
                key=lambda s: s.created_at,
                reverse=one_per_symbol,
            )

            for signal in ordered:
                result.considered += 1
                symbol = symbols_by_id.get(signal.asset_id)
                if not symbol:
                    result.skipped_filters += 1
                    continue
                symbol = symbol.upper()
                if allowed is not None and symbol not in allowed:
                    result.skipped_filters += 1
                    continue
                if one_per_symbol and symbol in seen_symbols:
                    result.skipped_filters += 1
                    continue
                if not self._passes_paper_gates(signal):
                    result.skipped_filters += 1
                    continue

                position = await self.open_from_stored_signal(
                    session, signal, symbol=symbol, extend_expiry=not self.retest_enabled
                )
                if position is None:
                    if self._last_skip_reason in PORTFOLIO_LIMIT_SKIPS:
                        result.skipped_limits += 1
                        continue
                    account = await self.get_or_create_account(session)
                    existing = await PaperRepository(session).get_active_by_symbol(
                        account.id, symbol
                    )
                    if existing is not None:
                        result.skipped_existing += 1
                    else:
                        result.skipped_cash += 1
                    continue

                seen_symbols.add(symbol)
                result.opened += 1
                if position.status == "pending":
                    result.pending += 1
                assert result.opened_symbols is not None
                result.opened_symbols.append(symbol)

        return result

    def _passes_paper_gates(self, signal: Signal) -> bool:
        try:
            direction = SignalDirection(signal.direction)
        except ValueError:
            return False
        if not direction.is_actionable:
            return False
        if self._settings.signal_require_strong and direction not in {
            SignalDirection.STRONG_LONG,
            SignalDirection.STRONG_SHORT,
        }:
            return False
        if direction.is_long and float(signal.score) < self._settings.signal_min_score:
            return False
        if direction.is_short and float(signal.score) > self._settings.signal_short_max_score:
            return False
        if direction.is_short and float(signal.score) <= self._settings.signal_short_min_score:
            return False
        if signal.stop_loss is None or signal.take_profit_1 is None:
            return False
        if signal.take_profit_2 is None or signal.take_profit_3 is None:
            return False
        rr = float(signal.risk_reward_ratio or 0.0)
        if rr < self._settings.min_risk_reward_ratio:
            return False
        if float(signal.data_quality) < 60.0:
            return False
        spec = self._settings.paper_entry_blackout_utc.strip()
        if spec and is_in_utc_blackout(ensure_utc(signal.created_at), spec):
            return False
        return True

    @staticmethod
    def _atr_stop(
        entry: float,
        atr: float,
        *,
        is_long: bool,
        atr_multiplier: float,
    ) -> float:
        dist = float(atr) * float(atr_multiplier)
        return entry - dist if is_long else entry + dist

    async def open_from_stored_signal(
        self,
        session: AsyncSession,
        signal: Signal,
        *,
        symbol: str,
        extend_expiry: bool = False,
        stop_loss_override: float | None = None,
    ) -> PaperPosition | None:
        """Paper-Position aus einem persistierten Signal oeffnen."""
        if not self.enabled:
            return None
        try:
            direction = SignalDirection(signal.direction)
        except ValueError:
            return None
        if not direction.is_actionable:
            return None
        if signal.stop_loss is None or signal.take_profit_1 is None:
            return None
        if signal.take_profit_2 is None or signal.take_profit_3 is None:
            return None

        entry_low = float(signal.entry_low or signal.reference_price)
        entry_high = float(signal.entry_high or signal.reference_price)
        entry_mid = (entry_low + entry_high) / 2.0
        # TPs/RR from zone edge (same reference live open / retest arm use).
        entry_ref = entry_low if direction.is_long else entry_high
        if entry_ref <= 0:
            entry_ref = entry_mid
        stop_loss = (
            float(stop_loss_override)
            if stop_loss_override is not None and stop_loss_override > 0
            else float(signal.stop_loss)
        )
        # Paper nutzt aktuelle TP-Multiples (Wide), nicht die historisch gespeicherten TPs.
        tp1, tp2, tp3 = RiskManager.targets_from_stop(
            entry_ref,
            stop_loss,
            is_long=direction.is_long,
            multipliers=self._tp_multipliers,
        )
        stop_distance = abs(entry_ref - stop_loss)
        rr = abs(tp2 - entry_ref) / stop_distance if stop_distance > 0 else 0.0
        risk = RiskParameters(
            entry_low=entry_low,
            entry_high=entry_high,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            take_profit_3=tp3,
            risk_reward_ratio=rr,
            risk_percent=float(signal.risk_percent or 0.0),
            suggested_position_size=float(signal.suggested_position_size or 0.0),
            stop_distance_percent=(stop_distance / entry_ref * 100.0) if entry_ref else 0.0,
            invalidation_note=signal.invalidation_note or "",
        )
        expires_at = signal.expires_at
        if extend_expiry:
            mult = int(self._settings.signal_expiry_multiplier)
            tf = signal.primary_timeframe or "1h"
            expires_at = ensure_utc(signal.created_at) + mult * timeframe_to_timedelta(tf)

        result = SignalResult(
            symbol=symbol.upper(),
            created_at=signal.created_at,
            expires_at=expires_at,
            direction=direction,
            score=float(signal.score),
            confidence=Confidence(signal.confidence),
            market_phase=MarketPhase(signal.market_phase),
            primary_timeframe=signal.primary_timeframe,
            analyzed_timeframes=[
                part.strip()
                for part in (signal.analyzed_timeframes or "").split(",")
                if part.strip()
            ],
            reference_price=float(signal.reference_price),
            data_quality=float(signal.data_quality),
            components=[],
            assessments={},
            risk=risk,
        )
        result.fingerprint = signal.fingerprint
        outcome = AnalysisOutcome(
            result=result,
            price_precision=8,
            signal_id=signal.id,
            asset_id=signal.asset_id,
        )
        return await self.open_from_signal(
            session, outcome, opened_at=signal.created_at
        )

    async def rebuild_from_signals(
        self,
        session: AsyncSession,
        *,
        since: datetime,
        provider,
        providers: list | None = None,
        dispatched_only: bool = False,
        one_per_symbol: bool = True,
        symbols: set[str] | None = None,
    ) -> PaperRebuildResult:
        """Paper-Ledger leeren, Retest-Entries aufloesen und per Kerzen replayen."""
        from app.scheduler.jobs import _collect_prices

        out = PaperRebuildResult()
        if not self.enabled:
            return out

        allowed = {s.upper() for s in symbols} if symbols else None

        account = await self.get_or_create_account(session)
        repo = PaperRepository(session)
        out.reset_positions = await repo.reset_ledger(account)

        with self._without_notifications():
            if not one_per_symbol:
                out.backfill = await self._rebuild_from_signal_stream(
                    session,
                    account,
                    provider,
                    since=since,
                    dispatched_only=dispatched_only,
                    out=out,
                    symbols=allowed,
                )
            else:
                out.backfill = await self.backfill_from_signals(
                    session,
                    since=since,
                    dispatched_only=dispatched_only,
                    one_per_symbol=one_per_symbol,
                    symbols=allowed,
                )
                if self.retest_enabled:
                    resolve = await self.resolve_pending_retest(
                        session, provider, end_time=utc_now(), historical=True
                    )
                    out.retest_filled = resolve.filled
                    out.retest_skipped = resolve.skipped
                    out.retest_still_pending = resolve.still_pending

                positions = await repo.list_positions(account.id)
                for position in positions:
                    if position.status != "open":
                        continue
                    try:
                        tf = position.timeframe or "1h"
                        series = await provider.get_candles(
                            position.symbol,
                            tf,
                            limit=100_000,
                            start_time=position.opened_at,
                            end_time=utc_now(),
                        )
                        await self._replay_bars(
                            session,
                            account,
                            position,
                            series.candles,
                            bar_minutes=timeframe_minutes(tf),
                        )
                        out.replayed += 1
                    except Exception as exc:
                        logger.warning(
                            "paper_rebuild_replay_failed",
                            symbol=position.symbol,
                            error=str(exc),
                        )

            still_open = await repo.list_open_positions(account.id)
            out.still_open = len(still_open)
            if still_open:
                symbols = [p.symbol for p in still_open]
                prices = await _collect_prices(provider, symbols, providers=providers)
                await self.update_open_positions(session, prices)
                still_open = await repo.list_open_positions(account.id)
                out.still_open = len(still_open)

        return out

    async def _rebuild_from_signal_stream(
        self,
        session: AsyncSession,
        account: PaperAccount,
        provider,
        *,
        since: datetime,
        dispatched_only: bool,
        out: PaperRebuildResult,
        symbols: set[str] | None = None,
    ) -> PaperBackfillResult:
        """Arm signals in created_at order; activate retest fills in fill_time order.

        Caps/cash must follow wall-clock fill order (live-realistic). Signal-order
        activate+replay under-counts concurrency when a later signal fills earlier.
        """
        backfill = PaperBackfillResult()
        allowed = {s.upper() for s in symbols} if symbols else None
        signals = await SignalRepository(session).list_since(
            since,
            actionable_only=True,
            dispatched_only=dispatched_only,
            limit=5000,
        )
        if not signals:
            return backfill

        asset_ids = list({signal.asset_id for signal in signals})
        symbols_by_id = await AssetRepository(session).get_symbols_by_ids(asset_ids)
        cfg = self._retest_config()
        lookback_pad = timedelta(days=14)
        cutoff = utc_now()
        ordered = sorted(signals, key=lambda s: s.created_at)
        # (fill_time, position, arm, mgmt_candles_start_hint)
        fill_jobs: list[tuple[datetime, PaperPosition, RetestArmResult, str]] = []

        for signal in ordered:
            backfill.considered += 1
            symbol = symbols_by_id.get(signal.asset_id)
            if not symbol:
                backfill.skipped_filters += 1
                continue
            symbol = symbol.upper()
            if allowed is not None and symbol not in allowed:
                backfill.skipped_filters += 1
                continue
            if not self._passes_paper_gates(signal):
                backfill.skipped_filters += 1
                continue

            repo = PaperRepository(session)
            if await repo.is_symbol_busy_at(
                account.id, symbol, ensure_utc(signal.created_at)
            ):
                backfill.skipped_existing += 1
                continue

            tf = signal.primary_timeframe or "1h"
            armed_at = ensure_utc(signal.created_at)
            candles: list = []
            stop_override: float | None = None
            try:
                series = await provider.get_candles(
                    symbol,
                    tf,
                    limit=100_000,
                    start_time=armed_at - lookback_pad,
                    end_time=cutoff,
                )
                candles = (
                    list(series.candles)
                    if series is not None and not series.is_empty
                    else []
                )
            except Exception as exc:
                logger.warning("paper_rebuild_candles_failed", symbol=symbol, error=str(exc))
                candles = []

            if candles:
                arm_idx = max(
                    (
                        i
                        for i, c in enumerate(candles)
                        if ensure_utc(c.open_time) <= armed_at
                    ),
                    default=None,
                )
                atr = wilder_atr(candles, arm_idx) if arm_idx is not None else None
                if atr and atr > 0:
                    try:
                        direction = SignalDirection(signal.direction)
                        entry_low = float(signal.entry_low or signal.reference_price)
                        entry_high = float(signal.entry_high or signal.reference_price)
                        entry_ref = entry_low if direction.is_long else entry_high
                        if entry_ref <= 0:
                            entry_ref = float(signal.reference_price)
                        stop_override = self._atr_stop(
                            entry_ref,
                            atr,
                            is_long=direction.is_long,
                            atr_multiplier=float(self._settings.atr_multiplier),
                        )
                    except (ValueError, TypeError):
                        stop_override = None

            position = await self.open_from_stored_signal(
                session,
                signal,
                symbol=symbol,
                extend_expiry=not self.retest_enabled,
                stop_loss_override=stop_override,
            )
            if position is None:
                if self._last_skip_reason in PORTFOLIO_LIMIT_SKIPS:
                    backfill.skipped_limits += 1
                else:
                    backfill.skipped_cash += 1
                continue

            backfill.opened += 1
            assert backfill.opened_symbols is not None
            if symbol not in backfill.opened_symbols:
                backfill.opened_symbols.append(symbol)

            tf = position.timeframe or tf

            if position.status != "pending":
                try:
                    await self._replay_bars(
                        session,
                        account,
                        position,
                        candles,
                        bar_minutes=timeframe_minutes(tf),
                    )
                    out.replayed += 1
                except Exception as exc:
                    logger.warning(
                        "paper_rebuild_replay_failed",
                        symbol=symbol,
                        error=str(exc),
                    )
                continue

            backfill.pending += 1

            if not candles:
                await self._cancel_pending_retest(
                    session,
                    position,
                    RetestArmResult(
                        status="skipped_no_history",
                        resolved_at=ensure_utc(position.opened_at),
                        note="no_candles",
                    ),
                )
                out.retest_skipped += 1
                continue

            arm = arm_retest_entry(
                direction=position.direction,
                arm_time=position.opened_at,
                reference_entry=float(position.entry_price),
                original_stop=float(position.stop_loss),
                timeframe=tf,
                candles=candles,
                config=cfg,
            )
            if (
                arm.filled
                and arm.fill_price is not None
                and arm.fill_time is not None
                and arm.stop is not None
            ):
                fill_jobs.append((ensure_utc(arm.fill_time), position, arm, symbol))
            elif arm.status == "pending":
                if position.expires_at is not None and cutoff >= ensure_utc(position.expires_at):
                    await self._cancel_pending_retest(session, position, arm)
                    out.retest_skipped += 1
                else:
                    out.retest_still_pending += 1
            else:
                await self._cancel_pending_retest(session, position, arm)
                out.retest_skipped += 1

        fill_jobs.sort(
            key=lambda job: (
                job[0],
                self._slot_priority(job[1]),
                int(job[1].id or 0),
            )
        )
        logger.info(
            "paper_rebuild_fill_candidates",
            n=len(fill_jobs),
            max_open=int(self._settings.paper_max_open_positions),
        )
        for fill_time, position, arm, symbol in fill_jobs:
            await session.refresh(account)
            ok = await self._activate_pending_retest(
                session, account, position, arm, regime_snapshot=None
            )
            if not ok:
                out.retest_skipped += 1
                continue
            out.retest_filled += 1
            await session.flush()
            try:
                mgmt_tf = position.timeframe or "1h"
                series_mgmt = await provider.get_candles(
                    symbol,
                    mgmt_tf,
                    limit=100_000,
                    start_time=position.opened_at,
                    end_time=cutoff,
                )
                await self._replay_bars(
                    session,
                    account,
                    position,
                    series_mgmt.candles,
                    bar_minutes=timeframe_minutes(mgmt_tf),
                )
                await session.flush()
                out.replayed += 1
            except Exception as exc:
                logger.warning(
                    "paper_rebuild_replay_failed",
                    symbol=symbol,
                    error=str(exc),
                )

        return backfill

    def _wick_cursor(self, position: PaperPosition, *, bar_minutes: int) -> datetime:
        """Exclusive lower bound for *closed* wick bars already applied.

        Unclosed/forming bars are never watermarked — they are reprocessed every
        poll until ``is_closed`` so final OHLC (and missed wicks) are not lost.
        """
        raw = _parse_note_kv(position.notes, "last_wick")
        if raw:
            try:
                return ensure_utc(datetime.fromisoformat(raw))
            except ValueError:
                pass
        opened = ensure_utc(position.opened_at) if position.opened_at is not None else utc_now()
        # Bars with open_time >= opened_at are eligible; micro-epsilon keeps ``>``.
        return opened - timedelta(microseconds=1)

    def _bar_is_closed(self, candle, *, bar_minutes: int, now: datetime) -> bool:
        if getattr(candle, "is_closed", None) is False:
            return False
        if getattr(candle, "is_closed", None) is True:
            close_time = getattr(candle, "close_time", None)
            if close_time is not None and ensure_utc(close_time) > now:
                return False
            return True
        close_time = getattr(candle, "close_time", None)
        if close_time is not None:
            return ensure_utc(close_time) <= now
        open_time = getattr(candle, "open_time", None)
        if open_time is None:
            return True
        return ensure_utc(open_time) + timedelta(minutes=bar_minutes) <= now

    def _skip_pre_fill_bar(self, candle, *, opened_at: datetime, bar_minutes: int) -> bool:
        """Skip OHLC that printed entirely or partially before the fill."""
        bar_open = ensure_utc(candle.open_time)
        close_time = getattr(candle, "close_time", None)
        bar_close = (
            ensure_utc(close_time)
            if close_time is not None
            else bar_open + timedelta(minutes=max(1, bar_minutes))
        )
        if bar_close <= opened_at:
            return True
        # Ambiguous fill bar: open before fill, close after — path unknown.
        if bar_open < opened_at < bar_close:
            return True
        return False

    async def _fetch_wick_bars(
        self,
        provider,
        position: PaperPosition,
        *,
        wick_timeframe: str,
    ) -> list:
        """Missed closed bars since watermark + current forming bar (reprocessed)."""
        bar_minutes = max(1, timeframe_minutes(wick_timeframe))
        cursor = self._wick_cursor(position, bar_minutes=bar_minutes)
        now = utc_now()
        span_minutes = max(bar_minutes, int((now - cursor).total_seconds() / 60) + bar_minutes)
        limit = min(500, max(2, span_minutes // bar_minutes + 2))
        series = await provider.get_candles(
            position.symbol.upper(),
            wick_timeframe,
            limit=limit,
            start_time=cursor,
            end_time=now,
            include_unclosed=True,
        )
        candles = list(series.candles) if series is not None else []
        bars: list = []
        for candle in candles:
            ot = ensure_utc(candle.open_time)
            closed = self._bar_is_closed(candle, bar_minutes=bar_minutes, now=now)
            if ot > cursor:
                bars.append(candle)
            elif not closed:
                # Reprocess forming bar even if previously seen partially.
                bars.append(candle)
        bars.sort(key=lambda c: ensure_utc(c.open_time))
        return bars

    async def _replay_bars(
        self,
        session: AsyncSession,
        account: PaperAccount,
        position: PaperPosition,
        bars,
        *,
        bar_minutes: int = 5,
    ) -> None:
        """OHLC-Replay: Stop hat Vorrang, danach TPs in Reihenfolge."""
        if not bars:
            return
        opened_at = (
            ensure_utc(position.opened_at) if position.opened_at is not None else None
        )
        for candle in bars:
            if position.status != "open":
                break
            if opened_at is not None and self._skip_pre_fill_bar(
                candle, opened_at=opened_at, bar_minutes=bar_minutes
            ):
                continue
            when = getattr(candle, "open_time", None) or getattr(candle, "timestamp", None)
            if when is None:
                when = utc_now()
            when = ensure_utc(when)
            high = float(candle.high)
            low = float(candle.low)
            close = float(candle.close)
            is_long = SignalDirection(position.direction).is_long
            stop = float(position.current_stop)

            stop_hit = low <= stop if is_long else high >= stop
            if stop_hit:
                slip_stop = self._slip_price(stop, is_long=is_long, side="exit")
                await self._close_remaining(
                    session,
                    account,
                    position,
                    price=slip_stop,
                    reason=ExitReason.STOP_LOSS,
                    when=when,
                )
                break

            self._update_peak_price(position, high if is_long else low)

            if await self._maybe_early_scratch(
                session, account, position, price=close, when=when
            ):
                break

            await self._apply_price(
                session,
                account,
                position,
                price=high if is_long else low,
                when=when,
                check_stop=False,
            )
            if position.status != "open":
                break

            if (
                position.expires_at is not None
                and when >= position.expires_at
                and position.remaining_quantity > 0
            ):
                slip_close = self._slip_price(close, is_long=is_long, side="exit")
                await self._close_remaining(
                    session,
                    account,
                    position,
                    price=slip_close,
                    reason=ExitReason.EXPIRED,
                    when=when,
                )
                break

    async def update_open_positions(
        self,
        session: AsyncSession,
        prices: dict[str, float],
        *,
        provider=None,
        wick_timeframe: str = "5m",
    ) -> list[PaperPosition]:
        """Offene Positionen gegen Preise pruefen (SL/TP Scale-out).

        Mit ``provider``: zuletzt geschlossene/aktuelle ``wick_timeframe``-Kerze
        als OHLC-Replay (Stop vor TP), danach Mark-Preis — schliesst Intrabar-
        Hits zwischen Polls.
        """
        if not self.enabled:
            return []

        account = await self.get_or_create_account(session)
        repo = PaperRepository(session)
        open_positions = await repo.list_open_positions(account.id)
        updated: list[PaperPosition] = []

        for position in open_positions:
            # Heal legacy BE stops that ignored round-trip fees.
            if (
                position.tp1_filled
                and self._settings.paper_move_stop_to_breakeven
                and position.status == "open"
            ):
                entry = float(position.entry_price)
                cur = float(position.current_stop or entry)
                if abs(cur - entry) < 1e-12 * max(entry, 1.0):
                    is_long = SignalDirection(position.direction).is_long
                    healed = RiskManager.fee_aware_breakeven(
                        entry,
                        is_long=is_long,
                        fee_percent=float(self._settings.paper_fee_percent),
                    )
                    if abs(healed - cur) > 1e-12:
                        position.current_stop = Decimal(str(healed))

            if position.status == "open":
                funded = await self._accrue_funding(
                    session, account, position, when=utc_now(), provider=provider
                )
                if funded:
                    updated.append(position)

            if provider is not None and position.status == "open":
                try:
                    bar_minutes = max(1, timeframe_minutes(wick_timeframe))
                    bars = await self._fetch_wick_bars(
                        provider, position, wick_timeframe=wick_timeframe
                    )
                    if bars:
                        await self._replay_bars(
                            session,
                            account,
                            position,
                            bars,
                            bar_minutes=bar_minutes,
                        )
                        now = utc_now()
                        closed = [
                            b
                            for b in bars
                            if self._bar_is_closed(
                                b, bar_minutes=bar_minutes, now=now
                            )
                        ]
                        # Only watermark closed bars — forming bars reprocess next poll.
                        if closed:
                            last_ot = getattr(closed[-1], "open_time", None)
                            if last_ot is not None:
                                position.notes = _set_note_kv(
                                    position.notes,
                                    "last_wick",
                                    ensure_utc(last_ot).isoformat(),
                                )
                        if position.status != "open":
                            updated.append(position)
                            continue
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "paper_wick_replay_failed",
                        symbol=position.symbol,
                        error=str(exc),
                    )

            price = prices.get(position.symbol.upper())
            if price is None:
                continue
            if position.status != "open":
                continue
            changed = await self._apply_price(session, account, position, float(price))
            if changed:
                updated.append(position)

        return updated

    async def _funding_rate_for(self, symbol: str, provider=None) -> float:
        """Live last funding rate when available, else configured default."""
        default = float(self._settings.paper_funding_rate_default)
        cache = getattr(self, "_funding_rate_cache", None)
        if cache is None:
            cache = {}
            self._funding_rate_cache = cache
        sym = symbol.upper()
        cached = cache.get(sym)
        if cached is not None:
            return cached
        rate = default
        try:
            from app.market_regime.sources import DerivativesClient

            client = DerivativesClient(self._settings)
            try:
                reading = await client.fetch_funding(sym, history_limit=1)
                if reading is not None and reading.rate is not None:
                    rate = float(reading.rate)
            finally:
                await client.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("paper_funding_rate_fallback", symbol=sym, error=str(exc))
            rate = default
        cache[sym] = rate
        return rate

    async def _accrue_funding(
        self,
        session: AsyncSession,
        account: PaperAccount,
        position: PaperPosition,
        *,
        when: datetime,
        provider=None,
    ) -> bool:
        """Charge Binance-style funding on open notional every N hours."""
        if not bool(self._settings.paper_funding_enabled):
            return False
        if position.status != "open" or position.remaining_quantity <= 0:
            return False
        hours = float(self._settings.paper_funding_interval_hours)
        if hours <= 0:
            return False
        interval = timedelta(hours=hours)
        when = ensure_utc(when)
        opened = (
            ensure_utc(position.opened_at) if position.opened_at is not None else when
        )
        raw_last = _parse_note_kv(position.notes, "last_funding")
        if raw_last:
            try:
                last = ensure_utc(datetime.fromisoformat(raw_last))
            except ValueError:
                last = opened
        else:
            last = opened

        changed = False
        rate = await self._funding_rate_for(position.symbol, provider)
        is_long = SignalDirection(position.direction).is_long
        # Positive rate: longs pay shorts. Negative: shorts pay longs.
        while last + interval <= when and position.status == "open":
            notional = self._remaining_notional(position)
            if notional <= 0:
                break
            payment = notional * Decimal(str(rate))
            # Long cash delta = -payment; short = +payment
            cash_delta = -payment if is_long else payment
            account.cash_balance += cash_delta
            account.realized_pnl += cash_delta
            position.realized_pnl += cash_delta
            position.fees += abs(payment)
            last = last + interval
            position.notes = _set_note_kv(
                position.notes, "last_funding", last.isoformat()
            )
            changed = True
            logger.info(
                "paper_funding_charged",
                symbol=position.symbol,
                rate=rate,
                notional=float(notional),
                cash_delta=float(cash_delta),
                at=last.isoformat(),
            )
        return changed

    async def summary(
        self, session: AsyncSession, prices: dict[str, float] | None = None
    ) -> PaperSummary:
        account = await self.get_or_create_account(session)
        repo = PaperRepository(session)
        open_positions = await repo.list_open_positions(account.id)
        pending_positions = await repo.list_pending_positions(account.id)
        closed = await repo.list_closed(account.id, limit=500)

        open_margin = sum((float(p.margin_used) for p in open_positions), 0.0)
        unrealized = 0.0
        if prices:
            for pos in open_positions:
                mark = prices.get(pos.symbol.upper())
                if mark is None:
                    continue
                direction = 1.0 if SignalDirection(pos.direction).is_long else -1.0
                unrealized += (
                    (mark - float(pos.entry_price))
                    * float(pos.remaining_quantity)
                    * direction
                )

        wins = [float(p.realized_pnl) for p in closed if float(p.realized_pnl) > 0]
        losses = [abs(float(p.realized_pnl)) for p in closed if float(p.realized_pnl) < 0]
        win_rate = (len(wins) / len(closed)) if closed else 0.0
        gross_profit = sum(wins)
        gross_loss = sum(losses)
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (0.0 if gross_profit == 0 else 99.0)

        risked = [p for p in closed if float(p.risk_amount) > 0]
        r_multiples = [float(p.realized_pnl) / float(p.risk_amount) for p in risked]
        total_r = sum(r_multiples)
        fees_r = sum(float(p.fees) / float(p.risk_amount) for p in risked)

        equity = float(account.cash_balance) + open_margin + unrealized
        return PaperSummary(
            cash_balance=float(account.cash_balance),
            initial_balance=float(account.initial_balance),
            realized_pnl=float(account.realized_pnl),
            open_positions=len(open_positions),
            open_margin=open_margin,
            equity=equity,
            win_rate=win_rate,
            closed_trades=len(closed),
            profit_factor=profit_factor,
            pending_positions=len(pending_positions),
            total_r=total_r,
            expectancy_r=(total_r / len(r_multiples)) if r_multiples else 0.0,
            fees_r=fees_r,
            r_trades=len(r_multiples),
        )

    async def build_digest(
        self,
        session: AsyncSession,
        prices: dict[str, float] | None = None,
        *,
        window: timedelta = timedelta(hours=1),
    ) -> PaperDigestSnapshot:
        """Snapshot fuer den stuendlichen Telegram-Paper-Digest."""
        now = utc_now()
        since_1h = now - window
        since_24h = now - timedelta(hours=24)
        since_7d = now - timedelta(days=7)
        since_30d = now - timedelta(days=30)
        account = await self.get_or_create_account(session)
        repo = PaperRepository(session)
        open_positions = await repo.list_open_positions(account.id)
        month_closed = await repo.list_closed_since(account.id, since_30d, limit=2000)
        week_closed = [
            pos
            for pos in month_closed
            if pos.closed_at is not None and pos.closed_at >= since_7d
        ]
        hour_closed = [
            pos
            for pos in month_closed
            if pos.closed_at is not None and pos.closed_at >= since_1h
        ]
        day_closed = [
            pos
            for pos in month_closed
            if pos.closed_at is not None and pos.closed_at >= since_24h
        ]
        hour_opened = await repo.count_opened_since(account.id, since_1h)
        day_opened = await repo.count_opened_since(account.id, since_24h)
        week_opened = await repo.count_opened_since(account.id, since_7d)
        month_opened = await repo.count_opened_since(account.id, since_30d)
        marks = {key.upper(): value for key, value in (prices or {}).items()}

        open_rows: list[PaperDigestOpenRow] = []
        total_upnl = 0.0
        total_upnl_r = 0.0
        for pos in open_positions:
            mark = marks.get(pos.symbol.upper())
            initial_qty = float(pos.initial_quantity) or 0.0
            remaining = float(pos.remaining_quantity)
            rem_pct = (remaining / initial_qty * 100.0) if initial_qty > 0 else 0.0
            upnl: float | None = None
            upnl_r: float | None = None
            if mark is not None:
                try:
                    direction = SignalDirection(pos.direction)
                    sign = 1.0 if direction.is_long else -1.0
                except ValueError:
                    sign = 1.0
                upnl = (mark - float(pos.entry_price)) * remaining * sign
                total_upnl += upnl
                risk = float(pos.risk_amount or 0.0)
                if risk > 0:
                    upnl_r = upnl / risk
                    total_upnl_r += upnl_r
            open_rows.append(
                PaperDigestOpenRow(
                    symbol=pos.symbol,
                    direction=pos.direction,
                    unrealized_usd=upnl,
                    unrealized_r=upnl_r,
                    mark=mark,
                    current_stop=float(pos.current_stop),
                    rem_pct=rem_pct,
                    tp1_filled=bool(pos.tp1_filled),
                    tp2_filled=bool(pos.tp2_filled),
                    tp3_filled=bool(pos.tp3_filled),
                )
            )
        open_rows.sort(
            key=lambda row: row.unrealized_r if row.unrealized_r is not None else float("-inf"),
            reverse=True,
        )

        close_rows: list[PaperDigestCloseRow] = []
        hour_pnl = 0.0
        hour_r = 0.0
        for pos in hour_closed:
            pnl = float(pos.realized_pnl)
            hour_pnl += pnl
            risk = float(pos.risk_amount or 0.0)
            realized_r = (pnl / risk) if risk > 0 else None
            if realized_r is not None:
                hour_r += realized_r
            close_rows.append(
                PaperDigestCloseRow(
                    symbol=pos.symbol,
                    direction=pos.direction,
                    realized_usd=pnl,
                    realized_r=realized_r,
                    exit_reason=pos.exit_reason,
                )
            )

        summary = await self.summary(session, prices=marks or None)
        initial = summary.initial_balance or 1.0
        equity_return_pct = ((summary.equity - initial) / initial) * 100.0

        from app.charts.paper_equity_chart import build_equity_curve_points

        fills = await repo.list_fills_for_account(account.id)
        fill_rows = [
            (fill.filled_at, float(fill.pnl), float(fill.fee)) for fill in fills
        ]
        start_at = getattr(account, "created_at", None) or now
        if fill_rows and fill_rows[0][0] < start_at:
            start_at = fill_rows[0][0]
        equity_curve = build_equity_curve_points(
            initial=float(summary.initial_balance),
            start_at=start_at,
            fills=fill_rows,
            as_of=now,
            live_equity=float(summary.equity),
        )

        windows = [
            self._digest_window_stats(
                "1h",
                hour_closed,
                opened_count=hour_opened,
                since=since_1h,
                live_equity=float(summary.equity),
                equity_curve=equity_curve,
            ),
            self._digest_window_stats(
                "24h",
                day_closed,
                opened_count=day_opened,
                since=since_24h,
                live_equity=float(summary.equity),
                equity_curve=equity_curve,
            ),
            self._digest_window_stats(
                "7d",
                week_closed,
                opened_count=week_opened,
                since=since_7d,
                live_equity=float(summary.equity),
                equity_curve=equity_curve,
            ),
            self._digest_window_stats(
                "30d",
                month_closed,
                opened_count=month_opened,
                since=since_30d,
                live_equity=float(summary.equity),
                equity_curve=equity_curve,
            ),
        ]

        return PaperDigestSnapshot(
            as_of=now,
            summary=summary,
            equity_return_pct=equity_return_pct,
            hour_closed_count=len(hour_closed),
            hour_closed_r=hour_r,
            hour_closed_pnl=hour_pnl,
            hour_opened_count=hour_opened,
            open_rows=open_rows,
            hour_closes=close_rows,
            total_open_upnl_usd=total_upnl,
            total_open_upnl_r=total_upnl_r,
            risk_per_trade=float(self._settings.paper_risk_per_trade_usd),
            leverage=float(self._settings.paper_leverage),
            max_notional=float(self._settings.paper_max_notional_usd),
            max_open=int(self._settings.paper_max_open_positions),
            equity_curve=equity_curve,
            windows=windows,
        )

    @staticmethod
    def _digest_window_stats(
        label: str,
        closed: list[PaperPosition],
        *,
        opened_count: int,
        since: datetime,
        live_equity: float,
        equity_curve: list[tuple[datetime, float]],
    ) -> PaperDigestWindowStats:
        closed_pnl = 0.0
        closed_r = 0.0
        win_count = 0
        for pos in closed:
            pnl = float(pos.realized_pnl)
            closed_pnl += pnl
            if pnl > 0:
                win_count += 1
            risk = float(pos.risk_amount or 0.0)
            if risk > 0:
                closed_r += pnl / risk
        return PaperDigestWindowStats(
            label=label,
            closed_count=len(closed),
            closed_pnl=closed_pnl,
            closed_r=closed_r,
            opened_count=opened_count,
            win_count=win_count,
            equity_delta=_equity_delta_since(equity_curve, since, live_equity),
        )

    async def _apply_price(
        self,
        session: AsyncSession,
        account: PaperAccount,
        position: PaperPosition,
        price: float,
        *,
        when=None,
        check_stop: bool = True,
    ) -> bool:
        is_long = SignalDirection(position.direction).is_long
        stop = float(position.current_stop)
        changed = False
        now = when or utc_now()

        self._update_peak_price(position, price)

        # Stop before early-scratch so mark polls match wick-path priority.
        if check_stop:
            stop_hit = price <= stop if is_long else price >= stop
            if stop_hit:
                slip_stop = self._slip_price(stop, is_long=is_long, side="exit")
                await self._close_remaining(
                    session,
                    account,
                    position,
                    price=slip_stop,
                    reason=ExitReason.STOP_LOSS,
                    when=now,
                )
                return True

        if await self._maybe_early_scratch(
            session, account, position, price=price, when=now
        ):
            return True

        scale = self._scale_out_fractions
        levels = (
            (not position.tp1_filled, float(position.take_profit_1), ExitReason.TAKE_PROFIT_1, scale[0], 1),
            (not position.tp2_filled, float(position.take_profit_2), ExitReason.TAKE_PROFIT_2, scale[1], 2),
            (not position.tp3_filled, float(position.take_profit_3), ExitReason.TAKE_PROFIT_3, scale[2], 3),
        )
        for pending, tp, reason, fraction, level in levels:
            if not pending:
                continue
            hit = price >= tp if is_long else price <= tp
            if not hit:
                break
            qty = min(
                Decimal(str(float(position.initial_quantity) * float(fraction))),
                position.remaining_quantity,
            )
            if level == 3:
                qty = position.remaining_quantity
            # TP limits fill at the level (no adverse slippage).
            await self._reduce(
                session, account, position, quantity=qty, price=tp, reason=reason, when=now
            )
            if level == 1:
                position.tp1_filled = True
                if self._settings.paper_move_stop_to_breakeven:
                    is_long = SignalDirection(position.direction).is_long
                    position.current_stop = Decimal(
                        str(
                            RiskManager.fee_aware_breakeven(
                                float(position.entry_price),
                                is_long=is_long,
                                fee_percent=float(self._settings.paper_fee_percent),
                            )
                        )
                    )
                extend_mult = int(self._settings.paper_expiry_multiplier_after_tp1)
                if extend_mult > 0 and position.opened_at is not None:
                    tf = position.timeframe or "1h"
                    position.expires_at = ensure_utc(position.opened_at) + extend_mult * timeframe_to_timedelta(
                        tf
                    )
            elif level == 2:
                position.tp2_filled = True
            else:
                position.tp3_filled = True
            changed = True
            if position.remaining_quantity <= Decimal("0"):
                break

        if (
            check_stop
            and position.status == "open"
            and position.expires_at is not None
            and now >= position.expires_at
            and position.remaining_quantity > 0
        ):
            slip_px = self._slip_price(price, is_long=is_long, side="exit")
            await self._close_remaining(
                session,
                account,
                position,
                price=slip_px,
                reason=ExitReason.EXPIRED,
                when=now,
            )
            return True

        return changed

    async def _reduce(
        self,
        session: AsyncSession,
        account: PaperAccount,
        position: PaperPosition,
        *,
        quantity: Decimal,
        price: float,
        reason: ExitReason,
        when,
    ) -> None:
        if quantity <= 0 or position.remaining_quantity <= 0:
            return

        remaining_before = position.remaining_quantity
        qty = min(quantity, remaining_before)
        direction = Decimal("1") if SignalDirection(position.direction).is_long else Decimal("-1")
        exit_price = Decimal(str(price))
        gross = (exit_price - position.entry_price) * qty * direction
        fee_rate = Decimal(str(self._settings.paper_fee_percent)) / Decimal("100")
        fee = exit_price * qty * fee_rate
        net = gross - fee

        # Margin proportional to closed share of *remaining* size — not of initial.
        # ``margin_used * qty/initial`` under-releases after prior scale-outs and
        # leaks cash when the last slice zeroes ``margin_used`` without payout.
        if remaining_before > 0 and position.margin_used > 0:
            margin_release = position.margin_used * (qty / remaining_before)
        else:
            margin_release = Decimal("0")

        position.remaining_quantity -= qty
        position.margin_used = max(Decimal("0"), position.margin_used - margin_release)
        position.realized_pnl += net
        position.fees += fee
        position.exit_reason = reason.value
        account.cash_balance += margin_release + net
        account.realized_pnl += net

        repo = PaperRepository(session)
        await repo.add_fill(
            PaperFill(
                position_id=position.id,
                reason=reason.value,
                price=exit_price,
                quantity=qty,
                fee=fee,
                pnl=net,
                filled_at=when,
            )
        )

        if position.remaining_quantity <= Decimal("0.00000001"):
            # Any dust margin must return to cash before zeroing the lock.
            if position.margin_used > 0:
                account.cash_balance += position.margin_used
                position.margin_used = Decimal("0")
            position.remaining_quantity = Decimal("0")
            position.status = "closed"
            position.closed_at = when
            logger.info(
                "paper_position_closed",
                symbol=position.symbol,
                reason=reason.value,
                pnl=float(position.realized_pnl),
            )

    async def _close_remaining(
        self,
        session: AsyncSession,
        account: PaperAccount,
        position: PaperPosition,
        *,
        price: float,
        reason: ExitReason,
        when,
    ) -> None:
        await self._reduce(
            session,
            account,
            position,
            quantity=position.remaining_quantity,
            price=price,
            reason=reason,
            when=when,
        )
