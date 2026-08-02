"""Kennzahlen eines Backtests.

Alle Funktionen sind rein und arbeiten auf der Liste der simulierten Trades.
Kennzahlen, die sich mit den vorhandenen Daten nicht berechnen lassen (z. B.
Sharpe Ratio bei weniger als zwei Trades), werden als ``0.0`` ausgegeben und
nicht geschaetzt.
"""

from __future__ import annotations

import math

from app.backtesting.engine import BacktestOutcome, SimulatedTrade
from app.core.time import timeframe_minutes

#: Handelsperioden pro Jahr je Timeframe — Basis der Annualisierung von
#: Sharpe und Sortino Ratio.
MINUTES_PER_YEAR = 365 * 24 * 60


def compute_metrics(outcome: BacktestOutcome) -> dict[str, dict[str, float]]:
    """Alle Kennzahlen gruppiert nach Auswertungsbereich berechnen.

    Rueckgabe z. B.
    ``{"overall": {...}, "long": {...}, "short": {...}, "symbol:BTCUSDT": {...}}``.
    """
    closed = [trade for trade in outcome.trades if trade.is_closed]

    grouped: dict[str, dict[str, float]] = {
        "overall": _metrics_for(closed, outcome),
    }

    longs = [t for t in closed if t.direction.is_long]
    shorts = [t for t in closed if t.direction.is_short]
    if longs:
        grouped["long"] = _metrics_for(longs, outcome)
    if shorts:
        grouped["short"] = _metrics_for(shorts, outcome)

    grouped[f"symbol:{outcome.config.symbol}"] = _metrics_for(closed, outcome)
    grouped[f"timeframe:{outcome.config.timeframe}"] = _metrics_for(closed, outcome)

    return grouped


def _metrics_for(trades: list[SimulatedTrade], outcome: BacktestOutcome) -> dict[str, float]:
    if not trades:
        return {
            "trade_count": 0.0,
            "win_rate": 0.0,
            "net_profit": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "max_drawdown": 0.0,
            "max_drawdown_percent": 0.0,
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "average_win": 0.0,
            "average_loss": 0.0,
            "average_risk_reward": 0.0,
            "total_fees": 0.0,
            "average_holding_minutes": 0.0,
        }

    wins = [trade for trade in trades if trade.net_pnl > 0]
    losses = [trade for trade in trades if trade.net_pnl < 0]

    gross_profit = sum(trade.net_pnl for trade in wins)
    gross_loss = abs(sum(trade.net_pnl for trade in losses))
    net_profit = sum(trade.net_pnl for trade in trades)

    win_rate = len(wins) / len(trades)
    average_win = gross_profit / len(wins) if wins else 0.0
    average_loss = gross_loss / len(losses) if losses else 0.0

    # Ohne Verluste waere PF unendlich — Sentinel 99 wie Paper-Summary,
    # damit Rankings All-Win-Symbole nicht als PF=0 verwerfen.
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = 99.0
    else:
        profit_factor = 0.0

    # Expectancy: erwarteter Gewinn pro Trade.
    expectancy = win_rate * average_win - (1.0 - win_rate) * average_loss

    equity = _equity_curve(trades, outcome.config.initial_capital)
    max_drawdown, max_drawdown_percent = _max_drawdown(equity)

    returns = [
        trade.net_pnl / outcome.config.initial_capital
        for trade in trades
        if outcome.config.initial_capital > 0
    ]
    periods = _periods_per_year(outcome.config.timeframe, trades)

    return {
        "trade_count": float(len(trades)),
        "win_count": float(len(wins)),
        "loss_count": float(len(losses)),
        "win_rate": round(win_rate, 6),
        "net_profit": round(net_profit, 8),
        "net_profit_percent": round(
            net_profit / outcome.config.initial_capital * 100.0
            if outcome.config.initial_capital > 0
            else 0.0,
            6,
        ),
        "gross_profit": round(gross_profit, 8),
        "gross_loss": round(gross_loss, 8),
        "profit_factor": round(profit_factor, 6),
        "expectancy": round(expectancy, 8),
        "average_win": round(average_win, 8),
        "average_loss": round(average_loss, 8),
        "average_risk_reward": round(
            sum(trade.risk_reward_planned for trade in trades) / len(trades), 6
        ),
        "max_drawdown": round(max_drawdown, 8),
        "max_drawdown_percent": round(max_drawdown_percent, 6),
        "sharpe_ratio": round(_sharpe_ratio(returns, periods), 6),
        "sortino_ratio": round(_sortino_ratio(returns, periods), 6),
        "total_fees": round(sum(trade.fees for trade in trades), 8),
        "average_holding_minutes": round(
            sum(trade.holding_minutes for trade in trades) / len(trades), 2
        ),
        "stop_loss_exits": float(
            sum(1 for t in trades if t.exit_reason and t.exit_reason.value == "stop_loss")
        ),
        "expired_exits": float(
            sum(1 for t in trades if t.exit_reason and t.exit_reason.value == "expired")
        ),
    }


