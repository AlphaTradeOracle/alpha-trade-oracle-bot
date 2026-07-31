"""Hat der Signal-Score Vorhersagekraft? — Auswertung auf tiefer Historie.

Verhaeltnis zu ``analyze_score_edge.py``
---------------------------------------
``analyze_score_edge.py`` wertet die **Live-Tabelle** ``signals`` aus. Die deckt
nur wenige Tage ab; bei ueberlappenden Vorhersagefenstern blieben dort fuer den
24h-Horizont effektiv zwei unabhaengige Beobachtungen uebrig. Jede Aussage war
damit die Beschreibung einer einzelnen Marktbewegung.

Dieses Skript wertet stattdessen das von
``scripts/regenerate_historical_signals.py`` erzeugte Panel aus: der echte
Produktions-Score, ueber Monate historischer Kerzen neu gerechnet. Die
statistischen Bausteine (Cluster-Bootstrap, Dezil-CIs, Rangkorrelation) werden
aus ``analyze_score_edge.py`` importiert, nicht neu geschrieben.

Was hier methodisch anders — und strenger — ist
-----------------------------------------------
1. **Cluster auf Tagesebene.** Ein 24h-Vorhersagefenster ueberlappt mit allen
   Scans desselben Tages. Der Bootstrap zieht deshalb ganze **Tage**, nicht
   Scans und erst gar nicht Einzelzeilen.
2. **IC-Zeitreihe als primaerer Schaetzer.** Je Scan wird eine
   Querschnitts-IC ueber alle Coins berechnet. Der Mittelwert dieser Reihe ist
   der Schaetzer, das Konfidenzintervall kommt aus einem Block-Bootstrap ueber
   Tage. Damit haengt die Aussage nicht an der Zeilenzahl.
3. **Ausgewiesene Unabhaengigkeit.** Neben ``n`` wird immer die Zahl der
   nicht-ueberlappenden Beobachtungen berichtet (Scan-Abstand >= Horizont).
4. **Multiples Testen.** Ueber die Haupttabelle laeuft eine
   Benjamini-Hochberg-Korrektur. Bei ~100 Tests sind fuenf ``p < 0.05`` die
   Erwartung unter der Nullhypothese, kein Befund.

Look-ahead-Freiheit
-------------------
Einstieg ist der ``reference_price`` des Signals, also der Schlusskurs der zum
Scan-Zeitpunkt letzten **geschlossenen** Kerze. Der Ausstieg ist der Schlusskurs
der Kerze, die genau ``H`` Stunden spaeter schliesst. Indikatoren wurden bereits
bei der Regeneration ausschliesslich aus Kerzen bis zum Scan-Zeitpunkt gerechnet.

Aufruf
------
    python scripts/analyze_deep_edge.py --tag live_current
    python scripts/analyze_deep_edge.py --tag long_current --return-timeframe 4h \
        --horizons 4,8,24 --start 2025-08-01
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.analyze_score_edge import (  # noqa: E402
    bootstrap_bucket_means,
    bootstrap_corr,
    fisher_pvalue,
    pearson,
    ranks,
    spearman,
)

DATA_DIR = REPO_ROOT / "exports" / "edge_data"
OUT_DIR = REPO_ROOT / "exports"

N_BOOT = 10_000
RNG_SEED = 20260731
N_DECILES = 10
MIN_ASSETS_PER_SCAN = 30

#: Score-Komponenten, wie sie die Engine liefert.
COMPONENTS = [
    "c_trend",
    "c_momentum",
    "c_volume",
    "c_volatility",
    "c_market_structure",
    "c_multi_timeframe",
    "c_risk_reward",
]

#: Zusaetzlich gepruefte Rohmerkmale. ``atr_percent`` ist der Kandidat, der in
#: der ersten Analyse als einziger stabil aussah.
RAW_FEATURES = [
    "atr_percent",
    "adx_14",
    "rsi_14",
    "roc_14",
    "volume_ratio",
    "obv_slope",
    "trend_strength",
    "macd_hist_norm",
    "bb_width_rel",
    "risk_reward_ratio",
]

TF_HOURS = {"15m": 0.25, "1h": 1, "4h": 4, "1d": 24}


# ---------------------------------------------------------------------------
# Statistik-Ergaenzungen
# ---------------------------------------------------------------------------


def benjamini_hochberg(pvalues: Sequence[float], alpha: float = 0.05) -> tuple[np.ndarray, float]:
    """BH-Korrektur. Rueckgabe: (abgelehnt?, Schwellenwert)."""
    p = np.asarray(pvalues, dtype=float)
    valid = np.isfinite(p)
    rejected = np.zeros_like(p, dtype=bool)
    if not valid.any():
        return rejected, float("nan")
    order = np.argsort(p[valid])
    ranked = p[valid][order]
    m = len(ranked)
    thresholds = alpha * np.arange(1, m + 1) / m
    passing = np.flatnonzero(ranked <= thresholds)
    if passing.size == 0:
        return rejected, 0.0
    cutoff = ranked[passing[-1]]
    rejected[valid] = p[valid] <= cutoff
    return rejected, float(cutoff)


#: Weniger Bloecke ergeben kein belastbares Intervall: bei zwei oder drei Tagen
#: sind die Resamples fast entartet und liefern absurd enge, scheinbar hoch
#: signifikante CIs. Genau diese Art Scheinbefund soll hier nicht entstehen.
MIN_BOOTSTRAP_BLOCKS = 10


def block_bootstrap_mean(
    values: np.ndarray,
    blocks: np.ndarray,
    n_boot: int = N_BOOT,
    rng: np.random.Generator | None = None,
    min_blocks: int = MIN_BOOTSTRAP_BLOCKS,
) -> dict[str, float]:
    """Mittelwert-CI, wobei ganze Bloecke (Tage) mit Zuruecklegen gezogen werden.

    Vektorisiert: pro Block werden Summe und Anzahl aggregiert, ein Resample ist
    dann eine gewichtete Summe dieser Aggregate.
    """
    rng = rng or np.random.default_rng(RNG_SEED)
    finite = np.isfinite(values)
    values, blocks = values[finite], blocks[finite]
    if values.size < 3:
        return {
            "mean": float("nan"),
            "ci_low": float("nan"),
            "ci_high": float("nan"),
            "p": float("nan"),
        }

    codes, _ = pd.factorize(blocks)
    n_blocks = int(codes.max()) + 1
    if n_blocks < min_blocks:
        return {
            "mean": float(values.mean()),
            "ci_low": float("nan"),
            "ci_high": float("nan"),
            "p": float("nan"),
            "n_blocks": int(n_blocks),
            "n_obs": int(values.size),
            "note": f"zu wenige Bloecke fuer ein belastbares CI ({n_blocks} < {min_blocks})",
        }
    sums = np.zeros(n_blocks)
    counts = np.zeros(n_blocks)
    np.add.at(sums, codes, values)
    np.add.at(counts, codes, 1.0)

    draws = np.empty(n_boot)
    done = 0
    chunk = max(1, min(2000, n_boot))
    while done < n_boot:
        size = min(chunk, n_boot - done)
        w = rng.multinomial(n_blocks, np.full(n_blocks, 1.0 / n_blocks), size=size).astype(float)
        num = w @ sums
        den = w @ counts
        with np.errstate(invalid="ignore", divide="ignore"):
            draws[done : done + size] = np.where(den > 0, num / den, np.nan)
        done += size

    draws = draws[np.isfinite(draws)]
    if draws.size == 0:
        return {
            "mean": float("nan"),
            "ci_low": float("nan"),
            "ci_high": float("nan"),
            "p": float("nan"),
        }
    lo, hi = np.percentile(draws, [2.5, 97.5])
    share_neg = float((draws <= 0).mean())
    p = 2.0 * min(share_neg, 1.0 - share_neg)
    return {
        "mean": float(values.mean()),
        "ci_low": float(lo),
        "ci_high": float(hi),
        "p": float(min(1.0, max(p, 1.0 / draws.size))),
        "n_blocks": int(n_blocks),
        "n_obs": int(values.size),
    }


def count_non_overlapping(times: pd.Series, horizon_h: float) -> int:
    """Groesse einer greedy gewaehlten Menge zeitlich disjunkter Beobachtungen."""
    ordered = pd.Series(sorted(pd.unique(times)))
    count, last = 0, None
    for value in ordered:
        if last is None or (value - last) >= pd.Timedelta(hours=horizon_h):
            count += 1
            last = value
    return count


# ---------------------------------------------------------------------------
# Panel aufbauen
# ---------------------------------------------------------------------------


def load_panel(data_dir: Path, tag: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    path = data_dir / f"regen_signals_{tag}.csv.gz"
    panel = pd.read_csv(path, parse_dates=["ts"])
    meta_path = data_dir / f"regen_signals_{tag}.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return panel, meta


def load_close_lookup(data_dir: Path, timeframe: str) -> pd.DataFrame:
    path = data_dir / f"deep_candles_{timeframe}.csv.gz"
    frame = pd.read_csv(path, usecols=["asset_id", "open_time", "close"], parse_dates=["open_time"])
    frame["open_time"] = frame["open_time"].dt.tz_convert("UTC")
    return frame


def attach_forward_returns(
    panel: pd.DataFrame, closes: pd.DataFrame, timeframe: str, horizons: Sequence[int]
) -> pd.DataFrame:
    """Forward Returns strikt aus Kerzen NACH dem Signalzeitpunkt anhaengen.

    Einstiegskerze schliesst genau bei ``ts`` (open_time = ts - Timeframe).
    Ausstiegskerze schliesst bei ``ts + H`` (open_time = ts - Timeframe + H).
    """
    step = pd.Timedelta(hours=TF_HOURS[timeframe])
    panel = panel.copy()
    panel["entry_open_time"] = panel["ts"] - step

    lookup = closes.rename(columns={"open_time": "entry_open_time", "close": "entry_close"})
    panel = panel.merge(lookup, on=["asset_id", "entry_open_time"], how="left")

    for horizon in horizons:
        exit_frame = closes.rename(columns={"open_time": "_exit_open", "close": f"_exit_{horizon}"})
        panel["_exit_open"] = panel["entry_open_time"] + pd.Timedelta(hours=horizon)
        panel = panel.merge(exit_frame, on=["asset_id", "_exit_open"], how="left")
        panel[f"ret_{horizon}h"] = panel[f"_exit_{horizon}"] / panel["entry_close"] - 1.0
        panel = panel.drop(columns=["_exit_open", f"_exit_{horizon}"])

    # Querschnittlich zentriert je Scan = marktneutrales Alpha. Der unzentrierte
    # Teil ist gemeinsames Marktbeta und sagt nichts ueber Coin-Selektion.
    for horizon in horizons:
        column = f"ret_{horizon}h"
        panel[f"xs_{horizon}h"] = panel[column] - panel.groupby("ts")[column].transform("mean")

    panel["day"] = panel["ts"].dt.floor("D")
    panel["month"] = panel["ts"].dt.tz_localize(None).dt.to_period("M").astype(str)
    panel["score_rank"] = panel.groupby("ts")["score"].rank(pct=True)
    for column in COMPONENTS + RAW_FEATURES:
        if column in panel.columns:
            panel[f"rank_{column}"] = panel.groupby("ts")[column].rank(pct=True)

    direction_sign = {
        "LONG": 1.0,
        "STRONG_LONG": 1.0,
        "SHORT": -1.0,
        "STRONG_SHORT": -1.0,
        "NEUTRAL": 0.0,
        "NO_TRADE": 0.0,
    }
    panel["dir_sign"] = panel["direction_raw"].map(direction_sign).fillna(0.0)
    return panel


# ---------------------------------------------------------------------------
# IC-Zeitreihen
# ---------------------------------------------------------------------------


def ic_time_series(panel: pd.DataFrame, feature: str, target: str) -> pd.DataFrame:
    """Je Scan-Zeitpunkt eine Querschnitts-IC (Spearman) ueber alle Coins."""
    sub = panel[[feature, target, "ts", "day", "month"]].dropna()
    if sub.empty:
        return pd.DataFrame(columns=["ts", "day", "month", "n", "ic"])

    rows = []
    for (ts, day, month), group in sub.groupby(["ts", "day", "month"], sort=True):
        if len(group) < MIN_ASSETS_PER_SCAN:
            continue
        value = spearman(group[feature].to_numpy(dtype=float), group[target].to_numpy(dtype=float))
        if np.isfinite(value):
            rows.append({"ts": ts, "day": day, "month": month, "n": len(group), "ic": value})
    return pd.DataFrame(rows)


def summarize_ic(
    panel: pd.DataFrame,
    feature: str,
    target: str,
    horizon_h: int,
    rng: np.random.Generator,
) -> dict[str, Any] | None:
    """Primaerer IC-Schaetzer: Mittel der Querschnitts-ICs, Bootstrap ueber Tage."""
    series = ic_time_series(panel, feature, target)
    if len(series) < 5:
        return None

    boot = block_bootstrap_mean(series["ic"].to_numpy(), series["day"].to_numpy(), rng=rng)

    # Gepoolte Variante zum Vergleich: alle Zeilen, Cluster-Bootstrap ueber Tage.
    pooled_sub = panel[[feature, target, "day"]].dropna()
    pooled_ic = float("nan")
    pooled = {"ci_low": float("nan"), "ci_high": float("nan"), "p_two_sided": float("nan")}
    if len(pooled_sub) >= 200:
        x = ranks(pooled_sub[feature].to_numpy(dtype=float))
        y = ranks(pooled_sub[target].to_numpy(dtype=float))
        pooled_ic = pearson(x, y)
        if pooled_sub["day"].nunique() >= MIN_BOOTSTRAP_BLOCKS:
            pooled = bootstrap_corr(x, y, pooled_sub["day"].to_numpy(), n_boot=2000, rng=rng)

    ics = series["ic"].to_numpy()
    n_indep = count_non_overlapping(series["ts"], horizon_h)
    # t-Statistik konservativ auf die Zahl unabhaengiger Fenster deflationiert.
    sd = float(ics.std(ddof=1)) if len(ics) > 1 else float("nan")
    t_deflated = (
        float(ics.mean() / (sd / math.sqrt(max(n_indep, 1))))
        if sd and np.isfinite(sd) and sd > 0
        else float("nan")
    )

    return {
        "feature": feature,
        "target": target,
        "horizon_h": horizon_h,
        "n_rows": len(pooled_sub),
        "n_scans": len(series),
        "n_days": int(series["day"].nunique()),
        "n_non_overlapping": int(n_indep),
        "mean_ic": float(ics.mean()),
        "median_ic": float(np.median(ics)),
        "std_ic": sd,
        "t_deflated": t_deflated,
        "share_positive": float((ics > 0).mean()),
        "ci_low": boot["ci_low"],
        "ci_high": boot["ci_high"],
        "p_block": boot["p"],
        "pooled_ic": pooled_ic,
        "pooled_ci_low": pooled["ci_low"],
        "pooled_ci_high": pooled["ci_high"],
        "pooled_p": pooled["p_two_sided"],
        "pooled_p_iid_naive": fisher_pvalue(pooled_ic, len(pooled_sub)),
        "significant_block": bool(
            np.isfinite(boot["ci_low"])
            and np.isfinite(boot["ci_high"])
            and boot["ci_low"] * boot["ci_high"] > 0
        ),
    }


def monthly_ic(panel: pd.DataFrame, feature: str, target: str) -> dict[str, Any]:
    """IC je Kalendermonat. Ein echter Edge behaelt sein Vorzeichen ueber Regime."""
    series = ic_time_series(panel, feature, target)
    if series.empty:
        return {"months": 0, "series": []}

    rows = []
    for month, group in series.groupby("month"):
        rows.append(
            {
                "month": str(month),
                "n_scans": len(group),
                "mean_ic": float(group["ic"].mean()),
                "share_positive": float((group["ic"] > 0).mean()),
            }
        )
    values = np.array([r["mean_ic"] for r in rows])
    overall_sign = np.sign(values.mean()) if values.size else 0.0
    return {
        "feature": feature,
        "target": target,
        "months": len(values),
        "mean_ic": float(values.mean()) if values.size else float("nan"),
        "months_same_sign": int((np.sign(values) == overall_sign).sum()),
        "min_ic": float(values.min()) if values.size else float("nan"),
        "max_ic": float(values.max()) if values.size else float("nan"),
        "series": rows,
    }


# ---------------------------------------------------------------------------
# Dezile
# ---------------------------------------------------------------------------


def decile_table(
    panel: pd.DataFrame,
    feature: str,
    target: str,
    rng: np.random.Generator,
    n_buckets: int = N_DECILES,
) -> dict[str, Any] | None:
    """Dezilkurve mit Cluster-Bootstrap-CIs; Cluster = Tag."""
    sub = panel[[feature, target, "day", "ts"]].dropna()
    if len(sub) < n_buckets * 50:
        return None
    try:
        buckets = pd.qcut(sub[feature].rank(method="first"), n_buckets, labels=False)
    except ValueError:
        return None

    values = sub[target].to_numpy(dtype=float)
    if sub["day"].nunique() >= MIN_BOOTSTRAP_BLOCKS:
        lows, highs, spread = bootstrap_bucket_means(
            values,
            buckets.to_numpy(dtype=int),
            sub["day"].to_numpy(),
            n_buckets,
            n_boot=4000,
            rng=rng,
        )
    else:
        lows = np.full(n_buckets, np.nan)
        highs = np.full(n_buckets, np.nan)
        spread = np.array([np.nan, np.nan, np.nan])
    grouped = sub.assign(_b=buckets).groupby("_b")
    means = grouped[target].mean().to_numpy()
    medians = grouped[target].median().to_numpy()
    counts = grouped[target].size().to_numpy()
    feature_low = grouped[feature].min().to_numpy()
    feature_high = grouped[feature].max().to_numpy()

    return {
        "feature": feature,
        "target": target,
        "n": len(sub),
        "n_days": int(sub["day"].nunique()),
        "n_scans": int(sub["ts"].nunique()),
        "buckets": [
            {
                "bucket": int(i + 1),
                "feature_min": float(feature_low[i]),
                "feature_max": float(feature_high[i]),
                "n": int(counts[i]),
                "mean_ret": float(means[i]),
                "median_ret": float(medians[i]),
                "ci_low": float(lows[i]),
                "ci_high": float(highs[i]),
            }
            for i in range(n_buckets)
        ],
        "top_minus_bottom": {
            "value": float(means[-1] - means[0]),
            "ci_low": float(spread[0]),
            "ci_high": float(spread[1]),
            "p_cluster": float(spread[2]),
            "significant": bool(np.isfinite(spread[0]) and spread[0] * spread[1] > 0),
        },
        "monotonicity_spearman": float(spearman(np.arange(n_buckets, dtype=float), means)),
    }


# ---------------------------------------------------------------------------
# Richtung, Regime, Filter
# ---------------------------------------------------------------------------


def direction_stats(
    panel: pd.DataFrame, horizon: int, rng: np.random.Generator
) -> list[dict[str, Any]]:
    """Verdient die diskrete Richtungsentscheidung Geld? Mit ehrlichen n."""
    raw_col, xs_col = f"ret_{horizon}h", f"xs_{horizon}h"
    out = []
    for label, mask, sign in (
        ("LONG (inkl. STRONG)", panel["dir_sign"] > 0, 1.0),
        ("NEUTRAL", panel["dir_sign"] == 0, 0.0),
        ("SHORT (inkl. STRONG)", panel["dir_sign"] < 0, -1.0),
    ):
        sub = panel[mask & panel[raw_col].notna()]
        if sub.empty:
            continue
        signed_raw = sub[raw_col] * sign if sign else sub[raw_col]
        signed_xs = sub[xs_col] * sign if sign else sub[xs_col]
        boot = block_bootstrap_mean(
            signed_xs.to_numpy(dtype=float), sub["day"].to_numpy(), n_boot=2000, rng=rng
        )
        out.append(
            {
                "group": label,
                "n": len(sub),
                "n_days": int(sub["day"].nunique()),
                "n_scans": int(sub["ts"].nunique()),
                "n_non_overlapping": count_non_overlapping(sub["ts"], horizon),
                "mean_raw_ret": float(sub[raw_col].mean()),
                "mean_signed_ret": float(signed_raw.mean()),
                "mean_signed_xs_ret": float(signed_xs.mean()),
                "xs_ci_low": boot["ci_low"],
                "xs_ci_high": boot["ci_high"],
                "xs_p": boot["p"],
                "significant": bool(
                    np.isfinite(boot["ci_low"]) and boot["ci_low"] * boot["ci_high"] > 0
                ),
                "hit_rate_signed": float((signed_raw > 0).mean()) if sign else float("nan"),
                "hit_rate_signed_xs": float((signed_xs > 0).mean()) if sign else float("nan"),
            }
        )
    return out


def directional_edge(
    panel: pd.DataFrame, horizon: int, rng: np.random.Generator
) -> dict[str, Any]:
    """Der entscheidende Test.

    Der Score ist ein Ueberzeugungsmass, keine Richtungsprognose -- und 2/3 der Signale
    sind SHORT. Ein negativer IC gegen die *unsignierte* Rendite kann daher genau das
    Gegenteil bedeuten: dass der Score auf der Short-Seite richtig liegt. Hier wird der
    Score deshalb gegen die mit der eigenen Signalrichtung signierte Rendite geprueft und
    zusaetzlich innerhalb der Long- und Short-Teilmengen getrennt.
    """
    xs_col = f"xs_{horizon}h"
    work = panel[panel[xs_col].notna()].copy()
    directional = work[work["dir_sign"] != 0].copy()
    if len(directional) < 2000:
        return {"available": False, "reason": "zu wenige gerichtete Signale"}

    directional["signed_xs"] = directional[xs_col] * directional["dir_sign"]
    # Gerichteter Score: stark-long soll stark-short im Querschnitt schlagen.
    directional["dir_score"] = directional["score"] * directional["dir_sign"]

    out: dict[str, Any] = {"available": True, "n_directional": len(directional)}

    out["score_vs_signed"] = summarize_ic(directional, "score", "signed_xs", horizon, rng)
    out["dirscore_vs_xs"] = summarize_ic(directional, "dir_score", xs_col, horizon, rng)

    longs = directional[directional["dir_sign"] > 0]
    shorts = directional[directional["dir_sign"] < 0]
    out["score_within_long"] = (
        summarize_ic(longs, "score", xs_col, horizon, rng) if len(longs) > 2000 else None
    )
    out["score_within_short"] = (
        summarize_ic(shorts, "score", xs_col, horizon, rng) if len(shorts) > 2000 else None
    )

    # Zahlt sich mehr Ueberzeugung aus? Score-Quintile gegen die signierte Rendite.
    buckets = []
    try:
        directional["conv_bucket"] = pd.qcut(
            directional["score"], 5, labels=False, duplicates="drop"
        )
    except ValueError:
        directional["conv_bucket"] = np.nan
    for bucket, group in directional.dropna(subset=["conv_bucket"]).groupby("conv_bucket"):
        boot = block_bootstrap_mean(
            group["signed_xs"].to_numpy(dtype=float), group["day"].to_numpy(), n_boot=2000, rng=rng
        )
        buckets.append(
            {
                "bucket": int(bucket) + 1,
                "score_low": float(group["score"].min()),
                "score_high": float(group["score"].max()),
                "n": len(group),
                "n_days": int(group["day"].nunique()),
                "mean_signed_xs": float(group["signed_xs"].mean()),
                "median_signed_xs": float(group["signed_xs"].median()),
                "hit_rate": float((group["signed_xs"] > 0).mean()),
                "ci_low": boot["ci_low"],
                "ci_high": boot["ci_high"],
                "p": boot["p"],
                "significant": bool(
                    np.isfinite(boot["ci_low"]) and boot["ci_low"] * boot["ci_high"] > 0
                ),
            }
        )
    out["conviction_buckets"] = buckets
    out["conviction_monotonicity"] = (
        spearman(
            np.array([b["bucket"] for b in buckets], dtype=float),
            np.array([b["mean_signed_xs"] for b in buckets], dtype=float),
        )
        if len(buckets) >= 3
        else float("nan")
    )

    # Nur die tatsaechlich gehandelten STRONG-Signale (Score-Gate der Live-Engine).
    strong = directional[directional["direction_raw"].str.startswith("STRONG")]
    if len(strong) >= 200:
        boot = block_bootstrap_mean(
            strong["signed_xs"].to_numpy(dtype=float),
            strong["day"].to_numpy(),
            n_boot=2000,
            rng=rng,
        )
        out["strong_only"] = {
            "n": len(strong),
            "n_days": int(strong["day"].nunique()),
            "n_non_overlapping": count_non_overlapping(strong["ts"], horizon),
            "mean_signed_xs": float(strong["signed_xs"].mean()),
            "median_signed_xs": float(strong["signed_xs"].median()),
            "hit_rate": float((strong["signed_xs"] > 0).mean()),
            "ci_low": boot["ci_low"],
            "ci_high": boot["ci_high"],
            "p": boot["p"],
            "significant": bool(
                np.isfinite(boot["ci_low"]) and boot["ci_low"] * boot["ci_high"] > 0
            ),
        }
        for label, sub in (("STRONG_LONG", strong[strong["dir_sign"] > 0]),
                           ("STRONG_SHORT", strong[strong["dir_sign"] < 0])):
            if len(sub) < 100:
                continue
            b = block_bootstrap_mean(
                sub["signed_xs"].to_numpy(dtype=float), sub["day"].to_numpy(), n_boot=2000, rng=rng
            )
            out[f"strong_{label.lower()}"] = {
                "n": len(sub),
                "n_days": int(sub["day"].nunique()),
                "mean_signed_xs": float(sub["signed_xs"].mean()),
                "hit_rate": float((sub["signed_xs"] > 0).mean()),
                "ci_low": b["ci_low"],
                "ci_high": b["ci_high"],
                "p": b["p"],
                "significant": bool(np.isfinite(b["ci_low"]) and b["ci_low"] * b["ci_high"] > 0),
            }
    return out


def regime_analysis(
    panel: pd.DataFrame,
    closes: pd.DataFrame,
    timeframe: str,
    horizon: int,
    rng: np.random.Generator,
) -> dict[str, Any]:
    """IC getrennt nach BTC-Trend und Marktbreite."""
    result: dict[str, Any] = {}
    btc_ids = panel.loc[panel["symbol"].str.upper().str.startswith("BTC"), "asset_id"].unique()
    if btc_ids.size == 0:
        return {"available": False, "reason": "kein BTC-Asset im Panel"}

    btc = closes[closes["asset_id"] == btc_ids[0]].set_index("open_time")["close"].sort_index()
    bars_24h = max(1, round(24 / TF_HOURS[timeframe]))
    bars_7d = max(1, round(24 * 7 / TF_HOURS[timeframe]))
    btc_24h = btc.pct_change(bars_24h)
    btc_7d = btc.pct_change(bars_7d)

    work = panel.copy()
    work["btc_trend_24h"] = work["entry_open_time"].map(btc_24h)
    work["btc_trend_7d"] = work["entry_open_time"].map(btc_7d)
    work["breadth"] = work.groupby("ts")[f"ret_{horizon}h"].transform(lambda s: (s > 0).mean())

    target = f"xs_{horizon}h"
    splits = {
        "BTC 7d < 0 (Baerenphase)": work["btc_trend_7d"] < 0,
        "BTC 7d >= 0 (Bullenphase)": work["btc_trend_7d"] >= 0,
        "BTC 24h < 0": work["btc_trend_24h"] < 0,
        "BTC 24h >= 0": work["btc_trend_24h"] >= 0,
        "Breite < 0.5 (mehrheitlich fallend)": work["breadth"] < 0.5,
        "Breite >= 0.5 (mehrheitlich steigend)": work["breadth"] >= 0.5,
    }
    result["available"] = True
    result["splits"] = {}
    for name, mask in splits.items():
        sub = work[mask]
        summary = summarize_ic(sub, "score", target, horizon, rng) if len(sub) > 2000 else None
        result["splits"][name] = summary or {"n_rows": len(sub), "note": "zu wenig Daten"}

    result["btc_trend_7d_share_negative"] = float((work["btc_trend_7d"] < 0).mean())
    result["btc_range"] = {
        "min_7d": float(np.nanmin(work["btc_trend_7d"]))
        if work["btc_trend_7d"].notna().any()
        else None,
        "max_7d": float(np.nanmax(work["btc_trend_7d"]))
        if work["btc_trend_7d"].notna().any()
        else None,
    }
    return result


def compare_scoring_variants(
    panel: pd.DataFrame,
    other: pd.DataFrame,
    horizons: Sequence[int],
    rng: np.random.Generator,
) -> dict[str, Any]:
    """Was hat der Phase-1-Fix (Commit 6df2f4a) tatsaechlich gebracht?

    Verglichen werden zwei Panels ueber identische (Asset, Zeitpunkt)-Paare:
    das aktuelle Scoring und das Scoring davor. Interessant sind drei Dinge —
    der Richtungsbias, die Score-Verteilung und die Vorhersagekraft. Nur das
    Dritte entscheidet, ob der Fix mehr als Kosmetik war.
    """
    keys = ["asset_id", "ts"]
    columns = [*keys, "score", "direction_raw"]
    merged = panel[columns].merge(other[columns], on=keys, suffixes=("_cur", "_pre"))
    if merged.empty:
        return {"available": False, "reason": "keine gemeinsamen (Asset, Zeitpunkt)-Paare"}

    def long_short_split(series: pd.Series) -> dict[str, float]:
        longs = int(series.isin(["LONG", "STRONG_LONG"]).sum())
        shorts = int(series.isin(["SHORT", "STRONG_SHORT"]).sum())
        total = longs + shorts
        return {
            "long": longs,
            "short": shorts,
            "long_share": round(longs / total, 4) if total else float("nan"),
            "short_share": round(shorts / total, 4) if total else float("nan"),
        }

    result: dict[str, Any] = {
        "available": True,
        "matched_rows": len(merged),
        "score_correlation": float(merged["score_cur"].rank().corr(merged["score_pre"].rank())),
        "score_mean_current": float(merged["score_cur"].mean()),
        "score_mean_prefix": float(merged["score_pre"].mean()),
        "score_median_current": float(merged["score_cur"].median()),
        "score_median_prefix": float(merged["score_pre"].median()),
        "mean_shift": float((merged["score_cur"] - merged["score_pre"]).mean()),
        "long_short_current": long_short_split(merged["direction_raw_cur"]),
        "long_short_prefix": long_short_split(merged["direction_raw_pre"]),
        "ic": {},
    }

    # IC beider Varianten auf demselben Zeilensatz, damit der Vergleich fair ist.
    other_returns = other.set_index(keys)
    aligned = panel.set_index(keys).copy()
    aligned["score_pre"] = other_returns["score"]
    aligned = aligned.reset_index()
    for horizon in horizons:
        target = f"xs_{horizon}h"
        current = summarize_ic(aligned, "score", target, horizon, rng)
        prefix = summarize_ic(
            aligned.dropna(subset=["score_pre"]), "score_pre", target, horizon, rng
        )
        result["ic"][f"{horizon}h"] = {
            "current": current,
            "prefix": prefix,
            "delta_ic": (
                float(current["mean_ic"] - prefix["mean_ic"])
                if current and prefix
                else float("nan")
            ),
        }
    return result


def atr_factor_check(panel: pd.DataFrame, horizon: int, rng: np.random.Generator) -> dict[str, Any]:
    """Faellt der frueher gefundene ATR-Effekt auch auf tiefer Historie an?

    Zwei Fragen getrennt halten: (a) traegt realisierte Volatilitaet als *Faktor*
    Information, (b) verbessert ein ATR-*Filter* den Score-Edge. (b) ist die
    gefaehrlichere Frage, weil jeder Filter auf genug Varianten irgendwann gut
    aussieht — deshalb wird hier nur ein einziger, vorab benannter Schnitt am
    Median getestet und keine Schwellenwertsuche gefahren.
    """
    target = f"xs_{horizon}h"
    out: dict[str, Any] = {}
    factor = summarize_ic(panel, "atr_percent", target, horizon, rng)
    out["atr_percent_factor"] = factor
    out["atr_percent_monthly"] = monthly_ic(panel, "atr_percent", target)
    out["atr_percent_deciles"] = decile_table(panel, "atr_percent", target, rng)

    median_atr = float(panel["atr_percent"].median())
    out["median_atr_percent"] = median_atr
    for name, mask in (
        ("ruhige Coins (ATR <= Median)", panel["atr_percent"] <= median_atr),
        ("bewegte Coins (ATR > Median)", panel["atr_percent"] > median_atr),
    ):
        sub = panel[mask]
        summary = summarize_ic(sub, "score", target, horizon, rng)
        out[f"score_ic_{name}"] = summary or {"n_rows": len(sub)}
    return out


# ---------------------------------------------------------------------------
# Orchestrierung
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> dict[str, Any]:
    rng = np.random.default_rng(RNG_SEED)
    horizons = [int(h) for h in args.horizons.split(",")]

    panel, meta = load_panel(args.data_dir, args.tag)
    closes = load_close_lookup(args.data_dir, args.return_timeframe)
    panel = attach_forward_returns(panel, closes, args.return_timeframe, horizons)

    if args.start:
        panel = panel[panel["ts"] >= pd.Timestamp(args.start, tz="UTC")]
    if args.end:
        panel = panel[panel["ts"] <= pd.Timestamp(args.end, tz="UTC")]
    if args.min_data_quality > 0:
        panel = panel[panel["data_quality"] >= args.min_data_quality]

    coverage = {
        "tag": args.tag,
        "regeneration_meta": meta,
        "return_timeframe": args.return_timeframe,
        "horizons_h": horizons,
        "rows": len(panel),
        "assets": int(panel["asset_id"].nunique()),
        "scans": int(panel["ts"].nunique()),
        "days": int(panel["day"].nunique()),
        "months": int(panel["month"].nunique()),
        "window_start": str(panel["ts"].min()),
        "window_end": str(panel["ts"].max()),
        "usable_rows": {f"{h}h": int(panel[f"ret_{h}h"].notna().sum()) for h in horizons},
        "non_overlapping_scans": {
            f"{h}h": count_non_overlapping(panel.loc[panel[f"ret_{h}h"].notna(), "ts"], h)
            for h in horizons
        },
        "direction_raw_counts": panel["direction_raw"].value_counts().to_dict(),
        "direction_counts": panel["direction"].value_counts().to_dict(),
        "score_quantiles": {
            str(q): float(panel["score"].quantile(q)) for q in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0)
        },
    }

    features = ["score", "score_rank"] + [c for c in COMPONENTS if c in panel.columns]
    features += [f for f in RAW_FEATURES if f in panel.columns]

    ic_rows: list[dict[str, Any]] = []
    for horizon in horizons:
        for target, label in ((f"xs_{horizon}h", "xs"), (f"ret_{horizon}h", "raw")):
            for feature in features:
                summary = summarize_ic(panel, feature, target, horizon, rng)
                if summary:
                    summary["target_type"] = label
                    ic_rows.append(summary)

    # Multiples Testen: nur die marktneutralen Tests bilden die Hauptfamilie.
    primary = [row for row in ic_rows if row["target_type"] == "xs"]
    rejected, cutoff = benjamini_hochberg([row["p_block"] for row in primary])
    for row, flag in zip(primary, rejected, strict=False):
        row["bh_significant"] = bool(flag)
    for row in ic_rows:
        row.setdefault("bh_significant", False)
    multiple_testing = {
        "n_tests_primary": len(primary),
        "bh_alpha": 0.05,
        "bh_pvalue_cutoff": cutoff,
        "n_bh_significant": int(rejected.sum()),
        "n_raw_p_below_05": int(
            sum(1 for r in primary if np.isfinite(r["p_block"]) and r["p_block"] < 0.05)
        ),
        "expected_false_positives_at_05": round(0.05 * len(primary), 1),
    }

    deciles: dict[str, Any] = {}
    for horizon in horizons:
        for feature in ("score", "score_rank"):
            for target, label in ((f"xs_{horizon}h", "xs"), (f"ret_{horizon}h", "raw")):
                table = decile_table(panel, feature, target, rng)
                if table:
                    deciles[f"{feature}|{label}|{horizon}h"] = table
    for component in COMPONENTS:
        if component in panel.columns:
            table = decile_table(panel, component, f"xs_{args.focus_horizon}h", rng)
            if table:
                deciles[f"{component}|xs|{args.focus_horizon}h"] = table

    monthly = {}
    for horizon in horizons:
        for feature in ["score"] + [c for c in COMPONENTS if c in panel.columns] + ["atr_percent"]:
            monthly[f"{feature}|xs|{horizon}h"] = monthly_ic(panel, feature, f"xs_{horizon}h")

    ic_series_export = {}
    for horizon in horizons:
        series = ic_time_series(panel, "score", f"xs_{horizon}h")
        ic_series_export[f"score|xs|{horizon}h"] = [
            {"ts": str(r.ts), "n": int(r.n), "ic": float(r.ic)} for r in series.itertuples()
        ]

    directions = {f"{h}h": direction_stats(panel, h, rng) for h in horizons}
    directional = {f"{h}h": directional_edge(panel, h, rng) for h in horizons}
    regimes = regime_analysis(panel, closes, args.return_timeframe, args.focus_horizon, rng)
    atr = atr_factor_check(panel, args.focus_horizon, rng)

    variants: dict[str, Any] = {"available": False}
    if args.compare_tag:
        other_panel, _ = load_panel(args.data_dir, args.compare_tag)
        other_panel = attach_forward_returns(other_panel, closes, args.return_timeframe, horizons)
        variants = compare_scoring_variants(panel, other_panel, horizons, rng)
        variants["compare_tag"] = args.compare_tag

    payload = {
        "meta": {
            "generated_for": "DATAMIND",
            "question": "Sagt der Signal-Score zukuenftige Renditen vorher?",
            "script": "scripts/analyze_deep_edge.py",
            "method": {
                "panel": "regenerierter Produktions-Score ueber tiefe Kerzenhistorie",
                "entry": "reference_price = Close der letzten zum Scan geschlossenen Kerze",
                "exit": "Close der Kerze, die H Stunden nach dem Scan schliesst",
                "targets": {
                    "xs": "je Scan quer-schnittlich zentriert (marktneutral)",
                    "raw": "unzentriert (enthaelt Marktbeta)",
                },
                "primary_estimator": "Mittel der Querschnitts-ICs je Scan",
                "inference": f"Block-Bootstrap ueber Tage, {N_BOOT} Resamples",
                "multiple_testing": "Benjamini-Hochberg ueber die xs-Testfamilie",
            },
        },
        "coverage": coverage,
        "multiple_testing": multiple_testing,
        "information_coefficients": ic_rows,
        "deciles": deciles,
        "monthly_ic": monthly,
        "ic_series": ic_series_export,
        "directions": directions,
        "directional_edge": directional,
        "regimes": regimes,
        "atr_check": atr,
        "scoring_variants": variants,
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / f"deep_edge_{args.tag}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    (args.out_dir / f"deep_edge_{args.tag}.txt").write_text(render_text(payload), encoding="utf-8")
    return payload


def _pct(value: float | None) -> str:
    if value is None or not np.isfinite(value):
        return "   n/a "
    return f"{value * 100:+7.4f}%"


def render_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    add = lines.append
    cov = payload["coverage"]

    add("=" * 100)
    add("SCORE-EDGE AUF TIEFER HISTORIE  --  Sagt der Score zukuenftige Renditen vorher?")
    add("=" * 100)
    add(f"Panel            : {cov['tag']}   Return-Timeframe {cov['return_timeframe']}")
    add(f"Fenster          : {cov['window_start']}  bis  {cov['window_end']}")
    add(
        f"Umfang           : {cov['rows']:,} Zeilen | {cov['assets']} Assets | "
        f"{cov['scans']} Scans | {cov['days']} Tage | {cov['months']} Monate"
    )
    add(f"Nutzbar          : {cov['usable_rows']}")
    add(f"Unabh. Scans     : {cov['non_overlapping_scans']}   <-- die ehrliche Stichprobengroesse")
    add(f"Richtungen (roh) : {cov['direction_raw_counts']}")
    add(f"Score-Quantile   : {cov['score_quantiles']}")
    add("")

    mt = payload["multiple_testing"]
    add("-" * 100)
    add("MULTIPLES TESTEN")
    add("-" * 100)
    add(f"  Tests in der Hauptfamilie (marktneutral) : {mt['n_tests_primary']}")
    add(f"  davon p < 0.05 (unkorrigiert)            : {mt['n_raw_p_below_05']}")
    add(f"  unter der Nullhypothese zu erwarten      : {mt['expected_false_positives_at_05']}")
    add(
        f"  nach Benjamini-Hochberg signifikant      : {mt['n_bh_significant']}"
        f"  (p-Schwelle {mt['bh_pvalue_cutoff']:.5f})"
    )
    add("")

    add("-" * 100)
    add("INFORMATION COEFFICIENT  (Mittel der Querschnitts-ICs je Scan, Bootstrap ueber Tage)")
    add("-" * 100)
    add(
        f"{'Feature':<22}{'Hor':>5}{'Ziel':>5}{'Scans':>7}{'Tage':>6}{'unabh':>7}{'IC':>9}"
        f"{'CI (Block)':>22}{'p':>8}{'>0':>7}{'t_defl':>8}  BH"
    )
    for row in payload["information_coefficients"]:
        add(
            f"{row['feature']:<22}{row['horizon_h']:>4}h{row['target_type']:>5}{row['n_scans']:>7}"
            f"{row['n_days']:>6}{row['n_non_overlapping']:>7}{row['mean_ic']:>9.4f}"
            f"   [{row['ci_low']:+.4f}, {row['ci_high']:+.4f}]{row['p_block']:>8.3f}"
            f"{row['share_positive']:>7.2f}{row['t_deflated']:>8.2f}"
            f"  {'JA' if row['bh_significant'] else '--'}"
        )
    add("")

    add("-" * 100)
    add("DEZILE  (Feature -> mittlere Forward-Rendite, CI per Tages-Cluster-Bootstrap)")
    add("-" * 100)
    for key, table in payload["deciles"].items():
        add(
            f"\n[{key}]  n={table['n']:,}  Tage={table['n_days']}  Scans={table['n_scans']}  "
            f"Monotonie={table['monotonicity_spearman']:+.3f}"
        )
        add(f"  {'Dez':<5}{'Bereich':<22}{'n':>8}{'Mittel':>11}{'Median':>11}{'CI (Tage)':>26}")
        for bucket in table["buckets"]:
            span = f"{bucket['feature_min']:.3f}..{bucket['feature_max']:.3f}"
            add(
                f"  {bucket['bucket']:<5}{span:<22}{bucket['n']:>8}"
                f"{_pct(bucket['mean_ret']):>11}{_pct(bucket['median_ret']):>11}"
                f"   [{_pct(bucket['ci_low'])}, {_pct(bucket['ci_high'])}]"
            )
        tmb = table["top_minus_bottom"]
        add(
            f"  Top-minus-Bottom: {_pct(tmb['value'])}"
            f"  CI [{_pct(tmb['ci_low'])}, {_pct(tmb['ci_high'])}]"
            f"  p={tmb['p_cluster']:.3f}"
            f"  {'SIGNIFIKANT' if tmb['significant'] else 'im Rauschen'}"
        )
    add("")

    add("-" * 100)
    add("MONATSSTABILITAET  (IC je Kalendermonat -- haelt der Edge sein Vorzeichen?)")
    add("-" * 100)
    add(f"{'Feature|Ziel|Hor':<30}{'Monate':>8}{'mean IC':>10}{'gleiches Vz.':>14}{'Spanne':>24}")
    for key, entry in payload["monthly_ic"].items():
        if not entry.get("months"):
            continue
        add(
            f"{key:<30}{entry['months']:>8}{entry['mean_ic']:>10.4f}"
            f"{entry['months_same_sign']:>8}/{entry['months']:<5}"
            f"   [{entry['min_ic']:+.4f}, {entry['max_ic']:+.4f}]"
        )
    add("")

    add("-" * 100)
    add("RICHTUNGSENTSCHEIDUNG  (signierte, marktneutrale Rendite)")
    add("-" * 100)
    for horizon, rows in payload["directions"].items():
        add(f"\nHorizont {horizon}")
        for row in rows:
            add(
                f"  {row['group']:<22} n={row['n']:>7,} Tage={row['n_days']:>4}"
                f" unabh={row['n_non_overlapping']:>4}"
                f"  roh={_pct(row['mean_raw_ret'])}  signiert={_pct(row['mean_signed_ret'])}"
                f"  signiert_xs={_pct(row['mean_signed_xs_ret'])}"
                f"  CI [{_pct(row['xs_ci_low'])}, {_pct(row['xs_ci_high'])}]"
                f"  {'SIGNIFIKANT' if row['significant'] else 'im Rauschen'}"
            )
    add("")

    add("-" * 100)
    add("GERICHTETER EDGE  (der entscheidende Test: Score gegen die MIT der Signalrichtung")
    add("signierte Rendite -- ein negativer IC gegen die unsignierte Rendite kann sonst")
    add("bedeuten, dass der Score auf der Short-Seite genau richtig liegt)")
    add("-" * 100)

    def _ic_line(label: str, entry: dict[str, Any] | None) -> None:
        if not entry:
            add(f"  {label:<38} zu wenig Daten")
            return
        add(
            f"  {label:<38} IC={entry['mean_ic']:+.4f}"
            f"  CI [{entry['ci_low']:+.4f}, {entry['ci_high']:+.4f}]"
            f"  p={entry['p_block']:.4f}  Scans={entry['n_scans']:>5}"
            f"  Tage={entry['n_days']:>4}  unabh={entry['n_non_overlapping']:>4}"
            f"  {'SIGNIFIKANT' if entry['significant_block'] else 'im Rauschen'}"
        )

    for horizon, entry in payload["directional_edge"].items():
        add(f"\nHorizont {horizon}")
        if not entry.get("available"):
            add(f"  {entry.get('reason', 'nicht verfuegbar')}")
            continue
        add(f"  gerichtete Signale (ohne NEUTRAL): {entry['n_directional']:,}")
        _ic_line("Score -> signierte xs-Rendite", entry.get("score_vs_signed"))
        _ic_line("Score*Richtung -> xs-Rendite", entry.get("dirscore_vs_xs"))
        _ic_line("Score innerhalb LONG (erw. +)", entry.get("score_within_long"))
        _ic_line("Score innerhalb SHORT (erw. -)", entry.get("score_within_short"))
        buckets = entry.get("conviction_buckets") or []
        if buckets:
            add(
                f"  Ueberzeugungs-Quintile (signierte xs-Rendite), "
                f"Monotonie={entry['conviction_monotonicity']:+.3f}:"
            )
            for bucket in buckets:
                add(
                    f"    Q{bucket['bucket']}  Score {bucket['score_low']:>5.1f}.."
                    f"{bucket['score_high']:>5.1f}"
                    f"  n={bucket['n']:>7,}  Mittel={_pct(bucket['mean_signed_xs'])}"
                    f"  Median={_pct(bucket['median_signed_xs'])}"
                    f"  Trefferquote={bucket['hit_rate']:.3f}"
                    f"  CI [{_pct(bucket['ci_low'])}, {_pct(bucket['ci_high'])}]"
                    f"  {'SIG' if bucket['significant'] else '--'}"
                )
        for key, label in (
            ("strong_only", "nur STRONG (Live-Gate)"),
            ("strong_strong_long", "nur STRONG_LONG"),
            ("strong_strong_short", "nur STRONG_SHORT"),
        ):
            strong = entry.get(key)
            if not strong:
                continue
            add(
                f"  {label:<24} n={strong['n']:>6,}  Tage={strong['n_days']:>4}"
                f"  Mittel={_pct(strong['mean_signed_xs'])}"
                f"  Trefferquote={strong['hit_rate']:.3f}"
                f"  CI [{_pct(strong['ci_low'])}, {_pct(strong['ci_high'])}]"
                f"  p={strong['p']:.4f}"
                f"  {'SIGNIFIKANT' if strong['significant'] else 'im Rauschen'}"
            )
    add("")

    add("-" * 100)
    add("REGIME")
    add("-" * 100)
    regimes = payload["regimes"]
    if regimes.get("available"):
        add(f"{'Split':<40}{'Scans':>7}{'IC':>9}{'CI':>22}{'p':>8}  sig")
        for name, entry in regimes["splits"].items():
            if "mean_ic" not in entry:
                add(f"{name:<40}  {entry.get('note', 'n/a')} (n={entry.get('n_rows')})")
                continue
            add(
                f"{name:<40}{entry['n_scans']:>7}{entry['mean_ic']:>9.4f}"
                f"   [{entry['ci_low']:+.4f}, {entry['ci_high']:+.4f}]{entry['p_block']:>8.3f}"
                f"  {'JA' if entry['significant_block'] else '--'}"
            )
        add(f"  Anteil Scans in BTC-7d-Abwaertsphase: {regimes['btc_trend_7d_share_negative']:.3f}")
    else:
        add(f"  nicht verfuegbar: {regimes.get('reason')}")
    add("")

    add("-" * 100)
    add("ATR-BEFUND  (realisierte Volatilitaet als Faktor -- Nachpruefung des frueheren Fundes)")
    add("-" * 100)
    atr = payload["atr_check"]
    factor = atr.get("atr_percent_factor")
    if factor:
        add(
            f"  atr_percent als Faktor : IC={factor['mean_ic']:+.4f}  "
            f"CI [{factor['ci_low']:+.4f}, {factor['ci_high']:+.4f}]  p={factor['p_block']:.4f}  "
            f"Scans={factor['n_scans']}  Tage={factor['n_days']}  "
            f"{'SIGNIFIKANT' if factor['significant_block'] else 'im Rauschen'}"
        )
    month = atr.get("atr_percent_monthly", {})
    if month.get("months"):
        add(
            f"  Monatsstabilitaet      : {month['months_same_sign']}/{month['months']} Monate "
            f"gleiches Vorzeichen, Spanne [{month['min_ic']:+.4f}, {month['max_ic']:+.4f}]"
        )
    add(f"  Median-ATR             : {atr.get('median_atr_percent', float('nan')):.3f}%")
    for key, entry in atr.items():
        if key.startswith("score_ic_") and isinstance(entry, dict) and "mean_ic" in entry:
            add(
                f"  Score-IC bei {key.replace('score_ic_', ''):<28}: {entry['mean_ic']:+.4f}"
                f"  CI [{entry['ci_low']:+.4f}, {entry['ci_high']:+.4f}]  p={entry['p_block']:.3f}"
            )

    variants = payload.get("scoring_variants", {})
    if variants.get("available"):
        add("")
        add("-" * 100)
        add(f"PHASE-1-FIX (6df2f4a): aktuell vs. Vorgaenger [{variants.get('compare_tag')}]")
        add("-" * 100)
        add(f"  gematchte Zeilen        : {variants['matched_rows']:,}")
        add(
            f"  Score im Mittel         : aktuell {variants['score_mean_current']:.2f}"
            f"  vorher {variants['score_mean_prefix']:.2f}"
            f"  (Verschiebung {variants['mean_shift']:+.2f})"
        )
        add(f"  Rangkorrelation         : {variants['score_correlation']:.4f}")
        cur, pre = variants["long_short_current"], variants["long_short_prefix"]
        add(
            f"  LONG/SHORT aktuell      : {cur['long']:,} / {cur['short']:,}  "
            f"= {cur['long_share'] * 100:.1f}% / {cur['short_share'] * 100:.1f}%"
        )
        add(
            f"  LONG/SHORT vorher       : {pre['long']:,} / {pre['short']:,}  "
            f"= {pre['long_share'] * 100:.1f}% / {pre['short_share'] * 100:.1f}%"
        )
        for horizon, entry in variants["ic"].items():
            current, prefix = entry.get("current"), entry.get("prefix")
            if not current or not prefix:
                continue
            add(
                f"  IC {horizon:<4} aktuell {current['mean_ic']:+.4f} "
                f"[{current['ci_low']:+.4f}, {current['ci_high']:+.4f}]   "
                f"vorher {prefix['mean_ic']:+.4f} "
                f"[{prefix['ci_low']:+.4f}, {prefix['ci_high']:+.4f}]   "
                f"Differenz {entry['delta_ic']:+.4f}"
            )
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--tag", default="live_current", help="Panel-Tag aus der Regeneration")
    parser.add_argument("--return-timeframe", default="1h", choices=sorted(TF_HOURS))
    parser.add_argument("--horizons", default="4,8,24", help="Komma-Liste in Stunden")
    parser.add_argument("--focus-horizon", type=int, default=24)
    parser.add_argument("--start", default="")
    parser.add_argument("--end", default="")
    parser.add_argument("--min-data-quality", type=float, default=0.0)
    parser.add_argument(
        "--compare-tag",
        default="",
        help="Zweites Panel (z. B. pre-Phase-1-Scoring) fuer den Variantenvergleich",
    )
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args(list(argv) if argv is not None else None)

    payload = run(args)
    print(render_text(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
