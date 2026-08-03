from app.backtesting.engine import BacktestConfig

fields = sorted(BacktestConfig.__dataclass_fields__)
print("fields", len(fields))
for f in fields:
    if "trend" in f or "retest" in f or "short" in f or "regime" in f:
        print(f)
