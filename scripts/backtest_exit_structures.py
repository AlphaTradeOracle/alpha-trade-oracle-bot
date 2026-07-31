"""Exit-Strukturen auf tiefer Historie backtesten — Tail-Harvesting vs. Contrarian.

Warum es dieses Skript gibt
---------------------------
Die Score-Edge-Analyse (``scripts/analyze_deep_edge.py``) hat gezeigt: der Score
ist negativ praediktiv, aber im obersten Quintil ist der MEDIAN der 24h-Rendite
negativ (-1.23%) und der MITTELWERT positiv (+0.16%, bei 72h +0.63%). Der Score
selektiert also rechtsschiefe Setups — sie verlieren meistens und gewinnen
selten gross. Ob daraus ein handelbarer Edge wird, entscheidet allein die
Exit-Struktur: eine enge TP-Leiter schneidet genau den Tail ab, der den
Erwartungswert traegt.

Zwei Hypothesen werden gegeneinander getestet:

* **A — Tail-Harvesting**: kein TP / sehr weites TP / Trailing-Stop / laengere
  Haltedauer / weitere Stops. Der Tail soll laufen duerfen.
* **B — Contrarian**: das Signal invertieren. Naheliegend bei negativem IC, aber
  gefaehrlich: die Inversion einer rechtsschiefen Verliererverteilung ist eine
  LINKSschiefe Gewinnerverteilung — viele kleine Gewinne, selten ein
  katastrophaler Verlust. Deshalb werden Schiefe, schlechtester Einzeltrade,
  Tail-Ratio und maximaler Drawdown fuer jede Variante mit ausgewiesen.

Methodik — was dieses Skript NICHT tut
--------------------------------------
Es ist **kein** Filter ueber bereits geschlossene Trades. Genau dieser Fehler
liess ADX>=35 in der Simulation gut aussehen (PF 1.30) und in der Realitaet
scheitern (PF 0.41). Stattdessen wird jedes Signal vollstaendig replayed:

1. **Level-Rekonstruktion** — Entry-Zone, Stop und TPs werden mit derselben
   Logik wie ``app.signals.risk.RiskManager`` aus Referenzpreis, ATR und
   Marktstruktur (naechster Support/Widerstand) gebildet. Fuer die
   Originalrichtung wird gegen den vom Produktionscode gelieferten Stop
   geprueft (``--parity-check``).
2. **Retest-Arming** — identisch zu ``app.signals.retest_entry.arm_retest_entry``
   (Zone 0.35–1.0 ATR, Fill zum unguenstigsten in der Zone gehandelten Preis,
   Abbruch bei SL-Treffer oder Ablauf des Pending-Fensters).
3. **Exit-Replay Kerze fuer Kerze** — identisch zu
   ``PaperTradingService._replay_bars``: Stop hat Vorrang vor TP, TPs in
   Reihenfolge, Expiry auf Schlusskurs.
4. **Ein Trade je Symbol gleichzeitig** — wie live ueber
   ``get_active_by_symbol``. Ohne diese Sperre wuerde dieselbe Bewegung
   mehrfach gezaehlt.

Alles wird in **R-Multiples** gerechnet. Das ist exakt, weil das Sizing
risikonormiert ist (``PAPER_RISK_PER_TRADE_USD``): die Stueckzahl ist
``Risikobudget / Stop-Abstand``, damit ist ``PnL / risk_amount`` unabhaengig vom
Kapital. Auch die Gebuehren sind in R exakt darstellbar:
``Gebuehr_R = Gebuehrensatz / Stop-Abstand-in-Prozent`` — enge Stops sind also
teurer, und das faellt hier korrekt ins Gewicht.

Aufruf
------
    # Hauptlauf: 1h-Panel, beide Polaritaeten, alle Varianten
    python scripts/backtest_exit_structures.py \
        --signals exports/edge_data/regen_signals_live_risk.csv.gz \
        --timeframe 1h --out exports/exit_structures_live.json

    # Unabhaengige Replikation auf dem 4h-Panel ueber 366 Tage
    python scripts/backtest_exit_structures.py \
        --signals exports/edge_data/regen_signals_long_risk.csv.gz \
        --timeframe 4h --out exports/exit_structures_long.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.time import TIMEFRAME_MINUTES  # noqa: E402
from app.signals.risk import ENTRY_ZONE_ATR_FRACTION, LEVEL_BUFFER_PERCENT  # noqa: E402
from app.signals.risk import LEVEL_RELEVANCE_ATR  # noqa: E402
from scripts.regenerate_historical_signals import load_candle_arrays  # noqa: E402

DATA_DIR = REPO_ROOT / "exports" / "edge_data"

#: Live-Werte (app/core/config.py, Stand cd607c5).
FEE_PERCENT = 0.05
RETEST_ZONE_NEAR = 0.35
RETEST_ZONE_FAR = 1.0
RETEST_PENDING_MULT = 4
EXPIRY_MULT = 24
ATR_PERIOD = 14
SCALE_OUT = (0.5, 0.25, 0.25)
LEGACY_SCALE_OUT = (0.33333333, 0.33333333, 0.33333334)
BASE_TP_MULTIPLIERS = (2.0, 4.0, 6.0)
LEGACY_TP_MULTIPLIERS = (2.0, 4.0, 6.0)
BASE_ATR_MULT = 1.5
MIN_STOP_PERCENT = 0.3
MAX_STOP_PERCENT = 8.0
MIN_DATA_QUALITY = 60.0
MIN_RR = 2.0

NS_PER_HOUR = 3_600_000_000_000

STRONG = ("STRONG_LONG", "STRONG_SHORT")
ACTIONABLE = ("STRONG_LONG", "STRONG_SHORT", "LONG", "SHORT")


# ---------------------------------------------------------------------------
# Varianten
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExitVariant:
    """Eine Exit-Struktur. ``None`` bei ``tp_multipliers`` heisst: kein TP."""

    key: str
    label: str
    hypothesis: str
    hold_hours: float
    tp_multipliers: tuple[float, ...] | None = BASE_TP_MULTIPLIERS
    scale: tuple[float, ...] = SCALE_OUT
    move_be: bool = True
    stop_atr_mult: float = BASE_ATR_MULT
    #: Chandelier-Trailing: Stop = Extrem seit Entry -/+ ``trail_atr`` x ATR,
    #: aktiv erst ab ``trail_arm_r`` R Buchgewinn. ``None`` = kein Trailing.
    trail_atr: float | None = None
    trail_arm_r: float = 1.0

    @property
    def n_scale_events(self) -> int:
        return 0 if self.tp_multipliers is None else len(self.tp_multipliers)


def build_variants(expiry_hours: float) -> list[ExitVariant]:
    """Variantenkatalog. ``expiry_hours`` ist die Live-Haltedauer (24 x TF)."""
    v: list[ExitVariant] = [
        ExitVariant(
            key="baseline_live",
            label="Baseline live (TP 2/4/6R, 33/33/34, BE nach TP1)",
            hypothesis="baseline",
            hold_hours=expiry_hours,
            tp_multipliers=LEGACY_TP_MULTIPLIERS,
            scale=LEGACY_SCALE_OUT,
        ),
        ExitVariant(
            key="ladder_123_502525",
            label="TP 1/2/3R, Scale-out 50/25/25, BE nach TP1",
            hypothesis="baseline",
            hold_hours=expiry_hours,
            tp_multipliers=(1.0, 2.0, 3.0),
            scale=(0.5, 0.25, 0.25),
        ),
        ExitVariant(
            key="ladder_1535_502525",
            label="TP 1.5/3/5R, Scale-out 50/25/25, BE nach TP1",
            hypothesis="baseline",
            hold_hours=expiry_hours,
            tp_multipliers=(1.5, 3.0, 5.0),
            scale=(0.5, 0.25, 0.25),
        ),
        ExitVariant(
            key="baseline_no_be",
            label="Baseline ohne Break-Even-Shift",
            hypothesis="baseline",
            hold_hours=expiry_hours,
            tp_multipliers=LEGACY_TP_MULTIPLIERS,
            scale=LEGACY_SCALE_OUT,
            move_be=False,
        ),
    ]

    # --- A: Tail-Harvesting ------------------------------------------------
    for hours in (24.0, 48.0, 72.0, 120.0):
        v.append(
            ExitVariant(
                key=f"A_no_tp_{int(hours)}h",
                label=f"Kein TP, Exit nur Stop oder Zeit ({int(hours)}h)",
                hypothesis="A",
                hold_hours=hours,
                tp_multipliers=None,
                move_be=False,
            )
        )
    for mult in (5.0, 8.0, 10.0):
        v.append(
            ExitVariant(
                key=f"A_tp{int(mult)}r_72h",
                label=f"Einzelnes TP bei {int(mult)}R, kein Scale-out (72h)",
                hypothesis="A",
                hold_hours=72.0,
                tp_multipliers=(mult,),
                scale=(1.0,),
                move_be=False,
            )
        )
    v.append(
        ExitVariant(
            key="A_tp6r_single_72h",
            label="Einzelnes TP bei 6R statt Leiter (72h)",
            hypothesis="A",
            hold_hours=72.0,
            tp_multipliers=(6.0,),
            scale=(1.0,),
            move_be=False,
        )
    )
    for atr in (2.0, 3.0):
        v.append(
            ExitVariant(
                key=f"A_trail_atr{int(atr)}_72h",
                label=f"Chandelier-Trailing {atr:.0f}x ATR ab +1R, kein TP (72h)",
                hypothesis="A",
                hold_hours=72.0,
                tp_multipliers=None,
                move_be=False,
                trail_atr=atr,
                trail_arm_r=1.0,
            )
        )
    for atr_mult in (2.5, 3.0):
        v.append(
            ExitVariant(
                key=f"A_wide_stop_{str(atr_mult).replace('.', '')}atr_72h",
                label=f"Weiter Stop {atr_mult}x ATR, kein TP (72h)",
                hypothesis="A",
                hold_hours=72.0,
                tp_multipliers=None,
                move_be=False,
                stop_atr_mult=atr_mult,
            )
        )
    v.append(
        ExitVariant(
            key="A_wide_stop_25atr_tp8r_120h",
            label="Weiter Stop 2.5x ATR + TP 8R (120h)",
            hypothesis="A",
            hold_hours=120.0,
            tp_multipliers=(8.0,),
            scale=(1.0,),
            move_be=False,
            stop_atr_mult=2.5,
        )
    )
    v.append(
        ExitVariant(
            key="A_ladder_4_8_12r_72h",
            label="Weite Leiter 4/8/12R (72h)",
            hypothesis="A",
            hold_hours=72.0,
            tp_multipliers=(4.0, 8.0, 12.0),
            move_be=False,
        )
    )
    return v


# ---------------------------------------------------------------------------
# Level-Rekonstruktion (Spiegel von app.signals.risk.RiskManager)
# ---------------------------------------------------------------------------


def entry_reference(price: float, atr: float, *, is_long: bool) -> float:
    offset = atr * ENTRY_ZONE_ATR_FRACTION
    return price - offset if is_long else price + offset


def structure_stop(
    entry: float,
    atr: float,
    *,
    is_long: bool,
    nearest_support: float | None,
    nearest_resistance: float | None,
    atr_multiplier: float,
) -> float:
    """``RiskManager._stop_loss`` — ATR-Stop, an Support/Widerstand ausgerichtet."""
    atr_stop = entry - atr * atr_multiplier if is_long else entry + atr * atr_multiplier
    buffer_down = 1.0 - LEVEL_BUFFER_PERCENT / 100.0
    buffer_up = 1.0 + LEVEL_BUFFER_PERCENT / 100.0

    if is_long and nearest_support is not None and not np.isnan(nearest_support):
        if nearest_support < entry and (entry - nearest_support) <= atr * LEVEL_RELEVANCE_ATR:
            candidate = nearest_support * buffer_down
            if candidate < atr_stop:
                return candidate
    if not is_long and nearest_resistance is not None and not np.isnan(nearest_resistance):
        if nearest_resistance > entry and (nearest_resistance - entry) <= atr * LEVEL_RELEVANCE_ATR:
            candidate = nearest_resistance * buffer_up
            if candidate > atr_stop:
                return candidate
    return atr_stop


def enforce_stop_bounds(entry: float, stop: float, *, is_long: bool) -> float:
    """``RiskManager._enforce_stop_bounds`` — zu enge Stops aufweiten.

    Zu weite Stops werden live nur gekennzeichnet, nicht verschoben.
    """
    distance = abs(entry - stop)
    if entry <= 0 or distance <= 0:
        return stop
    percent = distance / entry * 100.0
    if percent < MIN_STOP_PERCENT:
        required = entry * MIN_STOP_PERCENT / 100.0
        return entry - required if is_long else entry + required
    return stop


def levels_for_direction(
    *,
    reference_price: float,
    atr: float,
    nearest_support: float | None,
    nearest_resistance: float | None,
    is_long: bool,
    atr_multiplier: float,
) -> tuple[float, float] | None:
    """Referenz-Entry (Zonenmitte) und Stop fuer eine Richtung.

    Der Retest ankert am Zonen-MITTELPUNKT (``risk.entry_mid``, identisch zum
    Referenzpreis), der Stop dagegen am Zonenrand — genau wie im Live-Code.
    """
    if reference_price <= 0 or atr <= 0 or np.isnan(atr):
        return None
    edge = entry_reference(reference_price, atr, is_long=is_long)
    stop = structure_stop(
        edge,
        atr,
        is_long=is_long,
        nearest_support=nearest_support,
        nearest_resistance=nearest_resistance,
        atr_multiplier=atr_multiplier,
    )
    stop = enforce_stop_bounds(edge, stop, is_long=is_long)
    if (is_long and stop >= reference_price) or (not is_long and stop <= reference_price):
        return None
    return reference_price, stop


# ---------------------------------------------------------------------------
# Kerzen + Wilder-ATR
# ---------------------------------------------------------------------------


def wilder_atr_series(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Wilder-ATR je Bar, kompatibel zu ``retest_entry.wilder_atr``.

    Dort wird der ATR bei jedem Aufruf aus dem gesamten Praefix neu gerechnet
    (Seed = Mittel der ersten ``period`` True Ranges, danach rekursiv). Eine
    einmal berechnete Serie liefert denselben Wert an jeder Stelle. Live laeuft
    die Berechnung auf einem 14-Tage-Fenster; nach >300 Bars ist der Einfluss
    des Seeds numerisch verschwunden.
    """
    n = len(close)
    out = np.full(n, np.nan)
    if n <= ATR_PERIOD:
        return out
    prev_close = close[:-1]
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - prev_close), np.abs(low[1:] - prev_close)),
    )
    atr = float(tr[:ATR_PERIOD].mean())
    out[ATR_PERIOD] = atr
    for i in range(ATR_PERIOD, len(tr)):
        atr = (atr * (ATR_PERIOD - 1) + tr[i]) / ATR_PERIOD
        out[i + 1] = atr
    out[out <= 0] = np.nan
    return out


