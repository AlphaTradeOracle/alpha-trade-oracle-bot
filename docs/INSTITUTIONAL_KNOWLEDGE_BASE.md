# Institutional Trading Knowledge Base

Executable knowledge for Alpha Trade Oracle (Parts 1–9).

This is **mandatory trading logic**, not documentation fluff. Runtime modules:

| Part | Concern | Code |
|------|---------|------|
| 1 | Principles, hierarchy, no-trade gates | `app/knowledge/` |
| 2 | Market Intelligence (before coin) | `MarketRegimeEngine` + `InstitutionalIntelligenceOrchestrator.build_market_intelligence` |
| 3 | Structure / liquidity / context | `app/intelligence/structure_context.py` (+ regime structure) |
| 4 | Market Narrative | `app/intelligence/narrative.py` |
| 5 | Probability / decision / EV | `app/intelligence/probability.py`, `decision.py` |
| 6 | Historical patterns | `app/intelligence/historical.py` (bootstrap until journal DB) |
| 7 | Data quality | `app/intelligence/data_quality.py` |
| 8 | Adaptive performance | `app/intelligence/adaptive.py` (bootstrap) |
| 9 | Decision gap analysis | `app/intelligence/gap.py` |

## Pipeline order

1. **Market Intelligence** (regime, phase, narrative, structure, DQ prior, adaptive prior)
2. Coin candles + indicators + `SignalEngine.generate`
3. Regime score blend
4. **Finalize**: probability, gap, no-trade gates, explainability → `SignalResult.market_context`

## Config

```env
INSTITUTIONAL_KB_ENABLED=true
INSTITUTIONAL_ENFORCE_GATES=false   # soft blend default — gates advisory only
INSTITUTIONAL_MIN_CONFIDENCE_PCT=55
INSTITUTIONAL_MIN_DATA_QUALITY=70
INSTITUTIONAL_REQUIRE_POSITIVE_EV=false
INSTITUTIONAL_REJECT_THIN_LIQUIDITY=false
```

**Soft blend (production default):** Market Regime score blend + `SIGNAL_SHORT_MAX_SCORE`
filter trades; Institutional gates log warnings / explainability but do **not** force
`NO_TRADE`. Set `INSTITUTIONAL_ENFORCE_GATES=true` for hard confidence/EV skips.

## Explainability fields

Persisted under `signals.market_context`:

- `intelligence` — phase, narrative, structure, DQ, adaptive, historical
- `explainability` — trade score, confidence %, EV, gates, gap, NL summary

## Core rules (non-negotiable)

- Maximize EV; never forecast.
- Structure / liquidity > indicators.
- No single-indicator trades.
- Coin analysis never starts before Market Intelligence completes.
- Every decision is scored, confidence-rated, reasoned, and logged.
