import inspect
from app.services import paper_trading_service as m

src = inspect.getsource(m.PaperTradingService._rebuild_from_signal_stream)
print("mode", "two_phase" if "fill_candidates" in src else "signal_order")
print("has_rank_flag", "rank_by_sim_pnl" in src)
