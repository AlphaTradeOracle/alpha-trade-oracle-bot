"""Prueft, ob der Signal-Score zukuenftige Renditen vorhersagt.

Diese Analyse ist strikt read-only: sie liest Signale, Score-Komponenten und
Kerzen aus der Produktions-Datenbank (bzw. aus zuvor exportierten CSVs) und
misst, ob ein hoeherer Score mit einer hoeheren zukuenftigen Rendite einhergeht.
Es wird kein Strategie-Code veraendert.

Kernideen
---------
* Der Score ist gerichtet: ``score = clamp((gewichtete Rohsumme + 100) / 2, 0, 100)``
  (siehe ``app/signals/engine.py::_weighted_score``). 50 ist neutral, > 50
  bullisch, < 50 baerisch. Ein Score mit Vorhersagekraft muss also positiv mit
  der zukuenftigen Rendite korrelieren.
* Forward Returns stammen ausschliesslich aus Kerzen, die **nach** dem
  Signalzeitpunkt schliessen. Der Einstiegspreis ist der Schlusskurs der letzten
  zum Signalzeitpunkt bereits geschlossenen Kerze -- damit ist Look-ahead-Bias
  ausgeschlossen.
* Alle Coins laufen in einem 3-Tage-Fenster stark gemeinsam. Deshalb werden
  Renditen zusaetzlich pro Scan-Batch quer-schnittlich zentriert
  (``ret - mean(ret des Batches)``). Nur der zentrierte Teil ist echtes Alpha,
  der Rest ist Marktbeta.
* Konfidenzintervalle werden per **Cluster-Bootstrap ueber Scan-Batches**
  gerechnet, nicht i.i.d. ueber Einzelsignale. Bei ~37 Scans mit je ~300 stark
  korrelierten Coins ist die effektive Stichprobe die Zahl der Scans, nicht die
  Zahl der Zeilen. Der i.i.d.-Bootstrap wird nur zum Vergleich mitgefuehrt und
  ist systematisch zu optimistisch.

Aufruf
------
    python scripts/analyze_score_edge.py                 # nutzt exports/edge_data/*.csv
    python scripts/analyze_score_edge.py --fetch         # exportiert vorher frisch vom VPS

Ausgabe
-------
    exports/score_edge_analysis.json   maschinenlesbare Ergebnisse
    exports/score_edge_analysis.txt    Klartext-Zusammenfassung
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / "exports" / "edge_data"
DEFAULT_OUT_DIR = REPO_ROOT / "exports"

# --- VPS-Export ------------------------------------------------------------
# Der Export laeuft serverseitig im Postgres-Container (COPY ... TO), damit kein
# psycopg2 lokal noetig ist. Reine Lesezugriffe.
VPS_HOST = "root@187.124.12.83"
VPS_KEY = r"C:\Users\Admin\.ssh\cursor_vps_deploy"
PG_CONTAINER = "alpha-trade-oracle-postgres"
PG_USER = "alpha_trade_oracle"
PG_DB = "alpha_trade_oracle"

EXPORT_SQL = """
COPY (SELECT s.id AS signal_id, s.asset_id, a.symbol, s.created_at, s.direction,
             s.score, s.confidence, s.primary_timeframe, s.market_phase,
             s.reference_price, s.risk_reward_ratio, s.data_quality
      FROM signals s JOIN assets a ON a.id = s.asset_id
      ORDER BY s.created_at, s.id)
  TO '/tmp/edge_signals.csv' WITH (FORMAT csv, HEADER true);
COPY (SELECT signal_id, category, raw_score, weight, weighted_score
      FROM signal_score_components ORDER BY signal_id, category)
  TO '/tmp/edge_components.csv' WITH (FORMAT csv, HEADER true);
COPY (SELECT asset_id, open_time, close, high, low, volume FROM market_candles
      WHERE timeframe = '1h' ORDER BY asset_id, open_time)
  TO '/tmp/edge_candles_1h.csv' WITH (FORMAT csv, HEADER true);
COPY (SELECT asset_id, open_time, close, high, low, volume FROM market_candles
      WHERE timeframe = '4h' ORDER BY asset_id, open_time)
  TO '/tmp/edge_candles_4h.csv' WITH (FORMAT csv, HEADER true);
COPY (SELECT id AS asset_id, symbol, market_cap_rank, in_universe FROM assets)
  TO '/tmp/edge_assets.csv' WITH (FORMAT csv, HEADER true);
"""

EXPORT_FILES = [
    "edge_signals.csv",
    "edge_components.csv",
    "edge_candles_1h.csv",
    "edge_candles_4h.csv",
    "edge_assets.csv",
]

CATEGORIES = [
    "trend",
    "momentum",
    "volume",
    "volatility",
    "market_structure",
    "multi_timeframe",
    "risk_reward",
]

HORIZONS_H = [4, 8, 24]
N_DECILES = 10
N_BOOT = 10_000
RNG_SEED = 20260731


# ---------------------------------------------------------------------------
# Statistik-Bausteine (bewusst ohne scipy, damit das Skript ueberall laeuft)
# ---------------------------------------------------------------------------


def _normal_sf(z: float) -> float:
    """Obere Schwanzwahrscheinlichkeit der Standardnormalverteilung."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def ranks(values: np.ndarray) -> np.ndarray:
    """Durchschnittsraenge (Ties werden gemittelt) -- Basis fuer Spearman."""
    return pd.Series(values).rank(method="average").to_numpy(dtype=float)


