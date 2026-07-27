"""Grundstruktur der automatischen Kalibrierung.

Der Optimizer ist bewusst konservativ ausgelegt und **aktiviert niemals
selbstaendig eine Strategie**. Er erzeugt Kandidaten, vergleicht sie mit der
bestehenden Version und gibt eine Empfehlung ab. Die Freigabe erfolgt manuell.

Standardmaessig ist die Funktion ueber ``ENABLE_AUTO_CALIBRATION=false``
abgeschaltet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from app.backtesting.engine import BacktestConfig, BacktestEngine
from app.backtesting.metrics import compute_metrics
from app.core.errors import BacktestError
from app.core.logging import get_logger
from app.strategies.weights import StrategyWeights

logger = get_logger(__name__)

#: Ohne diese Mindestzahl an Trades ist ein Vergleich statistisch bedeutungslos.
MIN_TRADES_FOR_DECISION = 30

#: Relative Verbesserung, die eine Empfehlung rechtfertigt.
MIN_IMPROVEMENT = 0.15

#: Kennzahlen, die den Vergleich entscheiden — in dieser Prioritaet.
DECISION_METRICS = ("profit_factor", "expectancy", "sharpe_ratio")


@dataclass
class WalkForwardWindow:
    """Ein zeitlich getrenntes Fenster fuer Training, Validierung und Test."""

    train_start: datetime
    train_end: datetime
    validation_end: datetime
    test_end: datetime


@dataclass
class CandidateEvaluation:
    """Bewertung eines Gewichtungskandidaten."""

    weights: StrategyWeights
    metrics: dict[str, float]
    trade_count: int
    label: str = ""


@dataclass
class CalibrationReport:
    """Ergebnis eines Kalibrierungslaufs. Enthaelt nur eine Empfehlung."""

    baseline: CandidateEvaluation
    candidates: list[CandidateEvaluation] = field(default_factory=list)
    recommended: CandidateEvaluation | None = None
    rejection_reason: str | None = None

    @property
    def has_recommendation(self) -> bool:
        return self.recommended is not None


def build_walk_forward_windows(
    start: datetime, end: datetime, folds: int = 3
) -> list[WalkForwardWindow]:
    """Zeitraum in zeitlich getrennte Fenster teilen.

    Training, Validierung und Test ueberlappen nie. Innerhalb eines Fensters
    liegt der Testabschnitt immer *nach* dem Trainingsabschnitt — dieselbe
    Bedingung, die auch Look-ahead-Bias im Backtest verhindert.
    """
    if folds < 1:
        raise ValueError("Es wird mindestens ein Fold benoetigt")
    if end <= start:
        raise ValueError("Das Ende muss nach dem Start liegen")

    total = (end - start) / folds
    windows: list[WalkForwardWindow] = []

    for fold in range(folds):
        fold_start = start + total * fold
        fold_end = fold_start + total
        train_end = fold_start + total * 0.6
        validation_end = fold_start + total * 0.8
        windows.append(
            WalkForwardWindow(
                train_start=fold_start,
                train_end=train_end,
                validation_end=validation_end,
                test_end=fold_end,
            )
        )
    return windows


def generate_weight_candidates(
    base: StrategyWeights, *, step: float = 0.05
) -> list[StrategyWeights]:
    """Kandidaten durch Verschieben von Gewicht zwischen zwei Kategorien erzeugen.

    Es wird immer paarweise verschoben, damit die Summe exakt 1.0 bleibt.
    Ungueltige Kombinationen werden von ``StrategyWeights`` verworfen.
    """
    fields = ("trend", "momentum", "volume", "market_structure", "multi_timeframe")
    candidates: list[StrategyWeights] = []

    for giver in fields:
        for taker in fields:
            if giver == taker:
                continue
            giver_value = getattr(base, giver)
            if giver_value - step < 0.0:
                continue
            payload = base.model_dump()
            payload[giver] = round(giver_value - step, 6)
            payload[taker] = round(getattr(base, taker) + step, 6)
            try:
                candidates.append(StrategyWeights(**payload))
            except ValueError:
                continue

    return candidates


def evaluate_candidates(
    df: pd.DataFrame,
    base_config: BacktestConfig,
    candidates: list[StrategyWeights],
) -> CalibrationReport:
    """Basisgewichtung und Kandidaten auf denselben Daten vergleichen."""
    baseline = _evaluate(df, base_config, base_config.weights, label="baseline")
    report = CalibrationReport(baseline=baseline)

    if baseline.trade_count < MIN_TRADES_FOR_DECISION:
        report.rejection_reason = (
            f"Die Basisstrategie erzeugt nur {baseline.trade_count} Trades, "
            f"mindestens {MIN_TRADES_FOR_DECISION} sind fuer eine Entscheidung erforderlich."
        )
        return report

    for weights in candidates:
        report.candidates.append(_evaluate(df, base_config, weights))

    report.recommended, report.rejection_reason = _select_recommendation(report)
    if report.recommended is not None:
        logger.info(
            "calibration_candidate_recommended",
            symbol=base_config.symbol,
            profit_factor=report.recommended.metrics.get("profit_factor"),
            baseline_profit_factor=baseline.metrics.get("profit_factor"),
        )
    return report


def _evaluate(
    df: pd.DataFrame,
    base_config: BacktestConfig,
    weights: StrategyWeights,
    *,
    label: str = "candidate",
) -> CandidateEvaluation:
    config = BacktestConfig(
        symbol=base_config.symbol,
        timeframe=base_config.timeframe,
        fee_percent=base_config.fee_percent,
        slippage_percent=base_config.slippage_percent,
        initial_capital=base_config.initial_capital,
        min_score=base_config.min_score,
        min_risk_reward_ratio=base_config.min_risk_reward_ratio,
        atr_multiplier=base_config.atr_multiplier,
        max_atr_percent=base_config.max_atr_percent,
        expiry_multiplier=base_config.expiry_multiplier,
        weights=weights,
    )
    try:
        outcome = BacktestEngine(config).run(df)
    except BacktestError as exc:
        logger.warning("calibration_candidate_failed", error=str(exc))
        return CandidateEvaluation(weights=weights, metrics={}, trade_count=0, label=label)

    metrics = compute_metrics(outcome).get("overall", {})
    return CandidateEvaluation(
        weights=weights,
        metrics=metrics,
        trade_count=int(metrics.get("trade_count", 0)),
        label=label,
    )


def _select_recommendation(
    report: CalibrationReport,
) -> tuple[CandidateEvaluation | None, str | None]:
    """Nur bei klarer Verbesserung in allen Entscheidungskennzahlen empfehlen."""
    baseline = report.baseline
    best: CandidateEvaluation | None = None
    best_gain = 0.0

    for candidate in report.candidates:
        if candidate.trade_count < MIN_TRADES_FOR_DECISION:
            continue

        gains: list[float] = []
        for metric in DECISION_METRICS:
            base_value = baseline.metrics.get(metric, 0.0)
            candidate_value = candidate.metrics.get(metric, 0.0)
            if base_value <= 0:
                # Ohne belastbare Basis wird nicht verglichen.
                gains.append(0.0)
                continue
            gains.append((candidate_value - base_value) / abs(base_value))

        # Jede Entscheidungskennzahl muss sich verbessern, keine darf schlechter werden.
        if any(gain <= 0 for gain in gains):
            continue
        average_gain = sum(gains) / len(gains)
        if average_gain >= MIN_IMPROVEMENT and average_gain > best_gain:
            best = candidate
            best_gain = average_gain

    if best is None:
        return None, (
            "Kein Kandidat verbessert alle Entscheidungskennzahlen "
            f"({', '.join(DECISION_METRICS)}) um mindestens "
            f"{MIN_IMPROVEMENT * 100:.0f}%."
        )
    return best, None