@dataclass
class AssetSeries:
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    low: np.ndarray
    c: np.ndarray
    atr: np.ndarray


def load_series(data_dir: Path, timeframe: str) -> dict[int, AssetSeries]:
    per_tf, _ = load_candle_arrays(data_dir, [timeframe])
    out: dict[int, AssetSeries] = {}
    for asset_id, arrays in per_tf[timeframe].items():
        out[asset_id] = AssetSeries(
            t=arrays["t"],
            o=arrays["o"],
            h=arrays["h"],
            low=arrays["l"],
            c=arrays["c"],
            atr=wilder_atr_series(arrays["h"], arrays["l"], arrays["c"]),
        )
    return out


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


@dataclass
class Trade:
    symbol: str
    asset_id: int
    direction: str
    score: float
    armed_ns: int
    fill_ns: int
    exit_ns: int
    entry: float
    stop: float
    r_dist: float
    gross_r: float
    fees_r: float
    net_r: float
    exit_reason: str
    bars: int
    mae_r: float
    mfe_r: float
    tp_hits: int
    closed: bool


@dataclass
class ArmOutcome:
    status: str
    fill_price: float = 0.0
    fill_idx: int = -1
    atr: float = 0.0


def arm_retest(
    series: AssetSeries,
    *,
    armed_ns: int,
    reference_entry: float,
    original_stop: float,
    is_long: bool,
    pending_ns: int,
) -> ArmOutcome:
    """Spiegel von ``app.signals.retest_entry.arm_retest_entry``."""
    sig_idx = int(np.searchsorted(series.t, armed_ns, side="right")) - 1
    if sig_idx < 0:
        return ArmOutcome("skipped_no_history")
    atr = series.atr[sig_idx]
    if not np.isfinite(atr):
        return ArmOutcome("skipped_no_atr")

    near = atr * RETEST_ZONE_NEAR
    far = atr * RETEST_ZONE_FAR
    zone_lo, zone_hi = (
        (reference_entry - far, reference_entry - near)
        if is_long
        else (reference_entry + near, reference_entry + far)
    )
    pending_until = armed_ns + pending_ns

    for i in range(sig_idx + 1, len(series.t)):
        if series.t[i] > pending_until:
            return ArmOutcome("skipped_expiry", atr=float(atr))
        high = series.h[i]
        low = series.low[i]
        if (is_long and low <= original_stop) or ((not is_long) and high >= original_stop):
            return ArmOutcome("skipped_sl", atr=float(atr))
        if low <= zone_hi and high >= zone_lo:
            fill = min(high, zone_hi) if is_long else max(low, zone_lo)
            return ArmOutcome("filled", fill_price=float(fill), fill_idx=i, atr=float(atr))
    return ArmOutcome("skipped_expiry", atr=float(atr))


