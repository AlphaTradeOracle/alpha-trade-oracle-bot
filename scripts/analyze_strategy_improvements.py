"""Counterfactual sweeps on the 6M current-strategy backtest."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.backtest_current_strategy as m
from scripts.regenerate_historical_signals import load_assets

OUT = REPO_ROOT / "exports" / "strategy_improvements.json"
CANVAS = Path(
    r"C:\Users\Admin\.cursor\projects\c-Users-Admin-Projects-alpha-trade-oracle-bot"
    r"\canvases\strategy-profit-levers.canvas.tsx"
)


def kpi_line(name: str, k: dict) -> str:
    return (
        f"{name:28} n={k['closed']:4d} R={k['total_r']:+7.2f} "
        f"E={k['expectancy_r']:+.3f} WR={k['wr']:5.1f} PF={k['pf_r']} DD={k['max_dd_r']}"
    )


def run_mode(cands, s1, regime, mode: str, name: str) -> dict:
    trades, skips = m.run(cands, s1, regime, mode=mode)
    summary = m.summarize(trades, skips, {"mode": name})
    print(kpi_line(name, summary["kpi"]), flush=True)
    return {
        "name": name,
        "kpi": summary["kpi"],
        "by_side": summary["by_side"],
        "exits": summary["exits"],
        "skips": skips,
    }


def run_variant(cands, s1, regime, name: str, **overrides) -> dict:
    saved = {k: getattr(m, k) for k in overrides}
    for k, v in overrides.items():
        setattr(m, k, v)
    try:
        return run_mode(cands, s1, regime, "full", name)
    finally:
        for k, v in saved.items():
            setattr(m, k, v)


def write_canvas(payload: dict) -> None:
    base = payload["base"]["kpi"]
    rows = []
    for item in payload["variants"]:
        k = item["kpi"]
        delta = k["total_r"] - base["total_r"]
        rows.append(
            [
                item["name"],
                str(k["closed"]),
                f"{k['wr']}%",
                f"{k['total_r']:+.2f}R",
                f"{delta:+.2f}R",
                f"{k['expectancy_r']:+.3f}R",
                str(k["pf_r"]),
                f"{k['max_dd_r']}R",
            ]
        )
    recs = payload["recommendations"]
    body = """import {
  Callout,
  Card,
  CardBody,
  CardHeader,
  Grid,
  H1,
  H2,
  Pill,
  Row,
  Stack,
  Stat,
  Table,
  Text,
} from "cursor/canvas";

const BASE = __BASE__ as const;
const VARIANT_ROWS = __VARIANT_ROWS__ as const;
const RECS = __RECS__ as const;
const SIDE = __SIDE__ as const;
const EXITS = __EXITS__ as const;

function toneFor(n: number): "success" | "danger" | undefined {
  if (n > 0) return "success";
  if (n < 0) return "danger";
  return undefined;
}