def pearson(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 3:
        return float("nan")
    sx, sy = x.std(), y.std()
    if sx == 0 or sy == 0:
        return float("nan")
    return float(((x - x.mean()) * (y - y.mean())).mean() / (sx * sy))


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    return pearson(ranks(x), ranks(y))


def fisher_pvalue(rho: float, n: int) -> float:
    """p-Wert unter i.i.d.-Annahme (Fisher-z). Zu optimistisch bei Clustern."""
    if not np.isfinite(rho) or n < 5 or abs(rho) >= 1:
        return float("nan")
    z = math.atanh(rho) * math.sqrt(n - 3)
    return float(2.0 * _normal_sf(abs(z)))


def _group_sums(x: np.ndarray, y: np.ndarray, cluster_idx: np.ndarray, n_clusters: int) -> np.ndarray:
    """Pro Cluster die 6 Momente, die fuer Pearson noetig sind."""
    stats = np.zeros((n_clusters, 6), dtype=float)
    np.add.at(stats, (cluster_idx, 0), 1.0)
    np.add.at(stats, (cluster_idx, 1), x)
    np.add.at(stats, (cluster_idx, 2), y)
    np.add.at(stats, (cluster_idx, 3), x * x)
    np.add.at(stats, (cluster_idx, 4), y * y)
    np.add.at(stats, (cluster_idx, 5), x * y)
    return stats


def _corr_from_moments(m: np.ndarray) -> np.ndarray:
    """Pearson-r aus aggregierten Momenten (n, sx, sy, sxx, syy, sxy)."""
    n, sx, sy, sxx, syy, sxy = (m[..., i] for i in range(6))
    with np.errstate(invalid="ignore", divide="ignore"):
        cov = sxy / n - (sx / n) * (sy / n)
        vx = sxx / n - (sx / n) ** 2
        vy = syy / n - (sy / n) ** 2
        return cov / np.sqrt(np.clip(vx, 1e-18, None) * np.clip(vy, 1e-18, None))


def bootstrap_corr(
    x: np.ndarray,
    y: np.ndarray,
    clusters: np.ndarray | None,
    n_boot: int = N_BOOT,
    rng: np.random.Generator | None = None,
) -> dict:
    """Bootstrap-CI fuer die (Rang-)Korrelation.

    ``clusters`` = Scan-Batch je Zeile -> Cluster-Bootstrap (ganze Scans werden
    mit Zuruecklegen gezogen). ``None`` -> klassischer i.i.d.-Bootstrap.

    Trick: da nur ganze Cluster gezogen werden, laesst sich jedes Resample als
    gewichtete Summe der Cluster-Momente schreiben. Damit sind 10.000 Resamples
    eine einzige Matrixmultiplikation statt 10.000 Schleifendurchlaeufe.
    """
    rng = rng or np.random.default_rng(RNG_SEED)
    n = len(x)
    if n < 10:
        return {"ci_low": float("nan"), "ci_high": float("nan"), "p_two_sided": float("nan")}

    if clusters is None:
        cluster_idx = np.arange(n)
        n_clusters = n
    else:
        codes, _ = pd.factorize(clusters)
        cluster_idx = codes.astype(int)
        n_clusters = int(codes.max()) + 1

    moments = _group_sums(x, y, cluster_idx, n_clusters)

    draws = np.empty(n_boot, dtype=float)
    chunk = max(1, min(2000, n_boot))
    done = 0
    while done < n_boot:
        size = min(chunk, n_boot - done)
        weights = rng.multinomial(n_clusters, np.full(n_clusters, 1.0 / n_clusters), size=size)
        resampled = weights.astype(float) @ moments  # (size, 6)
        draws[done : done + size] = _corr_from_moments(resampled)
        done += size

    draws = draws[np.isfinite(draws)]
    if draws.size == 0:
        return {"ci_low": float("nan"), "ci_high": float("nan"), "p_two_sided": float("nan")}
    lo, hi = np.percentile(draws, [2.5, 97.5])
    share_neg = float((draws <= 0).mean())
    p = 2.0 * min(share_neg, 1.0 - share_neg)
    return {
        "ci_low": float(lo),
        "ci_high": float(hi),
        "p_two_sided": float(min(1.0, max(p, 1.0 / max(draws.size, 1)))),
        "n_boot_effective": int(draws.size),
        "n_clusters": int(n_clusters),
    }


def bootstrap_bucket_means(
    values: np.ndarray,
    bucket: np.ndarray,
    clusters: np.ndarray,
    n_buckets: int,
    n_boot: int = N_BOOT,
    rng: np.random.Generator | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cluster-Bootstrap-CIs fuer Bucket-Mittelwerte + Top-minus-Bottom-Spread."""
    rng = rng or np.random.default_rng(RNG_SEED + 1)
    codes, _ = pd.factorize(clusters)
    n_clusters = int(codes.max()) + 1

    sums = np.zeros((n_buckets, n_clusters))
    counts = np.zeros((n_buckets, n_clusters))
    np.add.at(sums, (bucket, codes), values)
    np.add.at(counts, (bucket, codes), 1.0)

    lows = np.full(n_buckets, np.nan)
    highs = np.full(n_buckets, np.nan)
    spread_draws = np.empty(n_boot)

    done = 0
    all_draws = np.empty((n_buckets, n_boot))
    chunk = max(1, min(2000, n_boot))
    while done < n_boot:
        size = min(chunk, n_boot - done)
        w = rng.multinomial(n_clusters, np.full(n_clusters, 1.0 / n_clusters), size=size).astype(float)
        num = sums @ w.T  # (n_buckets, size)
        den = counts @ w.T
        with np.errstate(invalid="ignore", divide="ignore"):
            all_draws[:, done : done + size] = np.where(den > 0, num / den, np.nan)
        done += size

    for b in range(n_buckets):
        col = all_draws[b][np.isfinite(all_draws[b])]
        if col.size:
            lows[b], highs[b] = np.percentile(col, [2.5, 97.5])
    spread_draws = all_draws[n_buckets - 1] - all_draws[0]
    spread_draws = spread_draws[np.isfinite(spread_draws)]
    if spread_draws.size:
        s_lo, s_hi = np.percentile(spread_draws, [2.5, 97.5])
        share_neg = float((spread_draws <= 0).mean())
        s_p = 2.0 * min(share_neg, 1.0 - share_neg)
    else:
        s_lo = s_hi = s_p = float("nan")
    return lows, highs, np.array([s_lo, s_hi, s_p])


# ---------------------------------------------------------------------------
# Daten laden
# ---------------------------------------------------------------------------


def _ssh(*args: str) -> None:
    subprocess.run(["ssh", "-i", VPS_KEY, "-o", "StrictHostKeyChecking=no", VPS_HOST, *args], check=True)


def fetch_from_vps(data_dir: Path) -> None:
    """Frischer read-only Export vom VPS in ``data_dir``."""
    data_dir.mkdir(parents=True, exist_ok=True)
    local_sql = data_dir / "_export.sql"
    local_sql.write_text(EXPORT_SQL, encoding="utf-8", newline="\n")
    subprocess.run(
        ["scp", "-i", VPS_KEY, "-o", "StrictHostKeyChecking=no", str(local_sql), f"{VPS_HOST}:/tmp/exp.sql"],
        check=True,
    )
    _ssh("docker", "cp", "/tmp/exp.sql", f"{PG_CONTAINER}:/tmp/exp.sql")
    _ssh("docker", "exec", PG_CONTAINER, "psql", "-U", PG_USER, "-d", PG_DB, "-f", "/tmp/exp.sql")
    for name in EXPORT_FILES:
        _ssh("docker", "cp", f"{PG_CONTAINER}:/tmp/{name}", f"/tmp/{name}")
        subprocess.run(
            ["scp", "-i", VPS_KEY, "-o", "StrictHostKeyChecking=no", f"{VPS_HOST}:/tmp/{name}", str(data_dir / name)],
            check=True,
        )


def load_data(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    signals = pd.read_csv(data_dir / "edge_signals.csv", parse_dates=["created_at"])
    components = pd.read_csv(data_dir / "edge_components.csv")
    candles = pd.read_csv(data_dir / "edge_candles_1h.csv", parse_dates=["open_time"])
    return signals, components, candles


# ---------------------------------------------------------------------------
# Feature-Aufbereitung
# ---------------------------------------------------------------------------


def assign_scan_batches(created_at: pd.Series, gap_minutes: int = 20) -> pd.Series:
    """Signale in Scan-Batches gruppieren (neue Batch bei Luecke > gap)."""
    uniq = np.sort(created_at.unique())
    gaps = np.diff(uniq) > np.timedelta64(gap_minutes, "m")
    batch_of_ts = np.concatenate([[0], np.cumsum(gaps)])
    mapping = dict(zip(uniq, batch_of_ts))
    return created_at.map(mapping).astype(int)


def build_panel(signals: pd.DataFrame, components: pd.DataFrame, candles: pd.DataFrame) -> pd.DataFrame:
    """Signale + Komponenten + Forward Returns zu einem Panel verbinden."""
    wide = components.pivot_table(index="signal_id", columns="category", values="raw_score")
    wide.columns = [f"c_{c}" for c in wide.columns]
    panel = signals.merge(wide, left_on="signal_id", right_index=True, how="left")

    panel["batch"] = assign_scan_batches(panel["created_at"])
    panel["anchor"] = panel["created_at"].dt.floor("h")
    # Letzte zum Signalzeitpunkt bereits GESCHLOSSENE Kerze: open_time = anchor - 1h
    # (sie schliesst exakt bei ``anchor`` <= created_at). Kein Look-ahead.
    panel["entry_open_time"] = panel["anchor"] - pd.Timedelta(hours=1)

    close_map = candles.set_index(["asset_id", "open_time"])["close"]

    def price_at(offset_h: int) -> pd.Series:
        idx = pd.MultiIndex.from_arrays(
            [panel["asset_id"], panel["entry_open_time"] + pd.Timedelta(hours=offset_h)]
        )
        return pd.Series(close_map.reindex(idx).to_numpy(), index=panel.index)

    panel["entry_close"] = price_at(0)
    for h in HORIZONS_H:
        exit_price = price_at(h)
        panel[f"ret_{h}h"] = exit_price / panel["entry_close"] - 1.0
        # Robustheits-Variante: Einstieg zum Live-Preis des Signals.
        panel[f"retref_{h}h"] = exit_price / panel["reference_price"] - 1.0

    # Cross-sektional zentrierte Rendite je Scan-Batch = markt-neutrales Alpha.
    for h in HORIZONS_H:
        col = f"ret_{h}h"
        panel[f"xs_{h}h"] = panel[col] - panel.groupby("batch")[col].transform("mean")

    # Rang des Scores innerhalb des Scans (0..1) -- testet relative Aussagekraft.
    panel["score_rank"] = panel.groupby("batch")["score"].rank(pct=True)
    for cat in CATEGORIES:
        col = f"c_{cat}"
        if col in panel.columns:
            panel[f"rank_{cat}"] = panel.groupby("batch")[col].rank(pct=True)

    dir_sign = {"LONG": 1.0, "STRONG_LONG": 1.0, "SHORT": -1.0, "STRONG_SHORT": -1.0, "NEUTRAL": 0.0}
    panel["dir_sign"] = panel["direction"].map(dir_sign).fillna(0.0)
    return panel


# ---------------------------------------------------------------------------
# Analysen
# ---------------------------------------------------------------------------


@dataclass
class ICResult:
    feature: str
    horizon: str
    target: str
    n: int
    n_clusters: int
    ic: float
    p_iid: float
    ci_cluster: tuple[float, float]
    p_cluster: float
    ci_iid: tuple[float, float]

    def to_dict(self) -> dict:
        return {
            "feature": self.feature,
            "horizon": self.horizon,
            "target": self.target,
            "n": self.n,
            "n_clusters": self.n_clusters,
            "ic": self.ic,
            "p_iid": self.p_iid,
            "ci_cluster_low": self.ci_cluster[0],
            "ci_cluster_high": self.ci_cluster[1],
            "p_cluster": self.p_cluster,
            "ci_iid_low": self.ci_iid[0],
            "ci_iid_high": self.ci_iid[1],
            "significant": bool(
                np.isfinite(self.ci_cluster[0])
                and np.isfinite(self.ci_cluster[1])
                and self.ci_cluster[0] * self.ci_cluster[1] > 0
            ),
        }


def information_coefficient(
    panel: pd.DataFrame, feature: str, target: str, horizon_label: str, rng: np.random.Generator
) -> ICResult | None:
    sub = panel[[feature, target, "batch"]].dropna()
    if len(sub) < 50:
        return None
    x = ranks(sub[feature].to_numpy(dtype=float))
    y = ranks(sub[target].to_numpy(dtype=float))
    ic = pearson(x, y)
    cl = bootstrap_corr(x, y, sub["batch"].to_numpy(), rng=rng)
    iid = bootstrap_corr(x, y, None, rng=rng)
    return ICResult(
        feature=feature,
        horizon=horizon_label,
        target=target,
        n=len(sub),
        n_clusters=int(sub["batch"].nunique()),
        ic=ic,
        p_iid=fisher_pvalue(ic, len(sub)),
        ci_cluster=(cl["ci_low"], cl["ci_high"]),
        p_cluster=cl["p_two_sided"],
        ci_iid=(iid["ci_low"], iid["ci_high"]),
    )


def decile_table(
    panel: pd.DataFrame, feature: str, target: str, rng: np.random.Generator, n_buckets: int = N_DECILES
) -> dict | None:
    sub = panel[[feature, target, "batch"]].dropna()
    if len(sub) < n_buckets * 10:
        return None
    try:
        buckets = pd.qcut(sub[feature].rank(method="first"), n_buckets, labels=False)
    except ValueError:
        return None
    values = sub[target].to_numpy(dtype=float)
    lows, highs, spread = bootstrap_bucket_means(
        values, buckets.to_numpy(dtype=int), sub["batch"].to_numpy(), n_buckets, rng=rng
    )
    grouped = sub.assign(_b=buckets).groupby("_b")
    means = grouped[target].mean().to_numpy()
    medians = grouped[target].median().to_numpy()
    counts = grouped[target].size().to_numpy()
    feat_lo = grouped[feature].min().to_numpy()
    feat_hi = grouped[feature].max().to_numpy()
    monotonicity = spearman(np.arange(n_buckets, dtype=float), means)
    return {
        "feature": feature,
        "target": target,
        "n": int(len(sub)),
        "n_clusters": int(sub["batch"].nunique()),
        "buckets": [
            {
                "bucket": int(i + 1),
                "feature_min": float(feat_lo[i]),
                "feature_max": float(feat_hi[i]),
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
        "monotonicity_spearman": float(monotonicity),
    }


def ic_by_batch(panel: pd.DataFrame, feature: str, target: str, horizon_h: int, min_n: int = 30) -> dict:
    """IC-Zeitreihe: pro Scan-Batch eine eigene Querschnitts-IC.

    Das ist der ehrlichste Test bei ueberlappenden Forward-Fenstern: statt
    3.000 abhaengige Zeilen zu zaehlen, betrachten wir k unabhaengige(re)
    Querschnitte. Zusaetzlich wird ausgewiesen, wie viele davon sich zeitlich
    UEBERHAUPT NICHT ueberlappen (Scan-Abstand >= Horizont) -- das ist die
    tatsaechliche Zahl unabhaengiger Beobachtungen.
    """
    sub = panel[[feature, target, "batch", "created_at"]].dropna()
    rows = []
    for batch, grp in sub.groupby("batch"):
        if len(grp) < min_n:
            continue
        ic = spearman(grp[feature].to_numpy(dtype=float), grp[target].to_numpy(dtype=float))
        if np.isfinite(ic):
            rows.append({"batch": int(batch), "t": grp["created_at"].min(), "n": int(len(grp)), "ic": float(ic)})
    if not rows:
        return {"n_batches": 0}
    ics = np.array([r["ic"] for r in rows])
    times = pd.Series([r["t"] for r in rows]).sort_values()
    # Nicht ueberlappende Scans greedy zaehlen
    n_indep, last = 0, None
    for t in times:
        if last is None or (t - last) >= pd.Timedelta(hours=horizon_h):
            n_indep += 1
            last = t
    mean, sd = float(ics.mean()), float(ics.std(ddof=1)) if len(ics) > 1 else float("nan")
    tstat = mean / (sd / math.sqrt(len(ics))) if len(ics) > 1 and sd > 0 else float("nan")
    return {
        "feature": feature,
        "target": target,
        "horizon_h": horizon_h,
        "n_batches": len(ics),
        "n_non_overlapping": int(n_indep),
        "mean_ic": mean,
        "std_ic": sd,
        "t_stat_naive": float(tstat),
        "share_positive": float((ics > 0).mean()),
        "ic_series": [{"t": str(r["t"]), "n": r["n"], "ic": r["ic"]} for r in rows],
    }


def top_decile_by_batch(panel: pd.DataFrame, horizon_h: int, top_frac: float = 0.1) -> dict:
    """Wie oft verliert das oberste Score-Dezil wirklich? (pro Scan)"""
    col = f"xs_{horizon_h}h"
    sub = panel[["score", col, "batch", "created_at"]].dropna()
    rows = []
    for batch, grp in sub.groupby("batch"):
        if len(grp) < 30:
            continue
        thr = grp["score"].quantile(1 - top_frac)
        top = grp[grp["score"] >= thr]
        bot = grp[grp["score"] <= grp["score"].quantile(top_frac)]
        rows.append(
            {
                "t": str(grp["created_at"].min()),
                "n_top": int(len(top)),
                "top_mean": float(top[col].mean()),
                "bottom_mean": float(bot[col].mean()),
                "spread": float(top[col].mean() - bot[col].mean()),
            }
        )
    spreads = np.array([r["spread"] for r in rows]) if rows else np.array([])
    return {
        "horizon_h": horizon_h,
        "n_batches": len(rows),
        "mean_spread": float(spreads.mean()) if spreads.size else float("nan"),
        "median_spread": float(np.median(spreads)) if spreads.size else float("nan"),
        "share_negative": float((spreads < 0).mean()) if spreads.size else float("nan"),
        "worst_batch": float(spreads.min()) if spreads.size else float("nan"),
        "best_batch": float(spreads.max()) if spreads.size else float("nan"),
        "series": rows,
    }


def direction_stats(panel: pd.DataFrame, horizon: int) -> list[dict]:
    """Verdient die diskrete Richtungsentscheidung Geld? (Signed Return)"""
    col = f"ret_{horizon}h"
    xs = f"xs_{horizon}h"
    out = []
    for label, mask in (
        ("LONG (inkl. STRONG_LONG)", panel["dir_sign"] > 0),
        ("NEUTRAL", panel["dir_sign"] == 0),
        ("SHORT (inkl. STRONG_SHORT)", panel["dir_sign"] < 0),
    ):
        sub = panel[mask & panel[col].notna()]
        if sub.empty:
            continue
        sign = 1.0 if "LONG" in label else (-1.0 if "SHORT" in label else 0.0)
        signed_raw = sub[col] * sign if sign else sub[col]
        signed_xs = sub[xs] * sign if sign else sub[xs]
        out.append(
            {
                "group": label,
                "n": int(len(sub)),
                "n_clusters": int(sub["batch"].nunique()),
                "mean_raw_ret": float(sub[col].mean()),
                "mean_signed_ret": float(signed_raw.mean()),
                "mean_signed_xs_ret": float(signed_xs.mean()),
                "hit_rate_signed": float((signed_raw > 0).mean()) if sign else float("nan"),
                "hit_rate_signed_xs": float((signed_xs > 0).mean()) if sign else float("nan"),
            }
        )
    return out


def regime_split(panel: pd.DataFrame, candles: pd.DataFrame, btc_asset_id: int | None, rng) -> dict:
    """IC getrennt nach BTC-Regime und nach Marktbreite je Scan."""
    result: dict = {}
    if btc_asset_id is not None:
        btc = candles[candles["asset_id"] == btc_asset_id].set_index("open_time")["close"].sort_index()
        btc_24h = btc.pct_change(24)
        anchor_ret = panel["entry_open_time"].map(btc_24h)
        panel = panel.assign(btc_trend_24h=anchor_ret)
        result["btc_regime_available"] = True
    else:
        result["btc_regime_available"] = False
        return result

    breadth = panel.groupby("batch")["ret_24h"].transform(lambda s: (s > 0).mean())
    panel = panel.assign(breadth_24h=breadth)

    for name, mask in (
        ("BTC 24h < 0 (Abwaerts)", panel["btc_trend_24h"] < 0),
        ("BTC 24h >= 0 (Aufwaerts)", panel["btc_trend_24h"] >= 0),
    ):
        sub = panel[mask]
        res = information_coefficient(sub, "score", "xs_24h", "24h", rng) if len(sub) > 100 else None
        result[name] = res.to_dict() if res else {"n": int(len(sub)), "note": "zu wenig Daten"}
    result["btc_trend_range"] = {
        "min": float(np.nanmin(panel["btc_trend_24h"])) if panel["btc_trend_24h"].notna().any() else None,
        "max": float(np.nanmax(panel["btc_trend_24h"])) if panel["btc_trend_24h"].notna().any() else None,
        "share_negative": float((panel["btc_trend_24h"] < 0).mean()),
    }
    return result


# ---------------------------------------------------------------------------
# Langfenster-Proxy auf 4h-Kerzen
# ---------------------------------------------------------------------------


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def _rsi(s: pd.Series, period: int = 14) -> pd.Series:
    delta = s.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = up / down.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def long_window_proxy(candles_4h: pd.DataFrame, rng: np.random.Generator, min_bars: int = 200) -> dict:
    """Feature-Proxy ueber ~100 Tage 4h-Historie.

    Wichtig: das ist NICHT der Produktions-Score. Der echte Score laesst sich
    historisch nicht rekonstruieren, weil er mehrere Timeframes, Struktur- und
    R:R-Logik braucht. Getestet wird hier nur, ob die Feature-*Familien*, aus
    denen der Score gebaut ist (Trend, Momentum, Volumen, Volatilitaet), ueber
    ein laengeres Fenster ueberhaupt Vorhersagekraft haben.
    """
    df = candles_4h.sort_values(["asset_id", "open_time"]).copy()
    sizes = df.groupby("asset_id")["close"].transform("size")
    df = df[sizes >= min_bars]
    if df.empty:
        return {"available": False, "reason": "zu wenig 4h-Historie"}

    g = df.groupby("asset_id", sort=False)["close"]
    df["ema20"] = g.transform(lambda s: _ema(s, 20))
    df["ema50"] = g.transform(lambda s: _ema(s, 50))
    df["rsi14"] = g.transform(_rsi)
    df["macd_hist"] = g.transform(lambda s: _ema(s, 12) - _ema(s, 26) - _ema(_ema(s, 12) - _ema(s, 26), 9))
    df["roc6"] = g.transform(lambda s: s.pct_change(6))
    df["roc30"] = g.transform(lambda s: s.pct_change(30))
    vol = df.groupby("asset_id", sort=False)["volume"]
    df["vol_ratio"] = df["volume"] / vol.transform(lambda s: s.rolling(20, min_periods=5).mean())
    df["ret1"] = g.transform(lambda s: s.pct_change())
    df["atr_pct"] = (
        df.groupby("asset_id", sort=False)["ret1"].transform(lambda s: s.rolling(14, min_periods=5).std()) * 100
    )

    # Trend-Proxy analog zur EMA-Stapel-Logik in app/signals/scoring.py::score_trend
    df["trend_proxy"] = (df["close"] / df["ema20"] - 1) + (df["ema20"] / df["ema50"] - 1)
    df["momentum_proxy"] = (df["rsi14"] - 50) / 50 + df["macd_hist"] / df["close"] * 100
    df["composite_proxy"] = df["trend_proxy"].rank(pct=True) + df["momentum_proxy"].rank(pct=True)

    # Forward Return 6 Bars = 24h, strikt in der Zukunft.
    df["fwd_24h"] = df.groupby("asset_id", sort=False)["close"].shift(-6) / df["close"] - 1
    df["fwd_xs"] = df["fwd_24h"] - df.groupby("open_time")["fwd_24h"].transform("mean")

    features = ["trend_proxy", "momentum_proxy", "rsi14", "macd_hist", "roc6", "roc30", "vol_ratio", "atr_pct", "composite_proxy"]
    work = df.dropna(subset=["fwd_xs"]).copy()
    # Cluster = Kerzen-Zeitstempel (alle Coins eines Zeitpunkts sind korreliert)
    work["batch"] = pd.factorize(work["open_time"])[0]

    results = []
    for feat in features:
        sub = work[[feat, "fwd_xs", "batch"]].dropna()
        if len(sub) < 500:
            continue
        x = ranks(sub[feat].to_numpy(dtype=float))
        y = ranks(sub["fwd_xs"].to_numpy(dtype=float))
        ic = pearson(x, y)
        cl = bootstrap_corr(x, y, sub["batch"].to_numpy(), n_boot=2000, rng=rng)
        results.append(
            {
                "feature": feat,
                "n": int(len(sub)),
                "n_clusters": int(sub["batch"].nunique()),
                "ic": float(ic),
                "ci_low": cl["ci_low"],
                "ci_high": cl["ci_high"],
                "p_cluster": cl["p_two_sided"],
                "significant": bool(np.isfinite(cl["ci_low"]) and cl["ci_low"] * cl["ci_high"] > 0),
            }
        )

    # Stabilitaet: IC je Monat fuer jedes Feature. Ein Feature mit echtem Edge
    # muss ueber Monate hinweg das Vorzeichen behalten.
    monthly = []
    work["month"] = work["open_time"].dt.tz_localize(None).dt.to_period("M").astype(str)
    for feat in features:
        per_month = []
        for month, sub in work.groupby("month"):
            s = sub[[feat, "fwd_xs"]].dropna()
            if len(s) < 300:
                continue
            per_month.append({"month": month, "n": int(len(s)), "ic": float(spearman(s[feat].to_numpy(), s["fwd_xs"].to_numpy()))})
        if per_month:
            vals = np.array([m["ic"] for m in per_month])
            monthly.append(
                {
                    "feature": feat,
                    "months": len(vals),
                    "mean_ic": float(vals.mean()),
                    "share_same_sign_as_mean": float((np.sign(vals) == np.sign(vals.mean())).mean()),
                    "min_ic": float(vals.min()),
                    "max_ic": float(vals.max()),
                    "series": per_month,
                }
            )

    return {
        "available": True,
        "assets": int(work["asset_id"].nunique()),
        "bars": int(len(work)),
        "period_start": str(work["open_time"].min()),
        "period_end": str(work["open_time"].max()),
        "features": results,
        "monthly_ic": monthly,
    }


# ---------------------------------------------------------------------------
# Orchestrierung
# ---------------------------------------------------------------------------


def run(data_dir: Path, out_dir: Path) -> dict:
    rng = np.random.default_rng(RNG_SEED)
    signals, components, candles = load_data(data_dir)
    panel = build_panel(signals, components, candles)

    coverage = {
        "signals_total": int(len(signals)),
        "signal_assets": int(signals["asset_id"].nunique()),
        "assets_with_1h_candles": int(candles["asset_id"].nunique()),
        "window_start": str(signals["created_at"].min()),
        "window_end": str(signals["created_at"].max()),
        "scan_batches": int(panel["batch"].nunique()),
        "candle_end": str(candles["open_time"].max()),
        "usable": {f"{h}h": int(panel[f"ret_{h}h"].notna().sum()) for h in HORIZONS_H},
        "usable_clusters": {f"{h}h": int(panel.loc[panel[f"ret_{h}h"].notna(), "batch"].nunique()) for h in HORIZONS_H},
        "direction_counts": signals["direction"].value_counts().to_dict(),
        "score_quantiles": {
            q: float(signals["score"].quantile(q)) for q in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0)
        },
    }

    features = ["score", "score_rank"] + [f"c_{c}" for c in CATEGORIES] + [f"rank_{c}" for c in CATEGORIES]
    features = [f for f in features if f in panel.columns]

    ics: list[dict] = []
    for h in HORIZONS_H:
        for target, tlabel in ((f"ret_{h}h", "raw"), (f"xs_{h}h", "xs")):
            for feat in features:
                res = information_coefficient(panel, feat, target, f"{h}h", rng)
                if res:
                    d = res.to_dict()
                    d["target_type"] = tlabel
                    ics.append(d)

    deciles = {}
    for h in HORIZONS_H:
        for feat in ("score", "score_rank"):
            for target, tlabel in ((f"ret_{h}h", "raw"), (f"xs_{h}h", "xs")):
                key = f"{feat}|{tlabel}|{h}h"
                tbl = decile_table(panel, feat, target, rng)
                if tbl:
                    deciles[key] = tbl
    for cat in CATEGORIES:
        col = f"c_{cat}"
        if col in panel.columns:
            tbl = decile_table(panel, col, "xs_24h", rng)
            if tbl:
                deciles[f"{col}|xs|24h"] = tbl

    directions = {f"{h}h": direction_stats(panel, h) for h in HORIZONS_H}

    ic_series = {}
    for h in HORIZONS_H:
        for feat in ["score"] + [f"c_{c}" for c in CATEGORIES]:
            if feat in panel.columns:
                ic_series[f"{feat}|xs|{h}h"] = ic_by_batch(panel, feat, f"xs_{h}h", h)
    top_decile = {f"{h}h": top_decile_by_batch(panel, h) for h in HORIZONS_H}

    btc_rows = signals[signals["symbol"].str.upper().str.startswith("BTC")]
    btc_asset_id = int(btc_rows["asset_id"].iloc[0]) if not btc_rows.empty else None
    regimes = regime_split(panel, candles, btc_asset_id, rng)

    # Robustheit: gleiche IC mit reference_price als Einstieg
    robustness = []
    for h in HORIZONS_H:
        res = information_coefficient(panel, "score", f"retref_{h}h", f"{h}h", rng)
        if res:
            d = res.to_dict()
            d["target_type"] = "reference_price_entry"
            robustness.append(d)

    candles_4h = pd.read_csv(data_dir / "edge_candles_4h.csv", parse_dates=["open_time"])
    proxy = long_window_proxy(candles_4h, rng)

    payload = {
        "meta": {
            "generated_for": "DATAMIND",
            "question": "Hat der Signal-Score Vorhersagekraft fuer zukuenftige Renditen?",
            "method": {
                "entry": "Close der letzten zum Signalzeitpunkt geschlossenen 1h-Kerze (kein Look-ahead)",
                "horizons_hours": HORIZONS_H,
                "targets": {
                    "raw": "einfache Forward-Rendite",
                    "xs": "pro Scan-Batch quer-schnittlich zentriert (markt-neutral)",
                },
                "bootstrap": f"{N_BOOT} Resamples, Cluster-Bootstrap ueber Scan-Batches",
                "significance": "CI schliesst die Null nicht ein (Cluster-Bootstrap)",
            },
        },
        "coverage": coverage,
        "information_coefficients": ics,
        "ic_by_batch": ic_series,
        "top_decile_by_batch": top_decile,
        "deciles": deciles,
        "directions": directions,
        "regimes": regimes,
        "robustness_reference_price": robustness,
        "long_window_proxy_4h": proxy,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "score_edge_analysis.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    (out_dir / "score_edge_analysis.txt").write_text(render_text(payload), encoding="utf-8")
    return payload


def _fmt_pct(x: float | None) -> str:
    if x is None or not np.isfinite(x):
        return "  n/a "
    return f"{x * 100:+6.3f}%"


def render_text(p: dict) -> str:
    lines: list[str] = []
    add = lines.append
    cov = p["coverage"]
    add("=" * 78)
    add("SCORE-EDGE-ANALYSE  --  Hat der Score Vorhersagekraft?")
    add("=" * 78)
    add(f"Fenster        : {cov['window_start']}  bis  {cov['window_end']}")
    add(f"Signale        : {cov['signals_total']} auf {cov['signal_assets']} Assets")
    add(f"1h-Kerzen fuer : {cov['assets_with_1h_candles']} Assets (Kerzen bis {cov['candle_end']})")
    add(f"Scan-Batches   : {cov['scan_batches']}")
    add(f"Nutzbar        : {cov['usable']}  (Cluster: {cov['usable_clusters']})")
    add(f"Score-Quantile : {cov['score_quantiles']}")
    add("")

    add("-" * 78)
    add("INFORMATION COEFFICIENT (Spearman, Score vs. Forward Return)")
    add("-" * 78)
    add(f"{'Feature':<22}{'Hor':>5}{'Ziel':>6}{'n':>7}{'Cl':>4}{'IC':>8}{'CI-Cluster':>22}{'p_cl':>8}  sig")
    for row in p["information_coefficients"]:
        add(
            f"{row['feature']:<22}{row['horizon']:>5}{row['target_type']:>6}{row['n']:>7}{row['n_clusters']:>4}"
            f"{row['ic']:>8.4f}   [{row['ci_cluster_low']:+.4f}, {row['ci_cluster_high']:+.4f}]"
            f"{row['p_cluster']:>8.3f}  {'JA' if row['significant'] else '--'}"
        )
    add("")

    add("-" * 78)
    add("IC-ZEITREIHE JE SCAN (ehrlichster Test bei ueberlappenden Fenstern)")
    add("-" * 78)
    add(f"{'Feature|Ziel|Hor':<28}{'Scans':>7}{'unabh.':>8}{'mean IC':>10}{'sd':>8}{'t':>8}{'>0':>7}")
    for key, r in p["ic_by_batch"].items():
        if not r.get("n_batches"):
            continue
        add(
            f"{key:<28}{r['n_batches']:>7}{r['n_non_overlapping']:>8}{r['mean_ic']:>10.4f}"
            f"{r['std_ic']:>8.3f}{r['t_stat_naive']:>8.2f}{r['share_positive']:>7.2f}"
        )
    add("")
    add("-" * 78)
    add("TOP-DEZIL MINUS BOTTOM-DEZIL JE SCAN (xs-Rendite)")
    add("-" * 78)
    for hor, r in p["top_decile_by_batch"].items():
        add(
            f"  {hor}: Scans={r['n_batches']}  Mittel={_fmt_pct(r['mean_spread'])}  Median={_fmt_pct(r['median_spread'])}"
            f"  Anteil negativ={r['share_negative']:.2f}  schlechtester={_fmt_pct(r['worst_batch'])}"
            f"  bester={_fmt_pct(r['best_batch'])}"
        )
    add("")

    add("-" * 78)
    add("DEZILE (Score -> mittlere Forward-Rendite)")
    add("-" * 78)
    for key, tbl in p["deciles"].items():
        add(f"\n[{key}]  n={tbl['n']}  Cluster={tbl['n_clusters']}  Monotonie(Spearman)={tbl['monotonicity_spearman']:+.3f}")
        add(f"  {'Dez':<5}{'Range':<20}{'n':>6}{'Mittel':>10}{'Median':>10}{'CI (Cluster)':>24}")
        for b in tbl["buckets"]:
            rng_s = f"{b['feature_min']:.2f}..{b['feature_max']:.2f}"
            add(
                f"  {b['bucket']:<5}{rng_s:<20}{b['n']:>6}{_fmt_pct(b['mean_ret']):>10}{_fmt_pct(b['median_ret']):>10}"
                f"   [{_fmt_pct(b['ci_low'])}, {_fmt_pct(b['ci_high'])}]"
            )
        tmb = tbl["top_minus_bottom"]
        add(
            f"  Top-Bottom: {_fmt_pct(tmb['value'])}  CI [{_fmt_pct(tmb['ci_low'])}, {_fmt_pct(tmb['ci_high'])}]"
            f"  p={tmb['p_cluster']:.3f}  {'SIGNIFIKANT' if tmb['significant'] else 'im Rauschen'}"
        )
    add("")

    add("-" * 78)
    add("RICHTUNG (verdient die Richtungsentscheidung Geld?)")
    add("-" * 78)
    for hor, rows in p["directions"].items():
        add(f"\nHorizont {hor}")
        for r in rows:
            add(
                f"  {r['group']:<28} n={r['n']:>5}  roh={_fmt_pct(r['mean_raw_ret'])}"
                f"  signiert={_fmt_pct(r['mean_signed_ret'])}  signiert_xs={_fmt_pct(r['mean_signed_xs_ret'])}"
                f"  Trefferquote={r['hit_rate_signed']:.3f}"
            )
    add("")

    add("-" * 78)
    add("REGIME")
    add("-" * 78)
    add(json.dumps(p["regimes"], indent=2, ensure_ascii=False, default=str))
    add("")

    add("-" * 78)
    add("LANGFENSTER-PROXY (4h-Kerzen, ~100 Tage) -- NICHT der Produktions-Score")
    add("-" * 78)
    prx = p["long_window_proxy_4h"]
    if prx.get("available"):
        add(f"Assets={prx['assets']}  Bars={prx['bars']}  {prx['period_start']} .. {prx['period_end']}")
        add(f"  {'Feature':<20}{'n':>8}{'Cl':>6}{'IC':>9}{'CI':>24}{'p':>8}  sig")
        for f in prx["features"]:
            add(
                f"  {f['feature']:<20}{f['n']:>8}{f['n_clusters']:>6}{f['ic']:>9.4f}"
                f"   [{f['ci_low']:+.4f}, {f['ci_high']:+.4f}]{f['p_cluster']:>8.3f}  {'JA' if f['significant'] else '--'}"
            )
        add("")
        add("  Monatsstabilitaet (IC je Monat):")
        for m in prx["monthly_ic"]:
            add(
                f"    {m['feature']:<18} Monate={m['months']:>3}  mean={m['mean_ic']:+.4f}"
                f"  gleiches Vorzeichen={m['share_same_sign_as_mean']:.2f}"
                f"  Spanne=[{m['min_ic']:+.3f}, {m['max_ic']:+.3f}]"
            )
    else:
        add(f"nicht verfuegbar: {prx.get('reason')}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--fetch", action="store_true", help="Daten vorher frisch vom VPS exportieren")
    args = ap.parse_args(argv)

    if args.fetch:
        fetch_from_vps(args.data_dir)
    payload = run(args.data_dir, args.out_dir)
    print(render_text(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