def replay_exit(
    series: AssetSeries,
    *,
    start_idx: int,
    entry: float,
    stop: float,
    is_long: bool,
    variant: ExitVariant,
    entry_atr: float,
    expiry_ns: int,
) -> tuple[float, float, str, int, int, float, float, int, bool]:
    """Kerze fuer Kerze wie ``PaperTradingService._replay_bars``.

    Rueckgabe: gross_r, fees_r, exit_reason, exit_idx, bars, mae_r, mfe_r,
    tp_hits, closed.
    """
    sign = 1.0 if is_long else -1.0
    r_dist = abs(entry - stop)
    fee_k = FEE_PERCENT / 100.0 / r_dist

    remaining = 1.0
    gross_r = 0.0
    fees_r = entry * fee_k
    current_stop = stop
    tps = variant.tp_multipliers or ()
    tp_prices = [entry + sign * m * r_dist for m in tps]
    tp_done = [False] * len(tps)
    tp_hits = 0
    extreme = entry
    exit_reason = "open"
    exit_idx = start_idx
    bars = 0
    mae_r = 0.0
    mfe_r = 0.0
    closed = False

    def take(price: float, fraction: float, reason: str, idx: int) -> None:
        nonlocal remaining, gross_r, fees_r, exit_reason, exit_idx, closed
        qty = min(fraction, remaining)
        if qty <= 0:
            return
        gross_r += (price - entry) * sign * qty / r_dist
        fees_r += price * qty * fee_k
        remaining -= qty
        exit_reason = reason
        exit_idx = idx
        if remaining <= 1e-9:
            remaining = 0.0
            closed = True

    for i in range(start_idx, len(series.t)):
        if remaining <= 0:
            break
        bars += 1
        high = series.h[i]
        low = series.low[i]
        close = series.c[i]

        worst_price, best_price = (low, high) if is_long else (high, low)
        mae_r = min(mae_r, (worst_price - entry) * sign / r_dist)
        mfe_r = max(mfe_r, (best_price - entry) * sign / r_dist)

        # 1) Stop hat Vorrang — auch bei Trailing wird der Stand vom Ende der
        #    Vorkerze geprueft, sonst waere der Trail innerhalb der Kerze
        #    vorausschauend.
        stop_hit = low <= current_stop if is_long else high >= current_stop
        if stop_hit:
            if abs(current_stop - entry) < 1e-12:
                reason = "break_even"
            elif abs(current_stop - stop) > 1e-12:
                reason = "trailing_stop"
            else:
                reason = "stop_loss"
            take(current_stop, remaining, reason, i)
            break

        # 2) TP-Leiter am guenstigsten Kurs der Kerze, in Reihenfolge.
        fav_price = best_price
        for level, tp_price in enumerate(tp_prices):
            if tp_done[level]:
                continue
            hit = fav_price >= tp_price if is_long else fav_price <= tp_price
            if not hit:
                break
            fraction = (
                variant.scale[level] if level < len(variant.scale) else 1.0
            )
            if level == len(tp_prices) - 1:
                fraction = remaining
            take(tp_price, fraction, f"take_profit_{level + 1}", i)
            tp_done[level] = True
            tp_hits += 1
            if level == 0 and variant.move_be:
                current_stop = entry
            if remaining <= 0:
                break
        if remaining <= 0:
            break

        # 3) Trailing nachziehen (erst nach der Kerze, nie nach innen).
        if variant.trail_atr is not None and np.isfinite(entry_atr) and entry_atr > 0:
            extreme = max(extreme, high) if is_long else min(extreme, low)
            reached = (extreme - entry) * sign / r_dist
            if reached >= variant.trail_arm_r:
                candidate = (
                    extreme - variant.trail_atr * entry_atr
                    if is_long
                    else extreme + variant.trail_atr * entry_atr
                )
                current_stop = (
                    max(current_stop, candidate) if is_long else min(current_stop, candidate)
                )

        # 4) Zeitlicher Exit auf Schlusskurs.
        if series.t[i] >= expiry_ns:
            take(close, remaining, "expired", i)
            break

    if remaining > 0:
        last = len(series.t) - 1
        take(float(series.c[last]), remaining, "data_end_mtm", last)
        closed = False

    return gross_r, fees_r, exit_reason, exit_idx, bars, mae_r, mfe_r, tp_hits, closed


