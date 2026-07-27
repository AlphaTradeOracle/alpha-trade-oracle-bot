"""Datenzugriff fuer Backtest-Laeufe, Trades und Kennzahlen."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import BacktestStatus
from app.core.time import utc_now
from app.models.backtest import BacktestMetric, BacktestRun, BacktestTrade


class BacktestRepository:
    """Backtest-Ergebnisse dauerhaft speichern und lesen."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_run(
        self,
        *,
        symbol: str,
        timeframe: str,
        start_at: datetime,
        end_at: datetime,
        initial_capital: float,
        fee_percent: float,
        slippage_percent: float,
        strategy_version_id: int | None = None,
        parameters: dict[str, object] | None = None,
    ) -> BacktestRun:
        run = BacktestRun(
            strategy_version_id=strategy_version_id,
            symbol=symbol.upper(),
            timeframe=timeframe,
            start_at=start_at,
            end_at=end_at,
            fee_percent=fee_percent,
            slippage_percent=slippage_percent,
            initial_capital=Decimal(str(initial_capital)),
            status=BacktestStatus.RUNNING.value,
            parameters=parameters,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def finish_run(
        self, run_id: int, *, status: BacktestStatus, error_message: str | None = None
    ) -> None:
        run = await self._session.get(BacktestRun, run_id)
        if run is None:
            return
        run.status = status.value
        run.error_message = error_message[:2000] if error_message else None
        run.finished_at = utc_now()

    async def add_trades(self, run_id: int, trades: list[dict[str, object]]) -> int:
        """Simulierte Trades speichern. Es wurde nie eine echte Order platziert."""
        if not trades:
            return 0
        self._session.add_all(
            [BacktestTrade(backtest_run_id=run_id, **trade) for trade in trades]  # type: ignore[arg-type]
        )
        await self._session.flush()
        return len(trades)

    async def add_metrics(self, run_id: int, metrics: dict[str, dict[str, float]]) -> int:
        """Kennzahlen je Auswertungsbereich speichern.

        ``metrics`` ist nach Scope gruppiert, z. B.
        ``{"overall": {"win_rate": 0.52}, "long": {...}}``.
        """
        rows: list[BacktestMetric] = []
        for scope, values in metrics.items():
            for name, value in values.items():
                if value is None:
                    continue
                rows.append(
                    BacktestMetric(
                        backtest_run_id=run_id,
                        scope=scope,
                        metric_name=name,
                        metric_value=Decimal(str(round(float(value), 8))),
                    )
                )
        if rows:
            self._session.add_all(rows)
            await self._session.flush()
        return len(rows)

    async def get_run(self, run_id: int) -> BacktestRun | None:
        return await self._session.get(BacktestRun, run_id)

    async def list_runs(self, *, symbol: str | None = None, limit: int = 20) -> list[BacktestRun]:
        statement = select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(limit)
        if symbol:
            statement = statement.where(BacktestRun.symbol == symbol.upper())
        result = await self._session.execute(statement)
        return list(result.scalars())

    async def get_metrics(self, run_id: int) -> dict[str, dict[str, float]]:
        result = await self._session.execute(
            select(BacktestMetric).where(BacktestMetric.backtest_run_id == run_id)
        )
        grouped: dict[str, dict[str, float]] = {}
        for metric in result.scalars():
            grouped.setdefault(metric.scope, {})[metric.metric_name] = (
                float(metric.metric_value) if metric.metric_value is not None else 0.0
            )
        return grouped

    async def get_trades(self, run_id: int, *, limit: int = 500) -> list[BacktestTrade]:
        result = await self._session.execute(
            select(BacktestTrade)
            .where(BacktestTrade.backtest_run_id == run_id)
            .order_by(BacktestTrade.entry_at)
            .limit(limit)
        )
        return list(result.scalars())

    async def compare_runs(self, run_id_a: int, run_id_b: int) -> dict[str, dict[str, float]]:
        """Zwei Laeufe anhand ihrer Gesamtkennzahlen vergleichen.

        Grundlage der Strategieversion-Bewertung: eine Kandidatenstrategie wird
        nur vorgeschlagen, wenn sie hier klar besser abschneidet.
        """
        metrics_a = (await self.get_metrics(run_id_a)).get("overall", {})
        metrics_b = (await self.get_metrics(run_id_b)).get("overall", {})

        comparison: dict[str, dict[str, float]] = {}
        for name in sorted(set(metrics_a) | set(metrics_b)):
            value_a = metrics_a.get(name, 0.0)
            value_b = metrics_b.get(name, 0.0)
            comparison[name] = {
                "run_a": value_a,
                "run_b": value_b,
                "delta": round(value_b - value_a, 6),
            }
        return comparison
