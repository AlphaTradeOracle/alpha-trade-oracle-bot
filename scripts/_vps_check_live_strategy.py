"""Print live strategy flags used by worker for future signals."""
from app.core.config import get_settings

s = get_settings()
print("regime_enabled", s.market_regime_enabled)
print("regime_hard_veto", s.market_regime_hard_veto)
print("regime_filter", s.regime_filter_enabled)
print("ikb_enabled", s.institutional_kb_enabled)
print("ikb_enforce", s.institutional_enforce_gates)
print("short_max", s.signal_short_max_score)
print("short_min", getattr(s, "signal_short_min_score", None))
print("long_min", s.signal_min_score)
print("require_strong", s.signal_require_strong)
print("dispatch", s.telegram_signal_dispatch)
print("paper", s.enable_paper_trading)
