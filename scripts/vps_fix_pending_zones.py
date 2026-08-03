"""Rewrite pending retest notes: replace ATR-multiplier zone with price zone."""

from __future__ import annotations

import asyncio
import re
from decimal import Decimal

from sqlalchemy import select

from app.container import build_container
from app.core.enums import SignalDirection
from app.core.logging import configure_logging
from app.core.time import ensure_utc, utc_now
from app.database.session import session_scope
from app.models.paper import PaperPosition
from app.signals.retest_entry import retest_zone, wilder_atr

_ZONE_ATR = re.compile(r"zone=-?\d+(?:\.\d+)?--?\d+(?:\.\d+)?ATR")
_REF = re.compile(r"ref_entry=(?P<v>-?\d+(?:\.\d+)?)")


async def main() -> None:
    configure_logging("WARNING", json_output=False)
    container = build_container()
    provider = container.paper_price_provider
    settings = container.settings
    near = Decimal(str(settings.paper_retest_zone_near))
    far = Decimal(str(settings.paper_retest_zone_far))
    fixed = 0
    try:
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(PaperPosition).where(PaperPosition.status == "pending")
                )
            ).scalars().all()
            for pos in rows:
                notes = str(pos.notes or "")
                if not _ZONE_ATR.search(notes):
                    continue
                ref_m = _REF.search(notes)
                ref = Decimal(str(ref_m.group("v") if ref_m else pos.entry_price))
                tf = pos.timeframe or "1h"
                armed = ensure_utc(pos.opened_at) if pos.opened_at else utc_now()
                try:
                    series = await provider.get_candles(
                        pos.symbol,
                        tf,
                        limit=200,
                        end_time=armed,
                    )
                    candles = list(series.candles) if series and not series.is_empty else []
                except Exception as exc:
                    print(f"SKIP {pos.symbol}: candles {exc}")
                    continue
                if len(candles) < 15:
                    print(f"SKIP {pos.symbol}: insufficient candles")
                    continue
                atr_f = wilder_atr(candles, len(candles) - 1, period=14)
                if atr_f is None or atr_f <= 0:
                    print(f"SKIP {pos.symbol}: no atr")
                    continue
                is_long = SignalDirection(pos.direction).is_long
                zone_lo, zone_hi = retest_zone(
                    ref,
                    Decimal(str(atr_f)),
                    is_long=is_long,
                    zone_near=near,
                    zone_far=far,
                )
                # Replace multiplier zone token with price zone + atr metadata.
                new_notes = _ZONE_ATR.sub(
                    f"zone={float(zone_lo)}-{float(zone_hi)};"
                    f"zone_atr={float(near)}-{float(far)};atr={atr_f}",
                    notes,
                    count=1,
                )
                pos.notes = new_notes
                fixed += 1
                print(
                    f"FIX {pos.symbol} ref={float(ref)} "
                    f"zone={float(zone_lo):.6f}-{float(zone_hi):.6f} atr={atr_f:.6f}"
                )
            await session.commit()
        print(f"fixed={fixed}")
    finally:
        await container.aclose()


if __name__ == "__main__":
    asyncio.run(main())
