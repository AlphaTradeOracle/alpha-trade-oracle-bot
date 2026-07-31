"""Traegt realisierte Volatilitaet einen handelbaren Faktor? — Charakterisierung.

Ausgangslage
------------
``scripts/analyze_deep_edge.py`` hat auf 366 Tagen 4h-Historie genau ein Merkmal
mit stabilem Vorzeichen gefunden: ``atr_percent``, IC -0.074 auf 24h, 13/13
Monate gleiches Vorzeichen. Negativer IC heisst: ruhige Coins landen im
Querschnitt **auf besseren Raengen** als bewegte.

Warum ein negativer IC noch keine Strategie ist
-----------------------------------------------
Ein Rang-IC ist eine Aussage ueber die *Mitte* der Verteilung. Gehandelt wird
aber der *Mittelwert*: Erwartungswert je Trade = Mittel der realisierten
Renditen. Bei rechtsschiefen Verteilungen — und Krypto-Renditen sind massiv
rechtsschief — koennen Rang und Mittelwert **entgegengesetzte** Vorzeichen
haben. Genau das ist hier die entscheidende Frage, und deshalb berichtet dieses
Skript zu jedem Quantil Mittelwert *und* Median *und* Schiefe *und* den Beitrag
der extremsten Beobachtungen.

Was geprueft wird
-----------------
1. **Definition**  — ATR vs. realisierte Vola verschiedener Lookbacks vs.
   Parkinson-Schaetzer; absolut vs. Querschnittsrang.
2. **Form**        — Quantilkurve: monoton oder von einem Eimer getrieben?
   Zusaetzlich derselbe Test ohne das oberste Quantil.
3. **Horizont**    — 4h, 8h, 24h, 72h, 120h.
4. **Liquiditaet** — haelt der Effekt nach Ausschluss der illiquidesten Coins?
5. **Eigenstaendigkeit** — Fama-MacBeth-Querschnittsregressionen mit Kontrolle
   fuer Marktkapitalisierungsrang und Dollarvolumen; Doppelsortierungen.
6. **Mean Reversion** — RSI, ROC und Querschnitts-Reversal einzeln und in
   Kombination mit dem Vola-Faktor.
7. **Kostenhuerde** — was muss der Spread liefern, damit 0.05% je Seite
   ueberlebt werden, und ab welcher Haltedauer traegt er sich.

Look-ahead-Freiheit
-------------------
Alle Merkmale stammen aus Kerzen mit ``open_time <= ts - 4h``, also aus zum
Entscheidungszeitpunkt geschlossenen Kerzen. Forward Returns beginnen strikt
danach. Inferenz per Block-Bootstrap ueber Tage; ausgewiesen wird immer die
Zahl nicht-ueberlappender Fenster, nicht die Zeilenzahl.

Aufruf
------
    python scripts/analyze_lowvol_factor.py
    python scripts/analyze_lowvol_factor.py --tag long_current --horizons 4,8,24,72,120
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.analyze_deep_edge import (  # noqa: E402
    TF_HOURS,
    block_bootstrap_mean,
    count_non_overlapping,
    load_panel,
)
from scripts.analyze_score_edge import ranks, spearman  # noqa: E402
from scripts.regenerate_historical_signals import load_candle_arrays  # noqa: E402

DATA_DIR = REPO_ROOT / "exports" / "edge_data"
OUT_DIR = REPO_ROOT / "exports"

RNG_SEED = 20260731
MIN_ASSETS_PER_SCAN = 30

#: Rundlauf-Gebuehr in Prozent des Notionals: 0.05% je Seite, Perp-Taker.
FEE_PER_SIDE_PCT = 0.05
FEE_ROUND_TRIP_PCT = 2 * FEE_PER_SIDE_PCT

#: Stop-Definition der Live-Engine (``RiskConfig.atr_multiplier``). 1R = dieser
#: Abstand, daher rechnet er jede Prozentbewegung in R um.
ATR_STOP_MULTIPLIER = 1.5

#: Kandidaten-Definitionen des Vola-Faktors. ``atr_percent`` ist die Version,
#: die die Live-Engine ohnehin berechnet; die anderen pruefen, ob der Befund an
#: dieser speziellen Definition haengt.
VOL_FEATURES = [
    "atr_percent",
    "rv_6",
    "rv_14",
    "rv_30",
    "rv_60",
    "parkinson_30",
    "bb_width_rel",
]

#: Mean-Reversion-Kandidaten. Alle Momentum-Indikatoren waren invertiert, was
#: kurzfristige Rueckkehr zum Mittel nahelegt.
REVERSION_FEATURES = ["rsi_14", "roc_14", "past_6", "past_18", "past_30"]

NS_PER_HOUR = 3_600_000_000_000
MIN_STOP_PERCENT = 0.3
MAX_STOP_PERCENT = 8.0
MIN_DATA_QUALITY = 60.0


# ---------------------------------------------------------------------------
# Merkmale aus Kerzen — ausschliesslich aus geschlossenen Kerzen
# ---------------------------------------------------------------------------


def load_candles(data_dir: Path, timeframe: str) -> pd.DataFrame:
    path = data_dir / f"deep_candles_{timeframe}.csv.gz"
    frame = pd.read_csv(path, parse_dates=["open_time"])
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    return frame.sort_values(["asset_id", "open_time"], ignore_index=True)


def build_candle_features(candles: pd.DataFrame) -> pd.DataFrame:
    """Vola-, Liquiditaets- und Reversal-Merkmale je Kerze.

    Jeder Wert in Zeile ``t`` benutzt ausschliesslich Kerzen bis einschliesslich
    ``t``. Beim Anhaengen wird ``t`` auf die letzte zum Scan geschlossene Kerze
    gesetzt, damit kein Wert aus der Zukunft einfliesst.
    """
    out = candles[["asset_id", "open_time", "open", "high", "low", "close", "volume"]].copy()
    # Nullpreise sind Luecken im Backfill, keine Marktdaten. Sie wuerden sonst
    # als unendliche Log-Renditen in jede Vola-Schaetzung durchschlagen.
    for column in ("open", "high", "low", "close"):
        out.loc[out[column] <= 0, column] = np.nan
    grouped = out.groupby("asset_id", sort=False)

    out["dollar_volume"] = out["close"] * out["volume"]
    out["adv_30"] = grouped["dollar_volume"].transform(
        lambda s: s.rolling(30, min_periods=10).mean()
    )
    out["log_ret"] = grouped["close"].transform(lambda s: np.log(s).diff())

    # Realisierte Vola in Prozent je Bar, damit sie mit atr_percent vergleichbar
    # ist (beides "typische Bewegung einer Kerze in Prozent").
    for window in (6, 14, 30, 60):
        out[f"rv_{window}"] = (
            grouped["log_ret"].transform(lambda s, w=window: s.rolling(w, min_periods=max(5, w // 2)).std())
            * 100.0
        )

    # Parkinson: nutzt die Bar-Spanne und ist damit effizienter als die
    # Close-to-Close-Schaetzung, reagiert aber empfindlicher auf Wicks.
    with np.errstate(divide="ignore", invalid="ignore"):
        hl = np.log(out["high"] / out["low"]) ** 2
    out["_hl"] = hl.replace([np.inf, -np.inf], np.nan)
    out["parkinson_30"] = (
        np.sqrt(
            out.groupby("asset_id", sort=False)["_hl"].transform(
                lambda s: s.rolling(30, min_periods=15).mean()
            )
            / (4.0 * np.log(2.0))
        )
        * 100.0
    )
    out = out.drop(columns=["_hl"])

    # Vergangene Rendite = Reversal-Kandidat. 6/18/30 Bars = 24h/72h/120h.
    for window in (6, 18, 30):
        out[f"past_{window}"] = grouped["close"].transform(
            lambda s, w=window: s / s.shift(w) - 1.0
        )
    return out


def attach_features_and_returns(
    panel: pd.DataFrame,
    candles: pd.DataFrame,
    features: pd.DataFrame,
    timeframe: str,
    horizons: Sequence[int],
) -> pd.DataFrame:
    """Merkmale zum Entscheidungszeitpunkt und Forward Returns danach anhaengen."""
    step = pd.Timedelta(hours=TF_HOURS[timeframe])
    work = panel.copy()
    work["entry_open_time"] = work["ts"] - step

    feature_columns = [
        "adv_30",
        "rv_6",
        "rv_14",
        "rv_30",
        "rv_60",
        "parkinson_30",
        "past_6",
        "past_18",
        "past_30",
        "close",
    ]
    lookup = features[["asset_id", "open_time", *feature_columns]].rename(
        columns={"open_time": "entry_open_time", "close": "entry_close"}
    )
    work = work.merge(lookup, on=["asset_id", "entry_open_time"], how="left")

    closes = candles[["asset_id", "open_time", "close"]]
    for horizon in horizons:
        exits = closes.rename(columns={"open_time": "_exit_open", "close": "_exit_close"})
        work["_exit_open"] = work["entry_open_time"] + pd.Timedelta(hours=horizon)
        work = work.merge(exits, on=["asset_id", "_exit_open"], how="left")
        work[f"ret_{horizon}h"] = work["_exit_close"] / work["entry_close"] - 1.0
        work = work.drop(columns=["_exit_open", "_exit_close"])
        # Querschnittlich zentriert = marktneutrales Alpha. Der unzentrierte Teil
        # ist gemeinsames Marktbeta und sagt nichts ueber Coin-Selektion.
        work[f"xs_{horizon}h"] = work[f"ret_{horizon}h"] - work.groupby("ts")[
            f"ret_{horizon}h"
        ].transform("mean")

    work["day"] = work["ts"].dt.floor("D")
    work["month"] = work["ts"].dt.tz_localize(None).dt.to_period("M").astype(str)

    rank_columns = [*VOL_FEATURES, *REVERSION_FEATURES, "adv_30", "market_cap_rank", "score"]
    for column in rank_columns:
        if column in work.columns:
            work[f"r_{column}"] = work.groupby("ts")[column].rank(pct=True)
    return work


# ---------------------------------------------------------------------------
# Bausteine
# ---------------------------------------------------------------------------


def ic_series(panel: pd.DataFrame, feature: str, target: str) -> pd.DataFrame:
    sub = panel[[feature, target, "ts", "day", "month"]].dropna()
    if sub.empty:
        return pd.DataFrame(columns=["ts", "day", "month", "n", "ic"])
    rows = []
    for (ts, day, month), group in sub.groupby(["ts", "day", "month"], sort=True):
        if len(group) < MIN_ASSETS_PER_SCAN:
            continue
        value = spearman(group[feature].to_numpy(float), group[target].to_numpy(float))
        if np.isfinite(value):
            rows.append({"ts": ts, "day": day, "month": month, "n": len(group), "ic": value})
    return pd.DataFrame(rows)


def summarize_ic(
    panel: pd.DataFrame, feature: str, target: str, horizon_h: int, rng: np.random.Generator
) -> dict[str, Any] | None:
    series = ic_series(panel, feature, target)
    if len(series) < 10:
        return None
    boot = block_bootstrap_mean(series["ic"].to_numpy(), series["day"].to_numpy(), rng=rng)
    monthly = series.groupby("month")["ic"].mean()
    sign = np.sign(monthly.mean()) if len(monthly) else 0.0
    return {
        "feature": feature,
        "target": target,
        "horizon_h": horizon_h,
        "n_scans": len(series),
        "n_days": int(series["day"].nunique()),
        "n_non_overlapping": count_non_overlapping(series["ts"], horizon_h),
        "mean_ic": float(series["ic"].mean()),
        "median_ic": float(series["ic"].median()),
        "share_positive": float((series["ic"] > 0).mean()),
        "ci_low": boot["ci_low"],
        "ci_high": boot["ci_high"],
        "p_block": boot["p"],
        "months": int(len(monthly)),
        "months_same_sign": int((np.sign(monthly.to_numpy()) == sign).sum()),
        "monthly_min": float(monthly.min()) if len(monthly) else float("nan"),
        "monthly_max": float(monthly.max()) if len(monthly) else float("nan"),
        "significant": bool(
            np.isfinite(boot["ci_low"]) and boot["ci_low"] * boot["ci_high"] > 0
        ),
    }


def trimmed_mean(values: np.ndarray, share: float = 0.01) -> float:
    """Mittelwert ohne die extremsten ``share`` an jedem Ende."""
    values = values[np.isfinite(values)]
    if values.size < 20:
        return float("nan")
    low, high = np.percentile(values, [100 * share, 100 * (1 - share)])
    kept = values[(values >= low) & (values <= high)]
    return float(kept.mean()) if kept.size else float("nan")


def skewness(values: np.ndarray) -> float:
    values = values[np.isfinite(values)]
    if values.size < 20:
        return float("nan")
    centered = values - values.mean()
    sd = centered.std(ddof=0)
    return float((centered**3).mean() / sd**3) if sd > 0 else float("nan")


def quantile_table(
    panel: pd.DataFrame,
    feature: str,
    target: str,
    rng: np.random.Generator,
    n_buckets: int = 10,
) -> dict[str, Any] | None:
    """Quantilkurve mit Mittelwert, Median, getrimmtem Mittel, Schiefe.

    Die Eimer werden **je Scan** gebildet. Ein globaler Schnitt wuerde ganze
    Scans in denselben Eimer legen, sobald das Vola-Niveau des Marktes wandert —
    dann messen die Eimer den Zeitpunkt statt den Coin.
    """
    sub = panel[[feature, target, "day", "ts"]].dropna()
    if len(sub) < n_buckets * 200:
        return None
    sub = sub.copy()
    sub["_bucket"] = sub.groupby("ts")[feature].transform(
        lambda s: pd.qcut(s.rank(method="first"), n_buckets, labels=False)
        if s.notna().sum() >= n_buckets * 3
        else np.nan
    )
    sub = sub.dropna(subset=["_bucket"])
    if sub.empty:
        return None
    sub["_bucket"] = sub["_bucket"].astype(int)

    buckets = []
    for bucket, group in sub.groupby("_bucket"):
        values = group[target].to_numpy(float)
        boot = block_bootstrap_mean(values, group["day"].to_numpy(), n_boot=4000, rng=rng)
        top_share = np.nan
        if values.size >= 100:
            threshold = np.percentile(values, 99)
            extreme = values[values >= threshold]
            # Wie viel des Mittelwerts kommt aus dem obersten Prozent? Nahe 1
                # heisst: der Eimer lebt von einer Handvoll Ausbruechen.
            total = values.sum()
            top_share = float(extreme.sum() / total) if total != 0 else float("nan")
        buckets.append(
            {
                "bucket": int(bucket) + 1,
                "feature_median": float(group[feature].median()),
                "n": int(len(group)),
                "mean": float(np.nanmean(values)),
                "median": float(np.nanmedian(values)),
                "trimmed_mean_1pct": trimmed_mean(values, 0.01),
                "share_positive": float((values > 0).mean()),
                "skew": skewness(values),
                "top1pct_share_of_sum": top_share,
                "ci_low": boot["ci_low"],
                "ci_high": boot["ci_high"],
            }
        )

    means = np.array([b["mean"] for b in buckets])
    medians = np.array([b["median"] for b in buckets])

    # Bottom minus Top ist die Rendite der Strategie "long ruhig, short bewegt".
    low = sub[sub["_bucket"] == 0]
    high = sub[sub["_bucket"] == n_buckets - 1]
    paired = (
        low.groupby("day")[target].mean().rename("low").to_frame()
        .join(high.groupby("day")[target].mean().rename("high"), how="inner")
    )
    spread = paired["low"] - paired["high"]
    spread_boot = block_bootstrap_mean(
        spread.to_numpy(float), spread.index.to_numpy(), n_boot=4000, rng=rng
    )

    return {
        "feature": feature,
        "target": target,
        "n": int(len(sub)),
        "n_days": int(sub["day"].nunique()),
        "n_scans": int(sub["ts"].nunique()),
        "buckets": buckets,
        "monotonicity_mean": float(spearman(np.arange(len(means), dtype=float), means)),
        "monotonicity_median": float(spearman(np.arange(len(medians), dtype=float), medians)),
        "monotonicity_mean_ex_top": float(
            spearman(np.arange(len(means) - 1, dtype=float), means[:-1])
        ),
        "bottom_minus_top_mean": {
            "value": float(spread.mean()),
            "ci_low": spread_boot["ci_low"],
            "ci_high": spread_boot["ci_high"],
            "p": spread_boot["p"],
            "n_days": int(len(spread)),
            "significant": bool(
                np.isfinite(spread_boot["ci_low"])
                and spread_boot["ci_low"] * spread_boot["ci_high"] > 0
            ),
        },
        "bottom_minus_top_median": float(medians[0] - medians[-1]),
    }


def fama_macbeth(
    panel: pd.DataFrame,
    target: str,
    regressors: Sequence[str],
    rng: np.random.Generator,
) -> dict[str, Any] | None:
    """Querschnittsregression je Scan, Koeffizienten ueber die Zeit gemittelt.

    Die Regressoren sind zentrierte Querschnittsraenge in ``[-0.5, +0.5]``. Ein
    Koeffizient ist damit direkt lesbar: die Renditedifferenz zwischen dem
    hoechsten und dem niedrigsten Rang, wenn alles andere konstant bleibt.
    """
    columns = [target, "ts", "day", *regressors]
    sub = panel[columns].dropna()
    if sub.empty:
        return None

    rows: list[dict[str, Any]] = []
    for (ts, day), group in sub.groupby(["ts", "day"], sort=True):
        if len(group) < max(MIN_ASSETS_PER_SCAN, 4 * len(regressors)):
            continue
        # ``ranks`` liefert 1..n. Auf [-0.5, +0.5] normiert wird der Koeffizient
        # direkt als Renditedifferenz zwischen hoechstem und niedrigstem Rang
        # lesbar und bleibt ueber Scans mit unterschiedlich vielen Coins
        # vergleichbar.
        size = len(group)
        design = np.column_stack(
            [np.ones(size)]
            + [
                (ranks(group[name].to_numpy(float)) - 1.0) / (size - 1.0) - 0.5
                for name in regressors
            ]
        )
        y = group[target].to_numpy(float)
        try:
            beta, *_ = np.linalg.lstsq(design, y, rcond=None)
        except np.linalg.LinAlgError:
            continue
        row: dict[str, Any] = {"ts": ts, "day": day}
        for index, name in enumerate(regressors, start=1):
            row[name] = float(beta[index])
        rows.append(row)

    if len(rows) < 20:
        return None
    frame = pd.DataFrame(rows)
    out: dict[str, Any] = {"n_scans": len(frame), "n_days": int(frame["day"].nunique()), "coef": {}}
    for name in regressors:
        boot = block_bootstrap_mean(
            frame[name].to_numpy(float), frame["day"].to_numpy(), n_boot=4000, rng=rng
        )
        out["coef"][name] = {
            "mean": float(frame[name].mean()),
            "ci_low": boot["ci_low"],
            "ci_high": boot["ci_high"],
            "p": boot["p"],
            "share_positive": float((frame[name] > 0).mean()),
            "significant": bool(
                np.isfinite(boot["ci_low"]) and boot["ci_low"] * boot["ci_high"] > 0
            ),
        }
    return out


def double_sort(
    panel: pd.DataFrame, target: str, control: str, vol: str = "atr_percent", n: int = 3
) -> dict[str, Any] | None:
    """Vola-Terzile innerhalb von Kontroll-Terzilen. Je Scan sortiert."""
    sub = panel[[target, control, vol, "ts"]].dropna()
    if len(sub) < 5000:
        return None
    sub = sub.copy()

    def bucketize(series: pd.Series) -> pd.Series:
        if series.notna().sum() < n * 3:
            return pd.Series(np.nan, index=series.index)
        return pd.qcut(series.rank(method="first"), n, labels=False)

    sub["_c"] = sub.groupby("ts")[control].transform(bucketize)
    sub["_v"] = sub.groupby("ts")[vol].transform(bucketize)
    sub = sub.dropna(subset=["_c", "_v"])
    if sub.empty:
        return None

    cells = []
    for (control_bucket, vol_bucket), group in sub.groupby(["_c", "_v"]):
        cells.append(
            {
                "control_bucket": int(control_bucket) + 1,
                "vol_bucket": int(vol_bucket) + 1,
                "n": int(len(group)),
                "mean": float(group[target].mean()),
                "median": float(group[target].median()),
            }
        )
    spreads = []
    for control_bucket, group in sub.groupby("_c"):
        low = group.loc[group["_v"] == 0, target]
        high = group.loc[group["_v"] == n - 1, target]
        if len(low) > 100 and len(high) > 100:
            spreads.append(
                {
                    "control_bucket": int(control_bucket) + 1,
                    "low_minus_high_mean": float(low.mean() - high.mean()),
                    "low_minus_high_median": float(low.median() - high.median()),
                }
            )
    return {"control": control, "vol": vol, "cells": cells, "spreads": spreads}


def liquidity_robustness(
    panel: pd.DataFrame, target: str, horizon: int, rng: np.random.Generator
) -> list[dict[str, Any]]:
    """IC und Mittelwert-Spread nach Ausschluss der illiquidesten Coins."""
    out = []
    for cut in (0.0, 0.2, 0.4, 0.6):
        sub = panel[panel["r_adv_30"] >= cut] if cut > 0 else panel
        summary = summarize_ic(sub, "atr_percent", target, horizon, rng)
        table = quantile_table(sub, "atr_percent", target, rng, n_buckets=5)
        out.append(
            {
                "adv_rank_cut": cut,
                "n_rows": int(sub[target].notna().sum()),
                "ic": summary["mean_ic"] if summary else float("nan"),
                "ic_ci_low": summary["ci_low"] if summary else float("nan"),
                "ic_ci_high": summary["ci_high"] if summary else float("nan"),
                "bottom_minus_top_mean": (
                    table["bottom_minus_top_mean"]["value"] if table else float("nan")
                ),
                "bottom_minus_top_ci_low": (
                    table["bottom_minus_top_mean"]["ci_low"] if table else float("nan")
                ),
                "bottom_minus_top_ci_high": (
                    table["bottom_minus_top_mean"]["ci_high"] if table else float("nan")
                ),
                "bottom_minus_top_median": (
                    table["bottom_minus_top_median"] if table else float("nan")
                ),
            }
        )
    return out


def combination_test(
    panel: pd.DataFrame, target: str, horizon: int, rng: np.random.Generator, top_n: int = 20
) -> list[dict[str, Any]]:
    """Vola-Faktor allein, Mean Reversion allein, und beides kombiniert.

    Das Signal ist immer so orientiert, dass **hoch = long-attraktiv** gilt:
    niedrige Vola, niedriger RSI, schwache vergangene Rendite. Neben IC und
    Quintilspread wird die Auswahl berichtet, die eine Strategie wirklich
    handelt: die besten ``top_n`` Coins je Scan.
    """
    needed = ["r_atr_percent", "r_rsi_14", "r_past_6", "r_past_18", target, "ts", "day", "month"]
    sub = panel[needed].dropna().copy()
    if sub.empty:
        return []

    definitions = {
        "nur -Vola": -sub["r_atr_percent"],
        "nur -RSI (ueberverkauft)": -sub["r_rsi_14"],
        "nur -Rendite 24h (Reversal)": -sub["r_past_6"],
        "nur -Rendite 72h (Reversal)": -sub["r_past_18"],
        "-Vola & -RSI": -(sub["r_atr_percent"] + sub["r_rsi_14"]) / 2,
        "-Vola & -Reversal 24h": -(sub["r_atr_percent"] + sub["r_past_6"]) / 2,
        "-Vola & -RSI & -Reversal": -(
            sub["r_atr_percent"] + sub["r_rsi_14"] + sub["r_past_6"]
        ) / 3,
        "+Vola & -RSI (Gegenprobe)": (sub["r_atr_percent"] - sub["r_rsi_14"]) / 2,
    }

    results = []
    for name, signal in definitions.items():
        sub["_signal"] = signal
        summary = summarize_ic(sub, "_signal", target, horizon, rng)
        table = quantile_table(sub, "_signal", target, rng, n_buckets=5)

        # Was die Strategie faktisch haelt: die top_n je Scan, long-only.
        picked = sub.sort_values("_signal", ascending=False).groupby("ts").head(top_n)
        per_scan = picked.groupby(["ts", "day"])[target].mean().reset_index()
        boot = block_bootstrap_mean(
            per_scan[target].to_numpy(float), per_scan["day"].to_numpy(), n_boot=4000, rng=rng
        )
        results.append(
            {
                "name": name,
                "ic": summary["mean_ic"] if summary else float("nan"),
                "ic_ci_low": summary["ci_low"] if summary else float("nan"),
                "ic_ci_high": summary["ci_high"] if summary else float("nan"),
                "ic_months_same_sign": summary["months_same_sign"] if summary else 0,
                "ic_months": summary["months"] if summary else 0,
                "q5_minus_q1_mean": (
                    table["buckets"][-1]["mean"] - table["buckets"][0]["mean"] if table else np.nan
                ),
                "q5_minus_q1_median": (
                    table["buckets"][-1]["median"] - table["buckets"][0]["median"]
                    if table
                    else np.nan
                ),
                "top_n": top_n,
                "top_n_mean": float(per_scan[target].mean()),
                "top_n_median": float(per_scan[target].median()),
                "top_n_ci_low": boot["ci_low"],
                "top_n_ci_high": boot["ci_high"],
                "top_n_p": boot["p"],
                "top_n_significant": bool(
                    np.isfinite(boot["ci_low"]) and boot["ci_low"] * boot["ci_high"] > 0
                ),
                "n_scans": int(len(per_scan)),
            }
        )
    return results


def cost_hurdle(panel: pd.DataFrame, horizons: Sequence[int]) -> dict[str, Any]:
    """Kostenrechnung in R — der eigentliche Knackpunkt einer Low-Vol-Strategie.

    1R ist der Stopabstand ``1.5 * ATR``. Damit gilt
    ``Gebuehr in R = Rundlaufgebuehr% / Stopabstand%`` und
    ``Alpha in R = Alpha% / Stopabstand%``. Beide skalieren mit der Vola —
    entscheidend ist deshalb nicht die Vola, sondern ob die Prozentbewegung die
    Prozentgebuehr schlaegt. Genau das wird hier je Vola-Quintil ausgewiesen.
    """
    sub = panel.dropna(subset=["atr_percent"]).copy()
    sub["_q"] = sub.groupby("ts")["atr_percent"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 5, labels=False)
        if s.notna().sum() >= 15
        else np.nan
    )
    sub = sub.dropna(subset=["_q"])

    rows = []
    for quintile, group in sub.groupby("_q"):
        stop_pct = float((ATR_STOP_MULTIPLIER * group["atr_percent"]).median())
        entry = {
            "quintile": int(quintile) + 1,
            "atr_percent_median": float(group["atr_percent"].median()),
            "stop_distance_pct": stop_pct,
            "fee_in_r": FEE_ROUND_TRIP_PCT / stop_pct if stop_pct > 0 else float("nan"),
        }
        for horizon in horizons:
            column = f"xs_{horizon}h"
            if column not in group:
                continue
            alpha = float(group[column].mean()) * 100.0
            entry[f"alpha_pct_{horizon}h"] = alpha
            entry[f"alpha_r_{horizon}h"] = alpha / stop_pct if stop_pct > 0 else float("nan")
            entry[f"net_r_{horizon}h"] = (
                (alpha - FEE_ROUND_TRIP_PCT) / stop_pct if stop_pct > 0 else float("nan")
            )
        rows.append(entry)

    # Break-even-Haltedauer: ab wann uebersteigt das Alpha des Spreads die Kosten?
    spread_by_horizon = {}
    for horizon in horizons:
        column = f"xs_{horizon}h"
        if column not in sub:
            continue
        low = sub.loc[sub["_q"] == 0, column].mean() * 100.0
        high = sub.loc[sub["_q"] == 4, column].mean() * 100.0
        spread_by_horizon[f"{horizon}h"] = {
            "long_only_low_vol_pct": float(low),
            "short_leg_high_vol_pct": float(-high),
            "long_short_spread_pct": float(low - high),
            "fee_long_only_pct": FEE_ROUND_TRIP_PCT,
            "fee_long_short_pct": 2 * FEE_ROUND_TRIP_PCT,
            "net_long_only_pct": float(low - FEE_ROUND_TRIP_PCT),
            "net_long_short_pct": float(low - high - 2 * FEE_ROUND_TRIP_PCT),
        }
    return {
        "fee_per_side_pct": FEE_PER_SIDE_PCT,
        "fee_round_trip_pct": FEE_ROUND_TRIP_PCT,
        "atr_stop_multiplier": ATR_STOP_MULTIPLIER,
        "by_vol_quintile": rows,
        "by_horizon": spread_by_horizon,
    }


def window_split(
    panel: pd.DataFrame, target: str, horizon: int, rng: np.random.Generator
) -> list[dict[str, Any]]:
    """Zwei disjunkte Haelften — haelt der Befund out-of-sample sein Vorzeichen?"""
    times = panel["ts"].dropna()
    if times.empty:
        return []
    midpoint = times.min() + (times.max() - times.min()) / 2
    out = []
    for label, mask in (
        ("Haelfte 1", panel["ts"] < midpoint),
        ("Haelfte 2", panel["ts"] >= midpoint),
    ):
        sub = panel[mask]
        summary = summarize_ic(sub, "atr_percent", target, horizon, rng)
        table = quantile_table(sub, "atr_percent", target, rng, n_buckets=5)
        out.append(
            {
                "window": label,
                "start": str(sub["ts"].min()),
                "end": str(sub["ts"].max()),
                "n_days": int(sub["day"].nunique()),
                "ic": summary["mean_ic"] if summary else float("nan"),
                "ic_ci_low": summary["ci_low"] if summary else float("nan"),
                "ic_ci_high": summary["ci_high"] if summary else float("nan"),
                "bottom_minus_top_mean": (
                    table["bottom_minus_top_mean"]["value"] if table else float("nan")
                ),
                "bottom_minus_top_ci_low": (
                    table["bottom_minus_top_mean"]["ci_low"] if table else float("nan")
                ),
                "bottom_minus_top_ci_high": (
                    table["bottom_minus_top_mean"]["ci_high"] if table else float("nan")
                ),
                "bottom_minus_top_median": (
                    table["bottom_minus_top_median"] if table else float("nan")
                ),
                "top_quintile_mean": table["buckets"][-1]["mean"] if table else float("nan"),
                "bottom_quintile_mean": table["buckets"][0]["mean"] if table else float("nan"),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Bar-Replay-Backtest — risikonormiert in R, Gebuehren exakt
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandleSeries:
    t: np.ndarray
    h: np.ndarray
    low: np.ndarray
    c: np.ndarray


@dataclass
class ReplayTrade:
    ts: pd.Timestamp
    day: pd.Timestamp
    asset_id: int
    strategy: str
    entry: float
    stop_pct: float
    gross_r: float
    fees_r: float
    net_r: float
    exit_reason: str
    hold_hours: float


def load_replay_series(data_dir: Path, timeframe: str) -> dict[int, CandleSeries]:
    per_tf, _ = load_candle_arrays(data_dir, [timeframe])
    out: dict[int, CandleSeries] = {}
    for asset_id, arrays in per_tf[timeframe].items():
        out[asset_id] = CandleSeries(
            t=arrays["t"],
            h=arrays["h"],
            low=arrays["l"],
            c=arrays["c"],
        )
    return out


def clip_stop_pct(atr_percent: float) -> float:
    raw = ATR_STOP_MULTIPLIER * atr_percent / 100.0
    return float(np.clip(raw, MIN_STOP_PERCENT / 100.0, MAX_STOP_PERCENT / 100.0))


def replay_long(
    series: CandleSeries,
    *,
    entry_ns: int,
    entry_price: float,
    stop_pct: float,
    hold_hours: float,
) -> tuple[float, float, str, float] | None:
    """Long-Replay: Stop zuerst, dann Zeit-Exit auf Schlusskurs."""
    if entry_price <= 0 or stop_pct <= 0:
        return None
    start_idx = int(np.searchsorted(series.t, entry_ns, side="right"))
    if start_idx >= len(series.t):
        return None

    stop_price = entry_price * (1.0 - stop_pct)
    expiry_ns = entry_ns + int(hold_hours * NS_PER_HOUR)
    fee_k = (FEE_PER_SIDE_PCT / 100.0) / (entry_price * stop_pct)
    fees_r = entry_price * fee_k

    for i in range(start_idx, len(series.t)):
        bar_ns = int(series.t[i])
        low = float(series.low[i])
        close = float(series.c[i])
        if low <= stop_price:
            gross_r = (stop_price - entry_price) / (entry_price * stop_pct)
            hold_h = (bar_ns - entry_ns) / NS_PER_HOUR
            return gross_r, fees_r, "stop_loss", hold_h
        if bar_ns >= expiry_ns:
            gross_r = (close - entry_price) / (entry_price * stop_pct)
            hold_h = (bar_ns - entry_ns) / NS_PER_HOUR
            return gross_r, fees_r, "expired", hold_h

    close = float(series.c[-1])
    gross_r = (close - entry_price) / (entry_price * stop_pct)
    hold_h = (int(series.t[-1]) - entry_ns) / NS_PER_HOUR
    return gross_r, fees_r, "data_end", hold_h


def strategy_picks(group: pd.DataFrame, key: str, top_n: int, rng: np.random.Generator) -> pd.DataFrame:
    """Je Scan die Top-N je Strategie — long-only, hoch = attraktiv."""
    if key == "low_vol":
        return group.nsmallest(top_n, "atr_percent")
    if key == "high_vol":
        return group.nlargest(top_n, "atr_percent")
    if key == "score":
        return group.nlargest(top_n, "score")
    if key == "reversal_rsi":
        return group.nsmallest(top_n, "rsi_14")
    if key == "reversal_72h":
        return group.nsmallest(top_n, "past_18")
    if key == "low_vol_reversal":
        group = group.copy()
        group["_combo"] = (
            group["r_atr_percent"] + group["r_rsi_14"] + group["r_past_6"]
        ) / 3.0
        return group.nsmallest(top_n, "_combo")
    if key == "random":
        if len(group) <= top_n:
            return group
        idx = rng.choice(group.index.to_numpy(), size=top_n, replace=False)
        return group.loc[idx]
    raise KeyError(key)


STRATEGY_LABELS = {
    "low_vol": "Low-Vol Top-N (atr_percent)",
    "high_vol": "High-Vol Top-N (Gegenprobe)",
    "random": "Zufall Top-N",
    "score": "Score Top-N (Composite)",
    "reversal_rsi": "Mean Reversion RSI",
    "reversal_72h": "Mean Reversion 72h",
    "low_vol_reversal": "Low-Vol + Reversal kombiniert",
}


def run_backtest(
    panel: pd.DataFrame,
    series_by_asset: dict[int, CandleSeries],
    *,
    hold_hours: float,
    top_n: int,
    strategies: Sequence[str],
    rng: np.random.Generator,
    one_per_symbol: bool = True,
    min_adv_rank: float = 0.0,
) -> list[ReplayTrade]:
    """Replay je Scan/Strategie. Ein Symbol gleichzeitig wie live."""
    needed = [
        "ts",
        "day",
        "asset_id",
        "reference_price",
        "atr_percent",
        "data_quality",
        "score",
        "rsi_14",
        "past_18",
        "r_atr_percent",
        "r_rsi_14",
        "r_past_6",
    ]
    work = panel[needed].dropna(subset=["reference_price", "atr_percent"]).copy()
    if min_adv_rank > 0 and "r_adv_30" in panel.columns:
        work = work.join(panel["r_adv_30"], on=work.index)
        work = work[work["r_adv_30"] >= min_adv_rank]

    work = work[
        (work["data_quality"] >= MIN_DATA_QUALITY)
        & (work["atr_percent"] > 0.05)
        & (work["reference_price"] > 0)
    ]
    trades: list[ReplayTrade] = []
    open_until: dict[tuple[str, int], int] = {}

    for ts, group in work.groupby("ts", sort=True):
        if len(group) < top_n:
            continue
        entry_ns = int(pd.Timestamp(ts).value)
        day = group["day"].iloc[0]
        for strategy in strategies:
            picks = strategy_picks(group, strategy, top_n, rng)
            for row in picks.itertuples(index=False):
                asset_id = int(row.asset_id)
                key = (strategy, asset_id)
                if one_per_symbol and open_until.get(key, 0) > entry_ns:
                    continue
                candle = series_by_asset.get(asset_id)
                if candle is None:
                    continue
                stop_pct = clip_stop_pct(float(row.atr_percent))
                replay = replay_long(
                    candle,
                    entry_ns=entry_ns,
                    entry_price=float(row.reference_price),
                    stop_pct=stop_pct,
                    hold_hours=hold_hours,
                )
                if replay is None:
                    continue
                gross_r, fees_r, reason, hold_h = replay
                net_r = gross_r - fees_r
                trades.append(
                    ReplayTrade(
                        ts=ts,
                        day=day,
                        asset_id=asset_id,
                        strategy=strategy,
                        entry=float(row.reference_price),
                        stop_pct=stop_pct,
                        gross_r=gross_r,
                        fees_r=fees_r,
                        net_r=net_r,
                        exit_reason=reason,
                        hold_hours=hold_h,
                    )
                )
                if one_per_symbol:
                    open_until[key] = entry_ns + int(hold_hours * NS_PER_HOUR)
    return trades


def summarise_replay(
    trades: list[ReplayTrade], *, rng: np.random.Generator, label: str = ""
) -> dict[str, Any]:
    if not trades:
        return {"label": label, "n": 0}
    net = np.array([t.net_r for t in trades], dtype=float)
    gross = np.array([t.gross_r for t in trades], dtype=float)
    fees = np.array([t.fees_r for t in trades], dtype=float)
    days = np.array([pd.Timestamp(t.day).value for t in trades])
    boot = block_bootstrap_mean(net, days, n_boot=4000, rng=rng)
    wins = net[net > 0]
    losses = net[net < 0]
    pf = float(wins.sum() / abs(losses.sum())) if losses.size and wins.size else float("nan")
    by_exit: dict[str, int] = {}
    for trade in trades:
        by_exit[trade.exit_reason] = by_exit.get(trade.exit_reason, 0) + 1
    ordered = sorted(trades, key=lambda t: t.ts)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for trade in ordered:
        equity += trade.net_r
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return {
        "label": label,
        "n": int(len(trades)),
        "n_days": int(len({t.day for t in trades})),
        "mean_net_r": float(net.mean()),
        "median_net_r": float(np.median(net)),
        "mean_gross_r": float(gross.mean()),
        "mean_fees_r": float(fees.mean()),
        "ci_low": boot["ci_low"],
        "ci_high": boot["ci_high"],
        "p": boot["p"],
        "significant": bool(np.isfinite(boot["ci_low"]) and boot["ci_low"] * boot["ci_high"] > 0),
        "win_rate": float((net > 0).mean()),
        "profit_factor": pf,
        "max_drawdown_r": float(max_dd),
        "avg_hold_hours": float(np.mean([t.hold_hours for t in trades])),
        "exit_mix": by_exit,
    }


def backtest_by_strategy(
    trades: list[ReplayTrade], *, rng: np.random.Generator
) -> list[dict[str, Any]]:
    out = []
    for key, label in STRATEGY_LABELS.items():
        subset = [t for t in trades if t.strategy == key]
        if subset:
            out.append(summarise_replay(subset, rng=rng, label=label))
    return out


def break_even_horizons(
    panel: pd.DataFrame,
    series_by_asset: dict[int, CandleSeries],
    *,
    strategy: str,
    horizons: Sequence[int],
    top_n: int,
    rng: np.random.Generator,
) -> list[dict[str, Any]]:
    """Ab welcher Haltedauer ueberlebt die Strategie 0,1% Rundlauf in R?"""
    rows = []
    for horizon in horizons:
        trades = run_backtest(
            panel,
            series_by_asset,
            hold_hours=float(horizon),
            top_n=top_n,
            strategies=[strategy],
            rng=rng,
        )
        summary = summarise_replay(
            trades, rng=rng, label=f"{strategy}@{horizon}h"
        )
        stop_pct = float(np.median([t.stop_pct for t in trades])) if trades else float("nan")
        fee_r = FEE_ROUND_TRIP_PCT / (stop_pct * 100.0) if stop_pct > 0 else float("nan")
        rows.append(
            {
                "horizon_h": horizon,
                "strategy": strategy,
                "n_trades": summary.get("n", 0),
                "mean_net_r": summary.get("mean_net_r", float("nan")),
                "ci_low": summary.get("ci_low", float("nan")),
                "ci_high": summary.get("ci_high", float("nan")),
                "significant": summary.get("significant", False),
                "median_stop_pct": stop_pct * 100.0,
                "fee_in_r": fee_r,
                "clears_fees": bool(
                    summary.get("significant") and summary.get("mean_net_r", -1.0) > 0
                ),
            }
        )
    return rows


def backtest_walk_forward(
    panel: pd.DataFrame,
    series_by_asset: dict[int, CandleSeries],
    *,
    hold_hours: float,
    top_n: int,
    strategy: str,
    rng: np.random.Generator,
) -> list[dict[str, Any]]:
    times = panel["ts"].dropna()
    if times.empty:
        return []
    midpoint = times.min() + (times.max() - times.min()) / 2
    out = []
    for label, mask in (
        ("Haelfte 1", panel["ts"] < midpoint),
        ("Haelfte 2", panel["ts"] >= midpoint),
    ):
        sub = panel[mask]
        trades = run_backtest(
            sub,
            series_by_asset,
            hold_hours=hold_hours,
            top_n=top_n,
            strategies=[strategy],
            rng=rng,
        )
        summary = summarise_replay(trades, rng=rng, label=label)
        summary["window"] = label
        summary["start"] = str(sub["ts"].min())
        summary["end"] = str(sub["ts"].max())
        out.append(summary)
    return out


def build_verdict(payload: dict[str, Any]) -> dict[str, Any]:
    """Synthese: handelbar nach Gebuehren oder nicht?"""
    focus = payload["coverage"].get("focus_horizon_h", 24)
    shape = payload.get("shape", {}).get(f"{focus}h", {})
    spread = shape.get("bottom_minus_top_mean", {})
    low_vol_combo = None
    rev_only = None
    for row in payload.get("reversion_combinations", {}).get(f"{focus}h", []):
        if row["name"] == "nur -Vola":
            low_vol_combo = row
        if row["name"] == "nur -Rendite 72h (Reversal)":
            rev_only = row

    backtests = {
        row.get("label", ""): row
        for row in payload.get("backtest", {}).get("by_strategy", [])
    }
    low_vol_bt = next(
        (row for row in backtests.values() if "Low-Vol Top-N" in row.get("label", "")),
        {},
    )
    break_even = payload.get("backtest", {}).get("break_even_low_vol", [])
    first_clear = next((row for row in break_even if row.get("clears_fees")), None)

    tradeable = bool(
        low_vol_bt.get("significant")
        and low_vol_bt.get("mean_net_r", -999.0) > 0
    )
    if tradeable:
        verdict = "handelbar nach Gebuehren"
        config = {
            "signal": "long-only, niedrigstes atr_percent je Scan",
            "top_n": payload.get("backtest", {}).get("top_n"),
            "hold_hours": payload.get("backtest", {}).get("hold_hours"),
            "stop": f"{ATR_STOP_MULTIPLIER}x ATR ({MIN_STOP_PERCENT}-{MAX_STOP_PERCENT}% Klammer)",
            "fee_assumption_pct": FEE_ROUND_TRIP_PCT,
            "expected_net_r": low_vol_bt.get("mean_net_r"),
            "ci_95": [low_vol_bt.get("ci_low"), low_vol_bt.get("ci_high")],
        }
    else:
        verdict = "does not clear cost hurdle"
        config = {
            "reason": (
                "Negativer IC/Median-Vorteil fuer Low-Vol uebersetzt sich nicht in "
                "positive Erwartungswerte; Mittelwert und Bar-Replay netto-R "
                "bleiben nach 0,05% je Seite negativ bzw. insignifikant."
            ),
            "ic_mean": next(
                (
                    row["mean_ic"]
                    for row in payload.get("vol_definitions", [])
                    if row.get("feature") == "atr_percent"
                    and row.get("horizon_h") == focus
                ),
                float("nan"),
            ),
            "spread_mean_pct": spread.get("value"),
            "spread_median_pct": shape.get("bottom_minus_top_median"),
            "top_n_mean_pct": (low_vol_combo or {}).get("top_n_mean"),
            "backtest_mean_net_r": low_vol_bt.get("mean_net_r"),
            "backtest_ci": [low_vol_bt.get("ci_low"), low_vol_bt.get("ci_high")],
            "mean_reversion_alone_top_n_pct": (rev_only or {}).get("top_n_mean"),
            "first_break_even_horizon_h": (
                first_clear.get("horizon_h") if first_clear else None
            ),
        }

    return {
        "verdict": verdict,
        "tradeable": tradeable,
        "recommended_config": config,
        "note": (
            "Mean Reversion (RSI / vergangene Rendite) ist ein separater, schwach "
            "positiver Faktor; Kombination mit Low-Vol ist nicht additiv."
            if not tradeable
            else ""
        ),
    }


# ---------------------------------------------------------------------------
# Orchestrierung
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> dict[str, Any]:
    rng = np.random.default_rng(RNG_SEED)
    horizons = [int(h) for h in args.horizons.split(",")]
    focus = args.focus_horizon
    target = f"xs_{focus}h"

    panel, meta = load_panel(args.data_dir, args.tag)
    candles = load_candles(args.data_dir, args.return_timeframe)
    features = build_candle_features(candles)
    panel = attach_features_and_returns(panel, candles, features, args.return_timeframe, horizons)

    if args.start:
        panel = panel[panel["ts"] >= pd.Timestamp(args.start, tz="UTC")]
    if args.end:
        panel = panel[panel["ts"] <= pd.Timestamp(args.end, tz="UTC")]
    # Absurde ATR-Werte stammen aus Kerzenluecken, nicht aus Marktbewegung.
    panel = panel[(panel["atr_percent"] > 0.05) & (panel["atr_percent"] < args.max_atr_percent)]

    coverage = {
        "tag": args.tag,
        "return_timeframe": args.return_timeframe,
        "regeneration_meta": {
            key: meta.get(key) for key in ("preset", "step_hours", "primary_timeframe", "weights")
        },
        "rows": int(len(panel)),
        "assets": int(panel["asset_id"].nunique()),
        "scans": int(panel["ts"].nunique()),
        "days": int(panel["day"].nunique()),
        "months": int(panel["month"].nunique()),
        "window_start": str(panel["ts"].min()),
        "window_end": str(panel["ts"].max()),
        "assets_per_scan_median": float(panel.groupby("ts").size().median()),
        "usable_rows": {f"{h}h": int(panel[f"ret_{h}h"].notna().sum()) for h in horizons},
        "non_overlapping_scans": {
            f"{h}h": count_non_overlapping(panel.loc[panel[f"ret_{h}h"].notna(), "ts"], h)
            for h in horizons
        },
        "atr_percent_quantiles": {
            str(q): float(panel["atr_percent"].quantile(q))
            for q in (0.05, 0.25, 0.5, 0.75, 0.95)
        },
        "focus_horizon_h": focus,
    }

    # 1 — Definition und Horizont
    definitions: list[dict[str, Any]] = []
    for feature in VOL_FEATURES:
        if feature not in panel.columns:
            continue
        for horizon in horizons:
            summary = summarize_ic(panel, feature, f"xs_{horizon}h", horizon, rng)
            if summary:
                definitions.append(summary)

    correlations = {}
    available = [f for f in VOL_FEATURES if f in panel.columns]
    corr_frame = panel[available].dropna()
    if len(corr_frame) > 1000:
        for i, left in enumerate(available):
            for right in available[i + 1 :]:
                correlations[f"{left}~{right}"] = float(
                    spearman(corr_frame[left].to_numpy(float), corr_frame[right].to_numpy(float))
                )

    # 2 — Form der Beziehung
    shapes = {}
    for horizon in horizons:
        table = quantile_table(panel, "atr_percent", f"xs_{horizon}h", rng, n_buckets=10)
        if table:
            shapes[f"{horizon}h"] = table

    # 3 — Liquiditaet
    liquidity = liquidity_robustness(panel, target, focus, rng)

    # 4 — Eigenstaendigkeit
    independence = {
        "univariate": fama_macbeth(panel, target, ["atr_percent"], rng),
        "vol_plus_size": fama_macbeth(panel, target, ["atr_percent", "market_cap_rank"], rng),
        "vol_plus_liquidity": fama_macbeth(panel, target, ["atr_percent", "adv_30"], rng),
        "vol_size_liquidity": fama_macbeth(
            panel, target, ["atr_percent", "market_cap_rank", "adv_30"], rng
        ),
        "full": fama_macbeth(
            panel,
            target,
            ["atr_percent", "market_cap_rank", "adv_30", "rsi_14", "past_6", "score"],
            rng,
        ),
        "double_sort_market_cap": double_sort(panel, target, "market_cap_rank"),
        "double_sort_liquidity": double_sort(panel, target, "adv_30"),
        "note_market_cap": (
            "market_cap_rank ist ein Momentaufnahme-Rang aus der Assets-Tabelle, "
            "keine historische Zeitreihe. Als Kontrollvariable brauchbar, als "
            "Handelssignal nicht."
        ),
    }

    # 5 — Mean Reversion
    reversion = {
        f"{horizon}h": combination_test(panel, f"xs_{horizon}h", horizon, rng, args.top_n)
        for horizon in horizons
    }
    reversion_solo = [
        summary
        for feature in REVERSION_FEATURES
        if feature in panel.columns
        for summary in [summarize_ic(panel, feature, target, focus, rng)]
        if summary
    ]

    # 6 — Kosten und Fenster
    costs = cost_hurdle(panel, horizons)
    windows = window_split(panel, target, focus, rng)

    backtest_payload: dict[str, Any] = {"skipped": True}
    if not args.skip_backtest:
        series_by_asset = load_replay_series(args.data_dir, args.return_timeframe)
        strategy_keys = [
            "low_vol",
            "high_vol",
            "random",
            "score",
            "reversal_rsi",
            "reversal_72h",
            "low_vol_reversal",
        ]
        all_trades = run_backtest(
            panel,
            series_by_asset,
            hold_hours=float(args.backtest_hold_hours),
            top_n=args.top_n,
            strategies=strategy_keys,
            rng=rng,
        )
        break_even_h = break_even_horizons(
            panel,
            series_by_asset,
            strategy="low_vol",
            horizons=horizons,
            top_n=args.top_n,
            rng=rng,
        )
        backtest_payload = {
            "skipped": False,
            "hold_hours": args.backtest_hold_hours,
            "top_n": args.top_n,
            "one_per_symbol": True,
            "fee_per_side_pct": FEE_PER_SIDE_PCT,
            "fee_round_trip_pct": FEE_ROUND_TRIP_PCT,
            "stop": f"{ATR_STOP_MULTIPLIER}x ATR",
            "by_strategy": backtest_by_strategy(all_trades, rng=rng),
            "break_even_low_vol": break_even_h,
            "walk_forward_low_vol": backtest_walk_forward(
                panel,
                series_by_asset,
                hold_hours=float(args.backtest_hold_hours),
                top_n=args.top_n,
                strategy="low_vol",
                rng=rng,
            ),
            "n_trades_total": len(all_trades),
        }

    monthly_focus = ic_series(panel, "atr_percent", target)
    monthly_rows = [
        {
            "month": str(month),
            "n_scans": int(len(group)),
            "mean_ic": float(group["ic"].mean()),
            "share_positive": float((group["ic"] > 0).mean()),
        }
        for month, group in monthly_focus.groupby("month")
    ]

    payload = {
        "meta": {
            "generated_for": "DATAMIND",
            "question": (
                "Traegt der Low-Vol-Effekt (atr_percent, IC -0.07) nach realistischen "
                "Kosten eine handelbare Strategie?"
            ),
            "script": "scripts/analyze_lowvol_factor.py",
            "method": {
                "features": "aus Kerzen mit open_time <= ts - Timeframe (keine Zukunft)",
                "targets": "xs = je Scan querschnittlich zentriert (marktneutral)",
                "buckets": "je Scan gebildet, damit Eimer Coins und nicht Zeitpunkte messen",
                "inference": "Block-Bootstrap ueber Tage",
                "fees": f"{FEE_PER_SIDE_PCT}% je Seite, {FEE_ROUND_TRIP_PCT}% Rundlauf",
                "r_definition": f"1R = {ATR_STOP_MULTIPLIER} * ATR (Live-Engine)",
            },
            "reused_from": [
                "scripts/analyze_deep_edge.py: block_bootstrap_mean, count_non_overlapping",
                "scripts/analyze_score_edge.py: ranks, spearman",
            ],
        },
        "coverage": coverage,
        "vol_definitions": definitions,
        "vol_definition_correlations": correlations,
        "shape": shapes,
        "liquidity_robustness": liquidity,
        "independence": independence,
        "reversion_combinations": reversion,
        "reversion_solo": reversion_solo,
        "cost_hurdle": costs,
        "windows": windows,
        "monthly_ic_focus": monthly_rows,
        "backtest": backtest_payload,
    }
    payload["verdict"] = build_verdict(payload)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "lowvol_factor.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    (args.out_dir / "lowvol_factor.txt").write_text(render_text(payload), encoding="utf-8")
    return payload


def _pct(value: float | None) -> str:
    if value is None or not np.isfinite(value):
        return "    n/a"
    return f"{value * 100:+7.4f}%"


def render_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    add = lines.append
    cov = payload["coverage"]

    add("=" * 108)
    add("LOW-VOL-FAKTOR  --  traegt realisierte Volatilitaet eine handelbare Strategie?")
    add("=" * 108)
    add(f"Panel        : {cov['tag']}  ({cov['return_timeframe']}-Kerzen)")
    add(f"Fenster      : {cov['window_start']}  bis  {cov['window_end']}")
    add(
        f"Umfang       : {cov['rows']:,} Zeilen | {cov['assets']} Assets | {cov['scans']} Scans | "
        f"{cov['days']} Tage | {cov['months']} Monate | Median {cov['assets_per_scan_median']:.0f} "
        f"Coins/Scan"
    )
    add(f"Unabh. Scans : {cov['non_overlapping_scans']}   <-- die ehrliche Stichprobengroesse")
    add(f"ATR-Quantile : {({k: round(v, 2) for k, v in cov['atr_percent_quantiles'].items()})}")
    add("")

    add("-" * 108)
    add("1  DEFINITION UND HORIZONT   (IC = Mittel der Querschnitts-Rangkorrelationen je Scan)")
    add("-" * 108)
    add(
        f"{'Definition':<16}{'Hor':>5}{'Scans':>7}{'unabh':>7}{'IC':>9}{'CI (Bootstrap)':>22}"
        f"{'p':>8}{'Monate gl. Vz.':>16}"
    )
    for row in payload["vol_definitions"]:
        add(
            f"{row['feature']:<16}{row['horizon_h']:>4}h{row['n_scans']:>7}"
            f"{row['n_non_overlapping']:>7}{row['mean_ic']:>9.4f}"
            f"   [{row['ci_low']:+.4f}, {row['ci_high']:+.4f}]{row['p_block']:>8.3f}"
            f"{row['months_same_sign']:>9}/{row['months']:<6}"
        )
    add("")
    add("  Rangkorrelation der Definitionen untereinander:")
    for key, value in payload["vol_definition_correlations"].items():
        add(f"    {key:<28} {value:+.3f}")
    add("")

    add("-" * 108)
    add("2  FORM DER BEZIEHUNG   (Dezile je Scan; MITTEL ist was gehandelt wird, MEDIAN was der")
    add("   Rang-IC misst. Gehen sie auseinander, ist der IC eine Schiefe-Aussage, kein Edge.)")
    add("-" * 108)
    for key, table in payload["shape"].items():
        add(
            f"\n[Horizont {key}]  n={table['n']:,}  Tage={table['n_days']}  "
            f"Monotonie(Mittel)={table['monotonicity_mean']:+.3f}  "
            f"Monotonie(Median)={table['monotonicity_median']:+.3f}  "
            f"Monotonie(Mittel ohne D10)={table['monotonicity_mean_ex_top']:+.3f}"
        )
        add(
            f"  {'Dez':<5}{'ATR%':>7}{'n':>8}{'Mittel':>10}{'Median':>10}{'getrimmt':>10}"
            f"{'Anteil>0':>10}{'Schiefe':>9}{'Top1%-Anteil':>14}{'CI (Tage)':>24}"
        )
        for bucket in table["buckets"]:
            add(
                f"  {bucket['bucket']:<5}{bucket['feature_median']:>7.2f}{bucket['n']:>8}"
                f"{_pct(bucket['mean']):>10}{_pct(bucket['median']):>10}"
                f"{_pct(bucket['trimmed_mean_1pct']):>10}{bucket['share_positive']:>10.3f}"
                f"{bucket['skew']:>9.2f}{bucket['top1pct_share_of_sum']:>14.2f}"
                f"   [{_pct(bucket['ci_low'])},{_pct(bucket['ci_high'])}]"
            )
        spread = table["bottom_minus_top_mean"]
        add(
            f"  Strategie 'long ruhig / short bewegt' (D1 minus D10), taeglich gepaart:"
            f"  Mittel={_pct(spread['value'])}"
            f"  CI [{_pct(spread['ci_low'])},{_pct(spread['ci_high'])}]  p={spread['p']:.3f}"
            f"  {'SIGNIFIKANT' if spread['significant'] else 'im Rauschen'}"
        )
        add(f"  dieselbe Differenz im Median: {_pct(table['bottom_minus_top_median'])}")
    add("")

    add("-" * 108)
    add("3  LIQUIDITAET   (Ausschluss der illiquidesten Coins nach 30-Bar-Dollarvolumen)")
    add("-" * 108)
    add(
        f"{'ADV-Rang ab':<14}{'Zeilen':>10}{'IC':>9}{'CI':>22}"
        f"{'D1-D5 Mittel':>14}{'CI':>24}{'D1-D5 Median':>14}"
    )
    for row in payload["liquidity_robustness"]:
        add(
            f"{row['adv_rank_cut']:<14.2f}{row['n_rows']:>10,}{row['ic']:>9.4f}"
            f"   [{row['ic_ci_low']:+.4f}, {row['ic_ci_high']:+.4f}]"
            f"{_pct(row['bottom_minus_top_mean']):>14}"
            f"   [{_pct(row['bottom_minus_top_ci_low'])},{_pct(row['bottom_minus_top_ci_high'])}]"
            f"{_pct(row['bottom_minus_top_median']):>14}"
        )
    add("")

    add("-" * 108)
    add("4  EIGENSTAENDIGKEIT   (Fama-MacBeth: Koeffizient = Renditedifferenz hoechster minus")
    add("   niedrigster Rang bei sonst gleichen Merkmalen)")
    add("-" * 108)
    for name, entry in payload["independence"].items():
        if not isinstance(entry, dict) or "coef" not in entry:
            continue
        add(f"\n  [{name}]  Scans={entry['n_scans']}  Tage={entry['n_days']}")
        for feature, coefficient in entry["coef"].items():
            add(
                f"    {feature:<20}{_pct(coefficient['mean']):>10}"
                f"  CI [{_pct(coefficient['ci_low'])},{_pct(coefficient['ci_high'])}]"
                f"  p={coefficient['p']:.3f}"
                f"  {'SIGNIFIKANT' if coefficient['significant'] else 'im Rauschen'}"
            )
    for key in ("double_sort_market_cap", "double_sort_liquidity"):
        entry = payload["independence"].get(key)
        if not entry:
            continue
        add(f"\n  [{key}]  Vola-Terzile innerhalb {entry['control']}-Terzilen (Mittel):")
        for spread in entry["spreads"]:
            add(
                f"    Kontroll-Terzil {spread['control_bucket']}:"
                f"  niedrig minus hoch Vola = {_pct(spread['low_minus_high_mean'])}"
                f"  (Median {_pct(spread['low_minus_high_median'])})"
            )
    add(f"\n  {payload['independence']['note_market_cap']}")
    add("")

    add("-" * 108)
    add("5  MEAN REVERSION UND KOMBINATION   (Signal immer so gedreht, dass hoch = long)")
    add("-" * 108)
    for horizon, rows in payload["reversion_combinations"].items():
        if not rows:
            continue
        add(f"\n  Horizont {horizon}")
        add(
            f"    {'Signal':<30}{'IC':>9}{'CI':>21}{'gl.Vz.':>9}{'Q5-Q1 Mittel':>14}"
            f"{'Q5-Q1 Median':>14}{'TopN Mittel':>13}{'CI':>24}  sig"
        )
        for row in rows:
            add(
                f"    {row['name']:<30}{row['ic']:>9.4f}"
                f"  [{row['ic_ci_low']:+.4f},{row['ic_ci_high']:+.4f}]"
                f"{row['ic_months_same_sign']:>5}/{row['ic_months']:<3}"
                f"{_pct(row['q5_minus_q1_mean']):>14}{_pct(row['q5_minus_q1_median']):>14}"
                f"{_pct(row['top_n_mean']):>13}"
                f"   [{_pct(row['top_n_ci_low'])},{_pct(row['top_n_ci_high'])}]"
                f"  {'JA' if row['top_n_significant'] else '--'}"
            )
    add("")

    add("-" * 108)
    add("6  KOSTENHUERDE   (1R = 1.5*ATR, daher Gebuehr in R = Rundlaufgebuehr% / Stopabstand%)")
    add("-" * 108)
    costs = payload["cost_hurdle"]
    add(
        f"  Gebuehr: {costs['fee_per_side_pct']}% je Seite = "
        f"{costs['fee_round_trip_pct']}% Rundlauf je Bein"
    )
    add(
        f"\n  {'Vola-Q':<8}{'ATR%':>7}{'Stop%':>8}{'Gebuehr R':>11}"
        f"{'Alpha% 24h':>12}{'Alpha R 24h':>13}{'netto R 24h':>13}"
    )
    for row in costs["by_vol_quintile"]:
        add(
            f"  Q{row['quintile']:<7}{row['atr_percent_median']:>7.2f}"
            f"{row['stop_distance_pct']:>8.2f}{row['fee_in_r']:>11.4f}"
            f"{row.get('alpha_pct_24h', float('nan')):>12.4f}"
            f"{row.get('alpha_r_24h', float('nan')):>13.4f}"
            f"{row.get('net_r_24h', float('nan')):>13.4f}"
        )
    add("\n  Spread je Horizont (Prozent, vor/nach Gebuehr):")
    add(
        f"  {'Hor':<6}{'long ruhig':>13}{'short bewegt':>14}{'Spread':>10}"
        f"{'netto long-only':>17}{'netto long/short':>18}"
    )
    for horizon, entry in costs["by_horizon"].items():
        add(
            f"  {horizon:<6}{entry['long_only_low_vol_pct']:>13.4f}"
            f"{entry['short_leg_high_vol_pct']:>14.4f}{entry['long_short_spread_pct']:>10.4f}"
            f"{entry['net_long_only_pct']:>17.4f}{entry['net_long_short_pct']:>18.4f}"
        )
    add("")

    add("-" * 108)
    add("7  ZWEI DISJUNKTE FENSTER")
    add("-" * 108)
    for row in payload["windows"]:
        add(
            f"  {row['window']}  {row['start'][:10]}..{row['end'][:10]}  Tage={row['n_days']}"
            f"  IC={row['ic']:+.4f} [{row['ic_ci_low']:+.4f},{row['ic_ci_high']:+.4f}]"
            f"  Q1={_pct(row['bottom_quintile_mean'])}  Q5={_pct(row['top_quintile_mean'])}"
            f"  Q1-Q5={_pct(row['bottom_minus_top_mean'])}"
            f" [{_pct(row['bottom_minus_top_ci_low'])},{_pct(row['bottom_minus_top_ci_high'])}]"
        )
    add("")

    add("-" * 108)
    add("8  MONATS-IC DES FAKTORS (Fokus-Horizont)")
    add("-" * 108)
    for row in payload["monthly_ic_focus"]:
        add(
            f"  {row['month']}  Scans={row['n_scans']:>4}  IC={row['mean_ic']:+.4f}"
            f"  Anteil positiver Scans={row['share_positive']:.2f}"
        )

    if not payload.get("backtest", {}).get("skipped"):
        add("")
        add("-" * 108)
        add("9  BAR-REPLAY-BACKTEST   (long-only, risikonormiert in R, 1 Position/Symbol)")
        add("-" * 108)
        bt = payload["backtest"]
        add(
            f"  Hold={bt['hold_hours']}h  Top-N={bt['top_n']}  "
            f"Stop={bt['stop']}  Gebuehr={bt['fee_round_trip_pct']}% RT  "
            f"Trades gesamt={bt['n_trades_total']:,}"
        )
        add(
            f"  {'Strategie':<34}{'N':>7}{'net R':>9}{'CI (Tage)':>24}"
            f"{'sig':>5}{'WR':>7}{'PF':>7}{'maxDD R':>9}"
        )
        for row in bt["by_strategy"]:
            if row.get("n", 0) == 0:
                continue
            add(
                f"  {row['label']:<34}{row['n']:>7}{row['mean_net_r']:>9.4f}"
                f"   [{row['ci_low']:+.4f},{row['ci_high']:+.4f}]"
                f"{'  JA' if row['significant'] else '  --':>5}"
                f"{row['win_rate']:>7.3f}{row['profit_factor']:>7.2f}"
                f"{row['max_drawdown_r']:>9.2f}"
            )
        add("\n  Break-even Low-Vol je Horizont (net R > 0, CI exkl. 0):")
        add(
            f"  {'Hor':>5}{'Trades':>8}{'net R':>9}{'CI':>24}{'Stop%':>8}"
            f"{'Fee R':>8}{'clears':>8}"
        )
        for row in bt["break_even_low_vol"]:
            add(
                f"  {row['horizon_h']:>4}h{row['n_trades']:>8}{row['mean_net_r']:>9.4f}"
                f"   [{row['ci_low']:+.4f},{row['ci_high']:+.4f}]"
                f"{row['median_stop_pct']:>8.2f}{row['fee_in_r']:>8.4f}"
                f"{'  JA' if row['clears_fees'] else '  --':>8}"
            )
        add("\n  Walk-forward Low-Vol:")
        for row in bt["walk_forward_low_vol"]:
            add(
                f"  {row['window']}  {row['start'][:10]}..{row['end'][:10]}"
                f"  net R={row.get('mean_net_r', float('nan')):+.4f}"
                f"  [{row.get('ci_low', float('nan')):+.4f},"
                f"{row.get('ci_high', float('nan')):+.4f}]"
                f"  {'SIGNIFIKANT' if row.get('significant') else 'im Rauschen'}"
            )

    verdict = payload.get("verdict", {})
    if verdict:
        add("")
        add("=" * 108)
        add(f"URTEIL: {verdict.get('verdict', 'n/a').upper()}")
        add("=" * 108)
        cfg = verdict.get("recommended_config", {})
        for key, value in cfg.items():
            add(f"  {key}: {value}")
        if verdict.get("note"):
            add(f"  Hinweis: {verdict['note']}")
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--tag", default="long_current")
    parser.add_argument("--return-timeframe", default="4h", choices=sorted(TF_HOURS))
    parser.add_argument("--horizons", default="4,8,24,72,120")
    parser.add_argument("--focus-horizon", type=int, default=24)
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--backtest-hold-hours", type=int, default=24)
    parser.add_argument(
        "--skip-backtest",
        action="store_true",
        help="Bar-Replay ueberspringen (nur Faktor-Charakterisierung)",
    )
    parser.add_argument(
        "--max-atr-percent",
        type=float,
        default=25.0,
        help="Obergrenze gegen Kerzenluecken-Artefakte",
    )
    parser.add_argument("--start", default="")
    parser.add_argument("--end", default="")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args(list(argv) if argv is not None else None)

    payload = run(args)
    print(render_text(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