# ---------------------------------------------------------------------------
# Kennzahlen
# ---------------------------------------------------------------------------


def _max_drawdown_r(sorted_r: Sequence[float]) -> float:
    peak = 0.0
    equity = 0.0
    worst = 0.0
    for r in sorted_r:
        equity += r
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def _skewness(x: np.ndarray) -> float:
    if len(x) < 3:
        return float("nan")
    sd = x.std(ddof=1)
    if sd <= 0:
        return float("nan")
    n = len(x)
    return float(n / ((n - 1) * (n - 2)) * (((x - x.mean()) / sd) ** 3).sum())


def _block_bootstrap(
    values: np.ndarray, day_keys: np.ndarray, *, reps: int, seed: int
) -> tuple[float, float, float]:
    """Bootstrap ueber Kalendertage statt ueber Trades.

    Trades desselben Tages teilen dasselbe Marktregime; sie als unabhaengig zu
    behandeln wuerde das Konfidenzintervall kuenstlich verengen.
    """
    if len(values) == 0:
        return float("nan"), float("nan"), float("nan")
    days, inverse = np.unique(day_keys, return_inverse=True)
    order = np.argsort(inverse, kind="stable")
    sorted_vals = values[order]
    counts = np.bincount(inverse, minlength=len(days))
    starts = np.concatenate([[0], np.cumsum(counts)])
    sums = np.add.reduceat(sorted_vals, starts[:-1]) if len(days) else np.array([])

    rng = np.random.default_rng(seed)
    means = np.empty(reps)
    n_days = len(days)
    for i in range(reps):
        pick = rng.integers(0, n_days, n_days)
        total = sums[pick].sum()
        count = counts[pick].sum()
        means[i] = total / count if count else np.nan
    lo, hi = np.nanpercentile(means, [2.5, 97.5])
    p = 2.0 * min((means <= 0).mean(), (means >= 0).mean())
    return float(lo), float(hi), float(min(1.0, max(p, 1.0 / reps)))