export default function StrategyProfitLevers() {
  return (
    <Stack gap={20}>
      <Stack gap={6}>
        <H1>Profit Levers from 6M Backtest</H1>
        <Text tone="secondary">
          Counterfactuals on the same regenerated STRONG panel · full live gates as baseline · 2026-01-30 → 2026-07-31
        </Text>
      </Stack>

      <Callout tone="info">
        Baseline full strategy: {BASE.total_r >= 0 ? "+" : ""}
        {BASE.total_r.toFixed(2)}R · E[R] {BASE.expectancy_r >= 0 ? "+" : ""}
        {BASE.expectancy_r.toFixed(3)} · {BASE.closed} trades · PF {BASE.pf_r}. Biggest structural edge: filtered
        shorts (+0.17R) crush longs (+0.02R). Early scratch is a net drag.
      </Callout>

      <Grid columns={{ sm: 2, md: 4 }} gap={12}>
        <Stat
          value={`${BASE.total_r >= 0 ? "+" : ""}${BASE.total_r.toFixed(2)}R`}
          label="Baseline Total R"
          tone={toneFor(BASE.total_r)}
        />
        <Stat
          value={`${SIDE.short.expectancy_r >= 0 ? "+" : ""}${SIDE.short.expectancy_r.toFixed(3)}R`}
          label={`Short E[R] (n=${SIDE.short.n})`}
          tone="success"
        />
        <Stat
          value={`${SIDE.long.expectancy_r >= 0 ? "+" : ""}${SIDE.long.expectancy_r.toFixed(3)}R`}
          label={`Long E[R] (n=${SIDE.long.n})`}
          tone={toneFor(SIDE.long.expectancy_r)}
        />
        <Stat value={`${BASE.max_dd_r}R`} label="Baseline Max DD" tone="danger" />
      </Grid>

      <Stack gap={8}>
        <H2>Where the R comes from (baseline exits)</H2>
        <Row gap={8} wrap>
          {EXITS.map((e) => (
            <Pill key={e.reason} tone={e.r >= 0 ? "success" : "deleted"}>
              {e.reason}: {e.n}× · {e.r >= 0 ? "+" : ""}
              {e.r.toFixed(1)}R
            </Pill>
          ))}
        </Row>
      </Stack>

      <Stack gap={8}>
        <H2>Counterfactual Sweep (delta vs full baseline)</H2>
        <Table
          headers={["Variant", "Closed", "WR", "Total R", "ΔR", "E[R]", "PF", "Max DD"]}
          rows={VARIANT_ROWS}
          striped
          framed
        />
        <Text tone="secondary" size="small">
          Same candidate set and candles · only one lever changed unless noted · Source: analyze_strategy_improvements.py
        </Text>
      </Stack>

      <Stack gap={8}>
        <H2>Recommended moves (ranked)</H2>
        <Table
          headers={["Priority", "Change", "Why", "Expected effect", "Risk"]}
          rows={RECS}
          striped
          framed
        />
      </Stack>

      <Card>
        <CardHeader>Do not do</CardHeader>
        <CardBody>
          <Text size="small">
            Invert the score · raise ADX to 35 · tighten TP to 1/2/3R · remove regime filter · remove portfolio
            caps. Ablation already showed core/no-regime destroys expectancy and doubles drawdown.
          </Text>
        </CardBody>
      </Card>
    </Stack>
  );
}
"""
    repl = {
        "__BASE__": json.dumps(base, indent=2),
        "__VARIANT_ROWS__": json.dumps(rows, indent=2),
        "__RECS__": json.dumps(recs, indent=2),
        "__SIDE__": json.dumps(payload["base"]["by_side"], indent=2),
        "__EXITS__": json.dumps(payload["base"]["exits"], indent=2),
    }
    for key, value in repl.items():
        body = body.replace(key, value)
    CANVAS.parent.mkdir(parents=True, exist_ok=True)
    CANVAS.write_text(body, encoding="utf-8")
    print(f"Wrote {CANVAS}", flush=True)


def main() -> int:
    print("Loading...", flush=True)
    cands, _ = m.load_candidates(m.DEFAULT_SIGNALS)
    s1 = m.load_series(m.DATA_DIR, m.PRIMARY_TF)
    s4 = m.load_series(m.DATA_DIR, m.REGIME_TF)
    assets = load_assets(m.DATA_DIR)
    regime = m.build_btc_regime_map(s4, assets, sorted({c.ts_ns for c in cands}))

    variants: list[dict] = []
    base = run_mode(cands, s1, regime, "full", "full_baseline")
    variants.append(base)

    for name, overrides in [
        ("no_early_scratch", {"EARLY_SCRATCH_H": 0}),
        ("scratch_12h_0.5R", {"EARLY_SCRATCH_H": 12.0, "EARLY_SCRATCH_MFE_R": 0.5}),
        ("scratch_8h_0.3R", {"EARLY_SCRATCH_H": 8.0, "EARLY_SCRATCH_MFE_R": 0.3}),
        ("scratch_12h_0.3R", {"EARLY_SCRATCH_H": 12.0, "EARLY_SCRATCH_MFE_R": 0.3}),
        ("expiry_36h", {"EXPIRY_MULT": 36}),
        ("expiry_48h", {"EXPIRY_MULT": 48}),
        ("retest_1bar", {"RETEST_MIN_BARS": 1}),
        ("retest_pending_x8", {"RETEST_PENDING_MULT": 8}),
        ("retest_near_0.35", {"RETEST_ZONE_NEAR": 0.35}),
        ("max_dir_8", {"MAX_PER_DIR": 8}),
        ("max_open_14", {"MAX_OPEN": 14}),
        ("risk_cap_15pct", {"MAX_PORTFOLIO_RISK_PCT": 15.0}),
    ]:
        variants.append(run_variant(cands, s1, regime, name, **overrides))

    for thr in (78, 80, 82, 85):
        subset = [c for c in cands if (not c.direction.endswith("LONG")) or c.score >= thr]
        variants.append(run_mode(subset, s1, regime, "full", f"long_min_{thr}"))

    for label, pred in [
        ("long_only", lambda c: c.direction.endswith("LONG")),
        ("short_only", lambda c: c.direction.endswith("SHORT")),
    ]:
        subset = [c for c in cands if pred(c)]
        variants.append(run_mode(subset, s1, regime, "full", label))

    # Rank by total R improvement vs baseline
    base_r = base["kpi"]["total_r"]
    ranked = sorted(variants, key=lambda v: v["kpi"]["total_r"], reverse=True)

    recommendations = [
        [
            "1",
            "Early Scratch entschärfen oder aus",
            "88 Scratches = −22R; oft vor späterem Expired/TP-Pfad",
            "Sweep entscheidet — Ziel +ΔR ohne DD-Explosion",
            "Mehr tote Trades / etwas höherer DD möglich",
        ],
        [
            "2",
            "Longs selektiver (Score ≥80–82)",
            f"Long E[R] nur +{base['by_side']['long']['expectancy_r']:.3f} vs Short +{base['by_side']['short']['expectancy_r']:.3f}",
            "Weniger schwache Long-Fills, höheres E[R]",
            "Deutlich weniger Trades",
        ],
        [
            "3",
            "Expiry 24h → 36h testen",
            "Expired-Exits sind netto +63R — Zeit hilft dem Tail",
            "Mehr MFE-Harvest vor Forced Exit",
            "Längere offene Risiken / Overnight",
        ],
        [
            "4",
            "Portfolio-Caps behalten",
            "full schlägt no_portfolio (+21R vs +18R) bei halbem DD",
            "Kapitalqualität > Trade-Count",
            "Gelegenheitkosten bei Cluster-Moves",
        ],
        [
            "5",
            "Regime-Filter behalten",
            "core ohne Regime nur +4.8R / DD −56R",
            "Shorts nur mit Trend-Rückenwind",
            "—",
        ],
    ]

    # Fill recommendation effects from best variants after sweep
    by_name = {v["name"]: v for v in variants}

    def effect(name: str) -> str:
        v = by_name.get(name)
        if not v:
            return "siehe Sweep"
        d = v["kpi"]["total_r"] - base_r
        return f"{v['kpi']['total_r']:+.1f}R (Δ {d:+.1f})"

    # Update first three recommendation expected-effect cells with real numbers if present
    if "no_early_scratch" in by_name:
        recommendations[0][3] = effect("no_early_scratch")
    best_long = None
    for thr in (80, 82, 78, 85):
        key = f"long_min_{thr}"
        if key in by_name and (
            best_long is None or by_name[key]["kpi"]["total_r"] > by_name[best_long]["kpi"]["total_r"]
        ):
            best_long = key
    if best_long:
        recommendations[1][0] = "2"
        recommendations[1][1] = f"Longs selektiver ({best_long})"
        recommendations[1][3] = effect(best_long)
    if "expiry_36h" in by_name:
        recommendations[2][3] = effect("expiry_36h")

    payload = {
        "base": base,
        "variants": variants,
        "ranked": [v["name"] for v in ranked[:10]],
        "recommendations": recommendations,
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}", flush=True)
    write_canvas(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