def _equity_curve(trades: list[SimulatedTrade], initial_capital: float) -> list[float]:
    equity = [initial_capital]
    running = initial_capital
    for trade in sorted(trades, key=lambda t: t.exit_at or t.entry_at):
        running += trade.net_pnl
        equity.append(running)
    return equity


def _max_drawdown(equity: list[float]) -> tuple[float, float]:
    """Groesster absoluter und relativer Rueckgang vom bisherigen Hoch."""
    if len(equity) < 2:
        return 0.0, 0.0

    peak = equity[0]
    max_absolute = 0.0
    max_relative = 0.0

    for value in equity:
        peak = max(peak, value)
        drawdown = peak - value
        max_absolute = max(max_absolute, drawdown)
        if peak > 0:
            max_relative = max(max_relative, drawdown / peak * 100.0)

    return max_absolute, max_relative


def _sharpe_ratio(returns: list[float], periods_per_year: float) -> float:
    """Annualisierte Sharpe Ratio bei risikofreiem Zins von 0."""
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    return mean / std * math.sqrt(periods_per_year)


def _sortino_ratio(returns: list[float], periods_per_year: float) -> float:
    """Wie Sharpe, aber nur Abwaertsvolatilitaet im Nenner."""
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    downside = [r for r in returns if r < 0]
    if not downside:
        return 0.0
    variance = sum(r**2 for r in downside) / len(downside)
    deviation = math.sqrt(variance)
    if deviation == 0:
        return 0.0
    return mean / deviation * math.sqrt(periods_per_year)


def _periods_per_year(timeframe: str, trades: list[SimulatedTrade]) -> float:
    """Annualisierungsfaktor aus der durchschnittlichen Haltedauer ableiten.

    Die Skalierung ueber die tatsaechliche Haltedauer ist aussagekraeftiger als
    die reine Timeframe-Laenge, weil ein Trade meist mehrere Kerzen offen ist.
    """
    if trades:
        average_holding = sum(trade.holding_minutes for trade in trades) / len(trades)
        if average_holding > 0:
            return MINUTES_PER_YEAR / average_holding
    try:
        return MINUTES_PER_YEAR / timeframe_minutes(timeframe)
    except ValueError:
        return 252.0


def summarize_for_display(metrics: dict[str, float], direction_label: str = "Gesamt") -> list[str]:
    """Kennzahlen als lesbare Zeilen fuer CLI und Telegram."""
    return [
        f"{direction_label}:",
        f"  Trades: {int(metrics.get('trade_count', 0))}",
        f"  Trefferquote: {metrics.get('win_rate', 0.0) * 100:.1f}%",
        f"  Nettoergebnis: {metrics.get('net_profit', 0.0):.2f} "
        f"({metrics.get('net_profit_percent', 0.0):+.2f}%)",
        f"  Profit Factor: {metrics.get('profit_factor', 0.0):.2f}",
        f"  Expectancy: {metrics.get('expectancy', 0.0):.2f}",
        f"  Max. Drawdown: {metrics.get('max_drawdown_percent', 0.0):.2f}%",
        f"  Sharpe: {metrics.get('sharpe_ratio', 0.0):.2f} | "
        f"Sortino: {metrics.get('sortino_ratio', 0.0):.2f}",
        f"  Durchschn. R:R: {metrics.get('average_risk_reward', 0.0):.2f}",
        f"  Gebuehren: {metrics.get('total_fees', 0.0):.2f}",
        f"  Durchschn. Haltedauer: {metrics.get('average_holding_minutes', 0.0):.0f} Minuten",
    ]