def summarise(trades: list[Trade], *, reps: int, seed: int, label: str = "") -> dict[str, Any]:
    if not trades:
        return {"n": 0, "label": label}
    net = np.array([t.net_r for t in trades])
    fees = np.array([t.fees_r for t in trades])
    gross = np.array([t.gross_r for t in trades])
    wins = net[net > 0]
    losses = net[net < 0]
    by_exit = sorted(trades, key=lambda t: t.exit_ns)
    day_keys = np.array([t.fill_ns // (24 * NS_PER_HOUR) for t in trades])
    lo, hi, p = _block_bootstrap(net, day_keys, reps=reps, seed=seed)
    hold_h = np.array([(t.exit_ns - t.fill_ns) / NS_PER_HOUR for t in trades])
    q95 = float(np.percentile(net, 95))
    q05 = float(np.percentile(net, 5))

    exit_counts: dict[str, int] = {}
    for t in trades:
        exit_counts[t.exit_reason] = exit_counts.get(t.exit_reason, 0) + 1

    return {
        "label": label,
        "n": len(trades),
        "n_days": int(len(np.unique(day_keys))),
        "n_symbols": len({t.symbol for t in trades}),
        "expectancy_r": float(net.mean()),
        "median_r": float(np.median(net)),
        "total_r": float(net.sum()),
        "ci_low_r": lo,
        "ci_high_r": hi,
        "p_value": p,
        "std_r": float(net.std(ddof=1)) if len(net) > 1 else float("nan"),
        "hit_rate": float((net > 0).mean()),
        "profit_factor": (
            float(wins.sum() / abs(losses.sum())) if losses.size and losses.sum() != 0 else None
        ),
        "avg_win_r": float(wins.mean()) if wins.size else 0.0,
        "avg_loss_r": float(losses.mean()) if losses.size else 0.0,
        "fees_r_total": float(fees.sum()),
        "fees_r_per_trade": float(fees.mean()),
        "gross_expectancy_r": float(gross.mean()),
        "max_dd_r": _max_drawdown_r([t.net_r for t in by_exit]),
        "worst_trade_r": float(net.min()),
        "best_trade_r": float(net.max()),
        "skewness": _skewness(net),
        "tail_ratio": float(q95 / abs(q05)) if q05 < 0 else None,
        "pct95_r": q95,
        "pct05_r": q05,
        "avg_hold_h": float(hold_h.mean()),
        "avg_mae_r": float(np.mean([t.mae_r for t in trades])),
        "avg_mfe_r": float(np.mean([t.mfe_r for t in trades])),
        "exit_counts": exit_counts,
        "first_fill": int(min(t.fill_ns for t in trades)),
        "last_fill": int(max(t.fill_ns for t in trades)),
    }


# ---------------------------------------------------------------------------
# Orchestrierung
# ---------------------------------------------------------------------------


@dataclass
class SignalRow:
    asset_id: int
    symbol: str
    ts_ns: int
    score: float
    direction: str
    reference_price: float
    atr: float
    nearest_support: float
    nearest_resistance: float
    live_stop: float


def load_signals(path: Path, *, gate: str) -> tuple[list[SignalRow], dict[str, Any]]:
    frame = pd.read_csv(path, compression="infer")
    total = len(frame)
    allowed = STRONG if gate == "strong" else ACTIONABLE
    mask = frame["direction"].isin(allowed)
    stats = {"rows": total, "after_direction": int(mask.sum())}

    frame = frame[mask]
    frame = frame[frame["risk_reward_ratio"].fillna(0.0) >= MIN_RR]
    stats["after_rr"] = len(frame)
    frame = frame[frame["data_quality"].fillna(0.0) >= MIN_DATA_QUALITY]
    stats["after_quality"] = len(frame)
    frame = frame[frame["atr_value"].notna() & (frame["atr_value"] > 0)]
    frame = frame[frame["reference_price"] > 0]
    stats["after_levels"] = len(frame)

    ts = pd.to_datetime(frame["ts"], utc=True).map(lambda value: value.value).to_numpy(dtype=np.int64)
    rows = [
        SignalRow(
            asset_id=int(a),
            symbol=str(s).upper(),
            ts_ns=int(t),
            score=float(sc),
            direction=str(d),
            reference_price=float(p),
            atr=float(atr),
            nearest_support=float(ns) if pd.notna(ns) else float("nan"),
            nearest_resistance=float(nr) if pd.notna(nr) else float("nan"),
            live_stop=float(sl) if pd.notna(sl) else float("nan"),
        )
        for a, s, t, sc, d, p, atr, ns, nr, sl in zip(
            frame["asset_id"],
            frame["symbol"],
            ts,
            frame["score"],
            frame["direction"],
            frame["reference_price"],
            frame["atr_value"],
            frame["nearest_support"],
            frame["nearest_resistance"],
            frame["stop_loss"],
            strict=True,
        )
    ]
    rows.sort(key=lambda r: (r.ts_ns, r.asset_id))
    return rows, stats


def parity_check(rows: Iterable[SignalRow], limit: int) -> dict[str, Any]:
    """Rekonstruierten Stop gegen den vom Produktionscode gelieferten pruefen."""
    checked = 0
    worst = 0.0
    mismatches: list[dict[str, Any]] = []
    for row in rows:
        if checked >= limit:
            break
        if not np.isfinite(row.live_stop):
            continue
        is_long = row.direction.endswith("LONG")
        built = levels_for_direction(
            reference_price=row.reference_price,
            atr=row.atr,
            nearest_support=row.nearest_support,
            nearest_resistance=row.nearest_resistance,
            is_long=is_long,
            atr_multiplier=BASE_ATR_MULT,
        )
        if built is None:
            continue
        checked += 1
        rel = abs(built[1] - row.live_stop) / row.live_stop
        worst = max(worst, rel)
        if rel > 1e-9 and len(mismatches) < 5:
            mismatches.append(
                {
                    "symbol": row.symbol,
                    "direction": row.direction,
                    "rebuilt_stop": built[1],
                    "live_stop": row.live_stop,
                    "rel_error": rel,
                }
            )
    return {
        "checked": checked,
        "max_rel_error": worst,
        "ok": bool(checked and worst <= 1e-9),
        "mismatches": mismatches,
    }


def arming_parity_check(
    rows: Sequence[SignalRow],
    series_by_asset: dict[int, AssetSeries],
    *,
    limit: int,
    timeframe: str,
    pending_ns: int,
    seed: int,
) -> dict[str, Any]:
    """Schnelles Arming gegen ``app.signals.retest_entry.arm_retest_entry`` pruefen.

    Der kanonische Code arbeitet mit ``Candle``-Objekten und ``Decimal``; fuer
    hunderttausende Replays ist das zu langsam, deshalb laeuft hier eine
    numpy-Fassung. Sie muss auf einer Stichprobe exakt dasselbe liefern —
    andernfalls ist der ganze Backtest wertlos.
    """
    from app.core.time import timeframe_to_timedelta
    from app.market_data.types import Candle
    from app.signals.retest_entry import RetestEntryConfig, arm_retest_entry

    rng = np.random.default_rng(seed)
    candidates = [r for r in rows if r.asset_id in series_by_asset]
    if not candidates:
        return {"checked": 0, "ok": False, "note": "keine Kandidaten"}
    picks = rng.choice(len(candidates), size=min(limit, len(candidates)), replace=False)

    cfg = RetestEntryConfig(pending_multiplier=RETEST_PENDING_MULT)
    tf_delta = timedelta(seconds=int(pending_ns / RETEST_PENDING_MULT / 1_000_000_000))
    timeframe = f"{int(tf_delta.total_seconds() // 60)}m"

    checked = 0
    status_mismatch = 0
    price_mismatch = 0
    worst_price_error = 0.0
    examples: list[dict[str, Any]] = []

    for idx in picks:
        row = candidates[int(idx)]
        series = series_by_asset[row.asset_id]
        is_long = row.direction.endswith("LONG")
        built = levels_for_direction(
            reference_price=row.reference_price,
            atr=row.atr,
            nearest_support=row.nearest_support,
            nearest_resistance=row.nearest_resistance,
            is_long=is_long,
            atr_multiplier=BASE_ATR_MULT,
        )
        if built is None:
            continue
        reference_entry, original_stop = built

        # Nur das Fenster um den Signalzeitpunkt materialisieren: der kanonische
        # Code rechnet den ATR aus dem gesamten uebergebenen Praefix, live sind
        # das 14 Tage Vorlauf.
        sig_idx = int(np.searchsorted(series.t, row.ts_ns, side="right")) - 1
        if sig_idx < ATR_PERIOD:
            continue
        lookback = int(14 * 24 * NS_PER_HOUR / (pending_ns / RETEST_PENDING_MULT))
        lo = max(0, sig_idx - max(lookback, 400))
        hi = min(len(series.t), sig_idx + 400)
        candles = [
            Candle(
                open_time=pd.Timestamp(series.t[i], unit="ns", tz="UTC").to_pydatetime(),
                close_time=pd.Timestamp(series.t[i], unit="ns", tz="UTC").to_pydatetime()
                + tf_delta,
                open=float(series.o[i]),
                high=float(series.h[i]),
                low=float(series.low[i]),
                close=float(series.c[i]),
                volume=0.0,
                is_closed=True,
            )
            for i in range(lo, hi)
        ]
        reference = arm_retest_entry(
            direction=row.direction,
            arm_time=pd.Timestamp(row.ts_ns, unit="ns", tz="UTC").to_pydatetime(),
            reference_entry=reference_entry,
            original_stop=original_stop,
            timeframe=timeframe,
            candles=candles,
            config=cfg,
        )
        fast = arm_retest(
            series,
            armed_ns=row.ts_ns,
            reference_entry=reference_entry,
            original_stop=original_stop,
            is_long=is_long,
            pending_ns=pending_ns,
        )
        checked += 1

        ref_filled = reference.status == "filled"
        if ref_filled != (fast.status == "filled"):
            status_mismatch += 1
            if len(examples) < 5:
                examples.append(
                    {
                        "symbol": row.symbol,
                        "reference_status": reference.status,
                        "fast_status": fast.status,
                    }
                )
            continue
        if ref_filled and reference.fill_price:
            rel = abs(reference.fill_price - fast.fill_price) / abs(reference.fill_price)
            worst_price_error = max(worst_price_error, rel)
            if rel > 1e-9:
                price_mismatch += 1
                if len(examples) < 5:
                    examples.append(
                        {
                            "symbol": row.symbol,
                            "reference_fill": reference.fill_price,
                            "fast_fill": fast.fill_price,
                            "rel_error": rel,
                        }
                    )

    return {
        "checked": checked,
        "status_mismatch": status_mismatch,
        "price_mismatch": price_mismatch,
        "max_rel_fill_error": worst_price_error,
        "ok": bool(checked and status_mismatch == 0 and price_mismatch == 0),
        "examples": examples,
    }


def invert(direction: str) -> str:
    return direction.replace("LONG", "TMP").replace("SHORT", "LONG").replace("TMP", "SHORT")


def run_arm(
    rows: list[SignalRow],
    series_by_asset: dict[int, AssetSeries],
    variant: ExitVariant,
    *,
    inverted: bool,
    pending_ns: int,
    allow_overlap: bool,
) -> tuple[list[Trade], dict[str, int]]:
    """Ein Signalstrom, chronologisch: Arming -> Fill -> Exit-Replay -> naechstes."""
    trades: list[Trade] = []
    skips: dict[str, int] = {}
    busy_until: dict[str, int] = {}
    expiry_ns_offset = int(variant.hold_hours * NS_PER_HOUR)

    def note(reason: str) -> None:
        skips[reason] = skips.get(reason, 0) + 1

    for row in rows:
        series = series_by_asset.get(row.asset_id)
        if series is None:
            note("no_candles")
            continue
        direction = invert(row.direction) if inverted else row.direction
        is_long = direction.endswith("LONG")

        if not allow_overlap and busy_until.get(row.symbol, -1) >= row.ts_ns:
            note("symbol_busy")
            continue

        built = levels_for_direction(
            reference_price=row.reference_price,
            atr=row.atr,
            nearest_support=row.nearest_support,
            nearest_resistance=row.nearest_resistance,
            is_long=is_long,
            atr_multiplier=variant.stop_atr_mult,
        )
        if built is None:
            note("no_levels")
            continue
        reference_entry, original_stop = built

        arm = arm_retest(
            series,
            armed_ns=row.ts_ns,
            reference_entry=reference_entry,
            original_stop=original_stop,
            is_long=is_long,
            pending_ns=pending_ns,
        )
        if arm.status != "filled":
            note(arm.status)
            continue

        r_dist = abs(reference_entry - original_stop)
        entry = arm.fill_price
        stop = entry - r_dist if is_long else entry + r_dist
        fill_ns = int(series.t[arm.fill_idx])
        expiry_ns = fill_ns + expiry_ns_offset

        gross_r, fees_r, reason, exit_idx, bars, mae_r, mfe_r, tp_hits, closed = replay_exit(
            series,
            start_idx=arm.fill_idx,
            entry=entry,
            stop=stop,
            is_long=is_long,
            variant=variant,
            entry_atr=arm.atr,
            expiry_ns=expiry_ns,
        )
        exit_ns = int(series.t[exit_idx])
        trades.append(
            Trade(
                symbol=row.symbol,
                asset_id=row.asset_id,
                direction=direction,
                score=row.score,
                armed_ns=row.ts_ns,
                fill_ns=fill_ns,
                exit_ns=exit_ns,
                entry=entry,
                stop=stop,
                r_dist=r_dist,
                gross_r=gross_r,
                fees_r=fees_r,
                net_r=gross_r - fees_r,
                exit_reason=reason,
                bars=bars,
                mae_r=mae_r,
                mfe_r=mfe_r,
                tp_hits=tp_hits,
                closed=closed,
            )
        )
        busy_until[row.symbol] = exit_ns

    return trades, skips


def split_windows(trades: list[Trade], split_ns: int | None) -> dict[str, list[Trade]]:
    if split_ns is None:
        return {}
    early = [t for t in trades if t.fill_ns < split_ns]
    late = [t for t in trades if t.fill_ns >= split_ns]
    return {"window_1": early, "window_2": late}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--timeframe", default="1h", help="Kerzen-Timeframe fuer das Replay")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--gate", choices=("strong", "actionable"), default="strong")
    parser.add_argument(
        "--polarity", choices=("normal", "inverted", "both"), default="both"
    )
    parser.add_argument("--allow-overlap", action="store_true")
    parser.add_argument("--bootstrap", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260731)
    parser.add_argument("--parity-check", type=int, default=2000)
    parser.add_argument(
        "--arm-parity",
        type=int,
        default=300,
        help="Stichprobe fuer den Abgleich gegen app.signals.retest_entry",
    )
    parser.add_argument("--split-date", default="", help="ISO-Datum fuer die Walk-Forward-Teilung")
    parser.add_argument("--variants", default="", help="Komma-Liste von Variantenschluesseln")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--trades-out", type=Path, default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)

    tf_minutes = int(TIMEFRAME_MINUTES[args.timeframe])
    tf_hours = tf_minutes / 60.0
    pending_ns = int(RETEST_PENDING_MULT * tf_minutes * 60 * 1_000_000_000)
    expiry_hours = EXPIRY_MULT * tf_hours

    print(f"Lade Signale {args.signals} ...", file=sys.stderr, flush=True)
    rows, gate_stats = load_signals(args.signals, gate=args.gate)
    print(f"  {len(rows)} Signale nach Gates {gate_stats}", file=sys.stderr, flush=True)

    parity = parity_check(rows, args.parity_check)
    print(
        f"  Level-Parity: {parity['checked']} geprueft, "
        f"max. rel. Abweichung {parity['max_rel_error']:.2e}",
        file=sys.stderr,
        flush=True,
    )

    print(f"Lade {args.timeframe}-Kerzen ...", file=sys.stderr, flush=True)
    t0 = time.time()
    series_by_asset = load_series(args.data_dir, args.timeframe)
    print(f"  {len(series_by_asset)} Assets in {time.time() - t0:.1f}s", file=sys.stderr, flush=True)

    arm_parity: dict[str, Any] = {"checked": 0, "ok": None, "note": "uebersprungen"}
    if args.arm_parity > 0:
        arm_parity = arming_parity_check(
            rows,
            series_by_asset,
            limit=args.arm_parity,
            timeframe=args.timeframe,
            pending_ns=pending_ns,
            seed=args.seed,
        )
        print(
            f"  Arming-Parity: {arm_parity['checked']} geprueft, "
            f"Status-Abweichungen {arm_parity.get('status_mismatch')}, "
            f"Fill-Abweichungen {arm_parity.get('price_mismatch')}",
            file=sys.stderr,
            flush=True,
        )

    variants = build_variants(expiry_hours)
    if args.variants:
        wanted = {v.strip() for v in args.variants.split(",") if v.strip()}
        variants = [v for v in variants if v.key in wanted]
    polarities = (
        ["normal", "inverted"] if args.polarity == "both" else [args.polarity]
    )

    split_ns: int | None = None
    if args.split_date:
        split_ns = int(pd.Timestamp(args.split_date, tz="UTC").value)
    elif rows:
        split_ns = int((rows[0].ts_ns + rows[-1].ts_ns) // 2)

    results: list[dict[str, Any]] = []
    all_trades: list[dict[str, Any]] = []
    t0 = time.time()
    for variant in variants:
        for polarity in polarities:
            trades, skips = run_arm(
                rows,
                series_by_asset,
                variant,
                inverted=polarity == "inverted",
                pending_ns=pending_ns,
                allow_overlap=args.allow_overlap,
            )
            entry = {
                "variant": variant.key,
                "label": variant.label,
                "hypothesis": "B" if polarity == "inverted" else variant.hypothesis,
                "polarity": polarity,
                "hold_hours": variant.hold_hours,
                "tp_multipliers": list(variant.tp_multipliers or []),
                "scale_events": variant.n_scale_events,
                "move_be": variant.move_be,
                "stop_atr_mult": variant.stop_atr_mult,
                "trail_atr": variant.trail_atr,
                "skips": skips,
                "overall": summarise(
                    trades, reps=args.bootstrap, seed=args.seed, label="gesamt"
                ),
            }
            for name, subset in split_windows(trades, split_ns).items():
                entry[name] = summarise(
                    subset, reps=args.bootstrap, seed=args.seed, label=name
                )
            results.append(entry)
            if args.trades_out:
                for t in trades:
                    all_trades.append(
                        {
                            "variant": variant.key,
                            "polarity": polarity,
                            "symbol": t.symbol,
                            "direction": t.direction,
                            "score": t.score,
                            "fill": int(t.fill_ns),
                            "exit": int(t.exit_ns),
                            "net_r": t.net_r,
                            "fees_r": t.fees_r,
                            "exit_reason": t.exit_reason,
                        }
                    )
            o = entry["overall"]
            print(
                f"  {variant.key:34s} {polarity:8s} n={o.get('n', 0):5d} "
                f"E[R]={o.get('expectancy_r', float('nan')):+.4f} "
                f"PF={o.get('profit_factor') or float('nan'):.3f} "
                f"DD={o.get('max_dd_r', float('nan')):+.1f}R "
                f"skew={o.get('skewness', float('nan')):+.2f}",
                file=sys.stderr,
                flush=True,
            )

    payload = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "signals_file": str(args.signals),
        "timeframe": args.timeframe,
        "gate": args.gate,
        "gate_stats": gate_stats,
        "level_parity": parity,
        "arming_parity": arm_parity,
        "economics": {
            "fee_percent_per_side": FEE_PERCENT,
            "retest_zone_atr": [RETEST_ZONE_NEAR, RETEST_ZONE_FAR],
            "retest_pending_bars": RETEST_PENDING_MULT,
            "live_expiry_hours": expiry_hours,
            "one_trade_per_symbol": not args.allow_overlap,
            "sizing": "risikonormiert (R = risk_amount); Gebuehr_R = Satz / Stop-Prozent",
        },
        "bootstrap": {"reps": args.bootstrap, "cluster": "Kalendertag des Fills"},
        "split_ns": split_ns,
        "split_date": pd.Timestamp(split_ns, unit="ns", tz="UTC").isoformat()
        if split_ns
        else None,
        "runtime_seconds": round(time.time() - t0, 1),
        "results": results,
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"Geschrieben: {args.out}", file=sys.stderr, flush=True)
    else:
        print(text)
    if args.trades_out and all_trades:
        pd.DataFrame(all_trades).to_csv(args.trades_out, index=False, compression="gzip")
        print(f"Geschrieben: {args.trades_out}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
