"""Regeneriert historische Signal-Scores offline ueber die tiefe Kerzenhistorie.

Warum es dieses Skript gibt
---------------------------
Die Tabelle ``signals`` deckt nur wenige Tage ab. Jede Aussage darueber, ob der
Score Vorhersagekraft hat, war damit an eine einzelne Marktbewegung gebunden.
Die Kerzenhistorie reicht dagegen Monate zurueck. Dieses Skript schliesst die
Luecke: es laesst den **echten Produktions-Score** ueber die historischen Kerzen
laufen und erzeugt so ein Panel aus zehntausenden Beobachtungen.

Es wird kein Scoring nachgebaut. Verwendet werden exakt dieselben Bausteine wie
im Live-Betrieb:

* ``app.indicators.engine.IndicatorEngine.compute`` je Timeframe,
* ``app.signals.engine.SignalEngine.generate`` fuer Score, Richtung und Risiko,
* ``app.strategies.weights.DEFAULT_WEIGHTS`` (identisch mit der in der DB
  aktiven Strategieversion 1).

Look-ahead-Freiheit
-------------------
Zu einem Scan-Zeitpunkt ``S`` gilt eine Kerze nur dann als bekannt, wenn sie
bereits **geschlossen** ist, also ``open_time + Timeframe-Dauer <= S``. Damit
kann in die Indikatoren keine Information aus der Zukunft einfliessen. Der
``reference_price`` des Signals ist der Schlusskurs der letzten geschlossenen
Kerze — genau der Preis, zu dem man haette handeln koennen.

Bekannte Abweichung zum Live-Betrieb
------------------------------------
Live werden vier Timeframes analysiert (``15m,1h,4h,1d``). Fuer ``15m`` gibt es
nur wenige Tage Historie, deshalb laeuft die Regeneration ohne ``15m``. Die
Rollengewichte werden dadurch auf die vorhandenen Timeframes normalisiert
(siehe ``assess_timeframes``). ``--timeframes`` erlaubt es, diese Abweichung im
Ueberlappungsfenster gegen die Live-Signale zu messen.

Aufruf
------
    # Live-Parity: Setup-Timeframe 1h, volle 1h-Tiefe (~6 Monate)
    python scripts/regenerate_historical_signals.py --preset live

    # Langfenster: Setup-Timeframe 4h, volle 4h-Tiefe (~13 Monate)
    python scripts/regenerate_historical_signals.py --preset long

    # Gegenprobe mit dem Scoring VOR Phase 1 (Commit 6df2f4a)
    python scripts/regenerate_historical_signals.py --preset live --scoring prefix
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.enums import ScoreCategory, StructureState  # noqa: E402
from app.core.errors import InsufficientDataError  # noqa: E402
from app.core.time import TIMEFRAME_MINUTES  # noqa: E402
from app.indicators.engine import IndicatorEngine, IndicatorSet  # noqa: E402
from app.signals.engine import SignalEngine, SignalEngineConfig  # noqa: E402
from app.signals.risk import RiskConfig, RiskManager  # noqa: E402
from app.strategies.weights import DEFAULT_WEIGHTS  # noqa: E402

DATA_DIR = REPO_ROOT / "exports" / "edge_data"
OUT_DIR = REPO_ROOT / "exports" / "edge_data"

#: Live-Werte vom VPS (.env, Commit 6eabbfe) — bewusst hart hinterlegt, damit die
#: Regeneration nicht von der lokalen Umgebung abhaengt. Per CLI ueberschreibbar.
LIVE_GATES = {
    "min_risk_reward_ratio": 2.0,
    "max_atr_percent": 12.0,
    "min_adx": 20.0,
    "rsi_long_max": 75.0,
    "rsi_short_min": 25.0,
    "block_range_market": True,
    "expiry_multiplier": 24,
    "atr_multiplier": 1.5,
    "max_risk_percent": 1.0,
    "min_stop_distance_percent": 0.3,
    "max_stop_distance_percent": 8.0,
    "reject_wide_stops": False,
}

#: ``CANDLE_LIMIT`` im Live-Betrieb. Relevant, weil der Supertrend pfadabhaengig
#: ist: eine andere Fensterlaenge ergibt einen anderen Supertrend.
LIVE_CANDLE_LIMIT = 500
LIVE_MIN_CANDLES = 210

PRESETS: dict[str, dict[str, Any]] = {
    "live": {
        "timeframes": ["1h", "4h", "1d"],
        "primary": "1h",
        "step_hours": 4,
        "note": "Setup-Timeframe 1h wie live, ohne 15m (keine Historie)",
    },
    "long": {
        "timeframes": ["4h", "1d"],
        "primary": "4h",
        "step_hours": 12,
        "note": "Setup-Timeframe 4h fuer maximale Historientiefe",
    },
    "live_with_15m": {
        "timeframes": ["15m", "1h", "4h", "1d"],
        "primary": "1h",
        "step_hours": 4,
        "note": "Exakte Live-Timeframes — nur im kurzen 15m-Fenster nutzbar",
    },
}

CATEGORY_COLUMN = {
    ScoreCategory.TREND: "c_trend",
    ScoreCategory.MOMENTUM: "c_momentum",
    ScoreCategory.VOLUME: "c_volume",
    ScoreCategory.VOLATILITY: "c_volatility",
    ScoreCategory.MARKET_STRUCTURE: "c_market_structure",
    ScoreCategory.MULTI_TIMEFRAME: "c_multi_timeframe",
    ScoreCategory.RISK_REWARD: "c_risk_reward",
    ScoreCategory.SENTIMENT: "c_sentiment",
}

_STRUCTURE_CODE = {
    StructureState.HH_HL: 1,
    StructureState.LH_LL: -1,
}


# ---------------------------------------------------------------------------
# Scoring-Variante vor Phase 1 (Commit 6df2f4a)
# ---------------------------------------------------------------------------


def _score_volatility_prefix(indicators: IndicatorSet) -> tuple[float, str]:
    """``score_volatility`` in der Fassung VOR Commit 6df2f4a.

    Unterschied: die Abzuege fuer ATR ausserhalb des Zielbandes waren
    richtungslos (nicht mit ``trend_sign`` multipliziert). Genau das erzeugte den
    systematischen Baerenbias — in einem gerichteten Score ist ein
    richtungsloser Abzug immer ein Schub Richtung SHORT.
    """
    from app.signals import scoring as _s

    score = 0.0
    notes: list[str] = []
    trend_sign = (
        1.0
        if indicators.trend_direction.value == "BULLISH"
        else (-1.0 if indicators.trend_direction.value == "BEARISH" else 0.0)
    )

    if indicators.atr_percent is not None:
        atr_pct = indicators.atr_percent
        if _s.ATR_IDEAL_MIN <= atr_pct <= _s.ATR_IDEAL_MAX:
            score += 50.0 * trend_sign
            notes.append(f"ATR {atr_pct:.2f}% im gut handelbaren Bereich")
        elif atr_pct > _s.ATR_IDEAL_MAX:
            score -= 40.0
            notes.append(f"ATR {atr_pct:.2f}% erhoeht (weite Stops erforderlich)")
        else:
            score -= 15.0
            notes.append(f"ATR {atr_pct:.2f}% sehr niedrig (wenig Bewegung)")

    if (
        indicators.bb_width is not None
        and indicators.bb_width_average is not None
        and indicators.bb_width_average > 0
    ):
        relative = indicators.bb_width / indicators.bb_width_average
        if relative < 0.7:
            notes.append("Bollinger-Squeeze (Ausbruch moeglich)")
        elif relative > 1.5:
            score += 25.0 * trend_sign
            notes.append("Bollinger-Baender expandieren (Bewegung laeuft)")

    return max(-100.0, min(100.0, score)), "; ".join(notes) or "Keine Volatilitaetsdaten verfuegbar"


def _install_prefix_scoring() -> None:
    """Pre-Phase-1-Volatilitaet einhaengen.

    ``multi_timeframe`` importiert ``score_volatility`` direkt in den eigenen
    Namensraum, deshalb muessen beide Stellen ersetzt werden.
    """
    from app.signals import multi_timeframe as _mtf
    from app.signals import scoring as _s

    _s.score_volatility = _score_volatility_prefix  # type: ignore[assignment]
    _mtf.score_volatility = _score_volatility_prefix  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Datenzugriff
# ---------------------------------------------------------------------------


def _read_candles(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(
        path,
        usecols=["asset_id", "open_time", "open", "high", "low", "close", "volume"],
        parse_dates=["open_time"],
        dtype={
            "asset_id": "int32",
            "open": "float64",
            "high": "float64",
            "low": "float64",
            "close": "float64",
            "volume": "float64",
        },
    )
    frame = frame.sort_values(["asset_id", "open_time"], kind="stable")
    return frame


def load_candle_arrays(
    data_dir: Path, timeframes: Sequence[str]
) -> tuple[dict[str, dict[int, dict[str, np.ndarray]]], dict[str, dict[str, Any]]]:
    """Kerzen je Timeframe als numpy-Arrays pro Asset laden."""
    per_timeframe: dict[str, dict[int, dict[str, np.ndarray]]] = {}
    coverage: dict[str, dict[str, Any]] = {}

    for timeframe in timeframes:
        path = data_dir / f"deep_candles_{timeframe}.csv.gz"
        if not path.exists():
            path = data_dir / f"deep_candles_{timeframe}.csv"
        if not path.exists():
            raise FileNotFoundError(f"Kerzen fuer {timeframe} fehlen: {path}")

        frame = _read_candles(path)
        times = frame["open_time"].to_numpy(dtype="datetime64[ns]").astype("int64")
        coverage[timeframe] = {
            "bars": len(frame),
            "assets": int(frame["asset_id"].nunique()),
            "first_bar": str(frame["open_time"].min()),
            "last_bar": str(frame["open_time"].max()),
        }

        by_asset: dict[int, dict[str, np.ndarray]] = {}
        asset_ids = frame["asset_id"].to_numpy()
        # Der Frame ist nach asset_id sortiert -> Gruppengrenzen per diff finden.
        boundaries = np.flatnonzero(np.diff(asset_ids)) + 1
        starts = np.concatenate([[0], boundaries])
        ends = np.concatenate([boundaries, [len(asset_ids)]])
        opens = frame["open"].to_numpy()
        highs = frame["high"].to_numpy()
        lows = frame["low"].to_numpy()
        closes = frame["close"].to_numpy()
        volumes = frame["volume"].to_numpy()
        for start, end in zip(starts, ends, strict=True):
            by_asset[int(asset_ids[start])] = {
                "t": times[start:end].copy(),
                "o": opens[start:end].copy(),
                "h": highs[start:end].copy(),
                "l": lows[start:end].copy(),
                "c": closes[start:end].copy(),
                "v": volumes[start:end].copy(),
            }
        per_timeframe[timeframe] = by_asset

    return per_timeframe, coverage


def load_assets(data_dir: Path) -> pd.DataFrame:
    path = data_dir / "deep_assets.csv.gz"
    if not path.exists():
        path = data_dir / "deep_assets.csv"
    return pd.read_csv(path)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

_WORKER: dict[str, Any] = {}


def _init_worker(cfg: dict[str, Any]) -> None:
    if cfg["scoring_variant"] == "prefix":
        _install_prefix_scoring()

    engine_config = SignalEngineConfig(
        weights=DEFAULT_WEIGHTS,
        primary_timeframe=cfg["primary"],
        confirmation_timeframe=cfg["confirmation"],
        min_risk_reward_ratio=cfg["gates"]["min_risk_reward_ratio"],
        max_atr_percent=cfg["gates"]["max_atr_percent"],
        expiry_multiplier=int(cfg["gates"]["expiry_multiplier"]),
        enable_sentiment=False,
        block_range_market=bool(cfg["gates"]["block_range_market"]),
        min_adx=cfg["gates"]["min_adx"],
        rsi_long_max=cfg["gates"]["rsi_long_max"],
        rsi_short_min=cfg["gates"]["rsi_short_min"],
    )
    risk_manager = RiskManager(
        RiskConfig(
            atr_multiplier=cfg["gates"]["atr_multiplier"],
            min_risk_reward_ratio=cfg["gates"]["min_risk_reward_ratio"],
            max_risk_percent=cfg["gates"]["max_risk_percent"],
            min_stop_distance_percent=cfg["gates"]["min_stop_distance_percent"],
            max_stop_distance_percent=cfg["gates"]["max_stop_distance_percent"],
            reject_wide_stops=bool(cfg["gates"]["reject_wide_stops"]),
        )
    )
    _WORKER["signal_engine"] = SignalEngine(engine_config, risk_manager=risk_manager)
    _WORKER["indicators"] = IndicatorEngine(min_candles=cfg["min_candles"])
    _WORKER["cfg"] = cfg


def _data_quality(times: np.ndarray, step_ns: int, min_candles: int) -> float:
    """``CandleSeries.data_quality`` nachbilden.

    Die Zahl fehlender Kerzen wird aus der Taktung abgeleitet: erwartet werden
    ``(letzte - erste) / Schrittweite + 1`` Kerzen, vorhanden sind ``len(times)``.
    """
    count = len(times)
    if count == 0:
        return 0.0
    length_factor = min(1.0, count / max(min_candles, 1))
    expected_span = int((times[-1] - times[0]) // step_ns) + 1
    expected = max(count, expected_span)
    gap_factor = count / expected if expected else 1.0
    return round(max(0.0, min(1.0, length_factor * 0.4 + gap_factor * 0.6)) * 100.0, 2)


def _slice_frame(arrays: dict[str, np.ndarray], start: int, end: int) -> pd.DataFrame:
    index = pd.DatetimeIndex(
        arrays["t"][start:end].view("datetime64[ns]"), tz="UTC", name="open_time"
    )
    return pd.DataFrame(
        {
            "open": arrays["o"][start:end],
            "high": arrays["h"][start:end],
            "low": arrays["l"][start:end],
            "close": arrays["c"][start:end],
            "volume": arrays["v"][start:end],
        },
        index=index,
    )


def _process_asset(
    task: tuple[int, str, Any, dict[str, dict[str, np.ndarray]], np.ndarray],
) -> pd.DataFrame:
    asset_id, symbol, market_cap_rank, series, scan_ns = task
    cfg = _WORKER["cfg"]
    indicator_engine: IndicatorEngine = _WORKER["indicators"]
    signal_engine: SignalEngine = _WORKER["signal_engine"]

    requested: list[str] = cfg["timeframes"]
    primary: str = cfg["primary"]
    window: int = cfg["window"]
    min_candles: int = cfg["min_candles"]
    step_ns: dict[str, int] = cfg["timeframe_ns"]

    rows: list[dict[str, Any]] = []
    for ts_ns in scan_ns:
        indicator_sets: dict[str, IndicatorSet] = {}
        qualities: list[float] = []

        for timeframe in requested:
            arrays = series.get(timeframe)
            if arrays is None:
                continue
            # Nur bereits geschlossene Kerzen: open_time + Dauer <= Scan-Zeitpunkt.
            cutoff = ts_ns - step_ns[timeframe]
            end = int(np.searchsorted(arrays["t"], cutoff, side="right"))
            if end < min_candles:
                continue
            start = max(0, end - window)
            frame = _slice_frame(arrays, start, end)
            try:
                indicator_sets[timeframe] = indicator_engine.compute(
                    frame, timeframe, symbol=symbol, strict=True
                )
            except InsufficientDataError:
                continue
            qualities.append(_data_quality(arrays["t"][start:end], step_ns[timeframe], min_candles))

        # Ohne Setup-Timeframe kein vergleichbares Signal: das Panel bliebe sonst
        # inhomogen (unterschiedliche Setup-Horizonte in derselben Zeile).
        if primary not in indicator_sets:
            continue

        from app.signals.data_quality import compute_analysis_data_quality

        data_quality = compute_analysis_data_quality(
            qualities,
            indicator_sets=indicator_sets,
            primary_timeframe=primary,
        )

        timestamp = _from_ns(int(ts_ns)).to_pydatetime()
        result = signal_engine.generate(
            symbol,
            indicator_sets,
            data_quality=data_quality,
            now=timestamp,
        )
        rows.append(
            _result_row(asset_id, symbol, market_cap_rank, ts_ns, result, indicator_sets, primary)
        )

    # Als DataFrame zurueckgeben: eine Liste aus hunderttausenden dicts im
    # Elternprozess zu halten kostet ein Vielfaches an Speicher.
    return pd.DataFrame(rows)


def _result_row(
    asset_id: int,
    symbol: str,
    market_cap_rank: Any,
    ts_ns: int,
    result: Any,
    indicator_sets: dict[str, IndicatorSet],
    primary: str,
) -> dict[str, Any]:
    primary_indicators = indicator_sets[primary]
    agreement = next(
        (
            c.raw_score / 100.0
            for c in result.components
            if c.category == ScoreCategory.MULTI_TIMEFRAME
        ),
        0.0,
    )
    row: dict[str, Any] = {
        "asset_id": asset_id,
        "symbol": symbol,
        "market_cap_rank": market_cap_rank,
        "ts": ts_ns,
        "score": result.score,
        # Richtung vor den harten Ausschlusskriterien: fuer die Score-Analyse
        # relevant, weil NO_TRADE die Richtung ueberschreibt.
        "direction_raw": SignalEngine._determine_direction(result.score, agreement).value,
        "direction": result.direction.value,
        "confidence": result.confidence.value,
        "market_phase": result.market_phase.value,
        "reference_price": result.reference_price,
        "data_quality": result.data_quality,
        "no_trade_reason": result.no_trade_reason or "",
        "risk_reward_ratio": (
            float(result.risk.risk_reward_ratio) if result.risk is not None else float("nan")
        ),
        "n_timeframes": len(indicator_sets),
        "agreement": agreement,
    }
    for component in result.components:
        column = CATEGORY_COLUMN.get(component.category)
        if column:
            row[column] = component.raw_score

    # Risiko-Levels und Marktstruktur mitschreiben. Ohne sie laesst sich ein
    # Exit-Replay nicht rekonstruieren: der Stop haengt am naechsten Support
    # bzw. Widerstand, nicht nur am ATR-Abstand.
    risk = result.risk
    row.update(
        {
            "atr_value": primary_indicators.atr_14,
            "nearest_support": primary_indicators.structure.nearest_support,
            "nearest_resistance": primary_indicators.structure.nearest_resistance,
            "entry_low": float(risk.entry_low) if risk is not None else None,
            "entry_high": float(risk.entry_high) if risk is not None else None,
            "stop_loss": float(risk.stop_loss) if risk is not None else None,
            "take_profit_1": float(risk.take_profit_1) if risk is not None else None,
            "take_profit_2": float(risk.take_profit_2) if risk is not None else None,
            "take_profit_3": float(risk.take_profit_3) if risk is not None else None,
            "stop_distance_percent": (
                float(risk.stop_distance_percent) if risk is not None else None
            ),
            "expires_at": (
                int(pd.Timestamp(result.expires_at).tz_convert("UTC").as_unit("ns").value)
                if result.expires_at is not None
                else None
            ),
        }
    )

    row.update(
        {
            "atr_percent": primary_indicators.atr_percent,
            "adx_14": primary_indicators.adx_14,
            "rsi_14": primary_indicators.rsi_14,
            "roc_14": primary_indicators.roc_14,
            "volume_ratio": primary_indicators.volume_ratio,
            "obv_slope": primary_indicators.obv_slope,
            "trend_strength": primary_indicators.trend_strength,
            "trend_direction": primary_indicators.trend_direction.value,
            "structure_state": _STRUCTURE_CODE.get(primary_indicators.structure.state, 0),
            "bb_width_rel": (
                primary_indicators.bb_width / primary_indicators.bb_width_average
                if primary_indicators.bb_width is not None and primary_indicators.bb_width_average
                else None
            ),
            "macd_hist_norm": (
                primary_indicators.macd_histogram / primary_indicators.close_price * 100.0
                if primary_indicators.macd_histogram is not None and primary_indicators.close_price
                else None
            ),
            "completeness": primary_indicators.completeness,
        }
    )
    return row


# ---------------------------------------------------------------------------
# Orchestrierung
# ---------------------------------------------------------------------------


def _to_ns(value: pd.Timestamp) -> int:
    """Zeitstempel als Nanosekunden seit Epoche.

    Explizit, weil pandas 3 fuer neue Zeitreihen standardmaessig
    Mikrosekunden-Aufloesung verwendet — ``asi8`` waere dann nicht in ns.
    """
    return int(pd.Timestamp(value).tz_convert("UTC").as_unit("ns").value)


def _from_ns(value: int) -> pd.Timestamp:
    return pd.Timestamp(np.datetime64(int(value), "ns")).tz_localize("UTC")


def build_scan_grid(start: pd.Timestamp, end: pd.Timestamp, step_hours: int) -> np.ndarray:
    grid = pd.date_range(start=start, end=end, freq=f"{step_hours}h", tz="UTC")
    naive = grid.tz_convert("UTC").tz_localize(None).to_numpy(dtype="datetime64[ns]")
    return naive.astype("int64")


def _default_window_start(
    per_timeframe: dict[str, dict[int, dict[str, np.ndarray]]],
    timeframes: Sequence[str],
    primary: str,
    min_candles: int,
) -> pd.Timestamp:
    """Frueheste Zeit, ab der der Setup-Timeframe genug Aufwaermkerzen hat."""
    starts: list[int] = []
    step_ns = int(TIMEFRAME_MINUTES[primary]) * 60 * 1_000_000_000
    for arrays in per_timeframe[primary].values():
        if len(arrays["t"]) > min_candles:
            starts.append(int(arrays["t"][min_candles - 1]) + step_ns)
    if not starts:
        raise RuntimeError(f"Kein Asset mit >= {min_candles} {primary}-Kerzen")
    return _from_ns(int(np.median(starts))).ceil("h")


def run(args: argparse.Namespace) -> dict[str, Any]:
    preset = PRESETS[args.preset]
    timeframes: list[str] = (
        args.timeframes.split(",") if args.timeframes else list(preset["timeframes"])
    )
    primary: str = args.primary or str(preset["primary"])
    step_hours: int = args.step_hours or int(preset["step_hours"])
    if primary not in timeframes:
        raise SystemExit(f"Setup-Timeframe {primary} ist nicht in {timeframes}")

    print(f"Lade Kerzen fuer {timeframes} ...", file=sys.stderr, flush=True)
    t0 = time.time()
    per_timeframe, coverage = load_candle_arrays(args.data_dir, timeframes)
    assets = load_assets(args.data_dir)
    print(f"  geladen in {time.time() - t0:.1f}s: {coverage}", file=sys.stderr, flush=True)

    start = (
        pd.Timestamp(args.start, tz="UTC")
        if args.start
        else _default_window_start(per_timeframe, timeframes, primary, args.min_candles)
    )
    end = (
        pd.Timestamp(args.end, tz="UTC")
        if args.end
        else _from_ns(max(int(a["t"][-1]) for a in per_timeframe[primary].values())).floor("h")
    )
    scan_ns = build_scan_grid(start, end, step_hours)
    print(
        f"Scan-Raster: {start} .. {end}  Schritt {step_hours}h  -> {len(scan_ns)} Zeitpunkte",
        file=sys.stderr,
        flush=True,
    )

    symbol_of = dict(zip(assets["asset_id"], assets["symbol"], strict=True))
    rank_of = dict(zip(assets["asset_id"], assets["market_cap_rank"], strict=True))

    asset_ids = sorted(per_timeframe[primary].keys())
    if args.limit_assets:
        asset_ids = asset_ids[: args.limit_assets]

    cfg = {
        "timeframes": timeframes,
        "primary": primary,
        "confirmation": "4h" if "4h" in timeframes and primary != "4h" else "1d",
        "window": args.window,
        "min_candles": args.min_candles,
        "gates": dict(LIVE_GATES),
        "scoring_variant": args.scoring,
        "timeframe_ns": {tf: int(TIMEFRAME_MINUTES[tf]) * 60 * 1_000_000_000 for tf in timeframes},
    }

    tasks = []
    for asset_id in asset_ids:
        series = {
            tf: per_timeframe[tf][asset_id] for tf in timeframes if asset_id in per_timeframe[tf]
        }
        if primary not in series:
            continue
        tasks.append(
            (
                int(asset_id),
                str(symbol_of.get(asset_id, f"ASSET{asset_id}")).upper(),
                rank_of.get(asset_id),
                series,
                scan_ns,
            )
        )

    print(
        f"Regeneriere Signale fuer {len(tasks)} Assets mit {args.workers} Prozessen "
        f"(Scoring: {args.scoring}) ...",
        file=sys.stderr,
        flush=True,
    )

    parts: list[pd.DataFrame] = []
    produced = 0
    t0 = time.time()
    if args.workers <= 1:
        _init_worker(cfg)
        for done, task in enumerate(tasks, start=1):
            part = _process_asset(task)
            produced += len(part)
            parts.append(part)
            if done % 10 == 0 or done == len(tasks):
                _progress(done, len(tasks), produced, t0)
    else:
        with ProcessPoolExecutor(
            max_workers=args.workers, initializer=_init_worker, initargs=(cfg,)
        ) as pool:
            for done, part in enumerate(pool.map(_process_asset, tasks, chunksize=1), start=1):
                produced += len(part)
                parts.append(part)
                if done % 10 == 0 or done == len(tasks):
                    _progress(done, len(tasks), produced, t0)

    parts = [p for p in parts if not p.empty]
    panel = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if panel.empty:
        raise RuntimeError("Keine Signale regeneriert — Fenster oder Kerzenabdeckung pruefen")
    panel["ts"] = pd.to_datetime(panel["ts"].astype("int64"), unit="ns", utc=True)
    if "expires_at" in panel.columns:
        panel["expires_at"] = pd.to_datetime(panel["expires_at"], unit="ns", utc=True)
    panel = panel.sort_values(["ts", "asset_id"], kind="stable")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.out_dir / f"regen_signals_{args.tag}.csv.gz"
    panel.to_csv(out_path, index=False, compression="gzip")

    meta = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "preset": args.preset,
        "tag": args.tag,
        "scoring_variant": args.scoring,
        "timeframes": timeframes,
        "primary_timeframe": primary,
        "confirmation_timeframe": cfg["confirmation"],
        "step_hours": step_hours,
        "window_candles": args.window,
        "min_candles": args.min_candles,
        "gates": LIVE_GATES,
        "weights": {k.value: v for k, v in DEFAULT_WEIGHTS.as_dict().items()},
        "scan_start": str(start),
        "scan_end": str(end),
        "scan_points": len(scan_ns),
        "assets": int(panel["asset_id"].nunique()),
        "rows": len(panel),
        "candle_coverage": coverage,
        "direction_counts": panel["direction"].value_counts().to_dict(),
        "direction_raw_counts": panel["direction_raw"].value_counts().to_dict(),
        "score_quantiles": {
            str(q): float(panel["score"].quantile(q)) for q in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0)
        },
        "runtime_seconds": round(time.time() - t0, 1),
        "output": str(out_path),
    }
    meta_path = args.out_dir / f"regen_signals_{args.tag}.meta.json"
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )

    print(json.dumps(meta, indent=2, ensure_ascii=False, default=str))
    return meta


def _progress(done: int, total: int, rows: int, t0: float) -> None:
    elapsed = time.time() - t0
    rate = done / elapsed if elapsed > 0 else 0.0
    remaining = (total - done) / rate if rate > 0 else float("nan")
    print(
        f"  [{done}/{total}] Zeilen={rows}  {elapsed:.0f}s  ETA {remaining:.0f}s",
        file=sys.stderr,
        flush=True,
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--preset", choices=sorted(PRESETS), default="live")
    parser.add_argument("--timeframes", default="", help="Komma-Liste, ueberschreibt das Preset")
    parser.add_argument("--primary", default="", help="Setup-Timeframe, ueberschreibt das Preset")
    parser.add_argument("--step-hours", type=int, default=0, help="Abstand der Scan-Zeitpunkte")
    parser.add_argument("--start", default="", help="ISO-Zeitpunkt, Beginn des Scan-Rasters")
    parser.add_argument("--end", default="", help="ISO-Zeitpunkt, Ende des Scan-Rasters")
    parser.add_argument("--window", type=int, default=LIVE_CANDLE_LIMIT)
    parser.add_argument("--min-candles", type=int, default=LIVE_MIN_CANDLES)
    parser.add_argument("--scoring", choices=("current", "prefix"), default="current")
    parser.add_argument("--limit-assets", type=int, default=0)
    parser.add_argument(
        "--workers", type=int, default=max(1, (__import__("os").cpu_count() or 4) - 2)
    )
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--tag", default="", help="Namenszusatz der Ausgabedatei")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.tag:
        args.tag = f"{args.preset}_{args.scoring}"
    return 0 if run(args) else 1


if __name__ == "__main__":
    raise SystemExit(main())
