"""Tests der Telegram-Nachrichtenformatierung.

Zwei Eigenschaften sind kritisch und werden hier festgeschrieben:
1. Jede ausgehende Nachricht enthaelt den Risikohinweis.
2. Zahlen stammen immer aus dem Signal, nie aus der LLM-Antwort.
"""

from __future__ import annotations

import pytest

from app.bot.formatting import (
    DISCLAIMER,
    SPLIT_LENGTH,
    TELEGRAM_MAX_LENGTH,
    escape_markdown_v2,
    format_analysis_message,
    format_paper_digest_message,
    format_paper_trade_close_message,
    format_paper_trade_open_message,
    format_price,
    format_score_breakdown,
    format_signal_message,
    split_caption_and_body,
    split_message,
)
from app.core.enums import SignalDirection
from app.llm.schemas import LLMAnalysisResponse
from app.models.paper import PaperPosition
from tests.test_dedup import make_result

_MDV2_SPECIAL = r"_*[]()~`>#+-=|{}.!"


class TestEscaping:
    def test_escapes_all_reserved_characters(self) -> None:
        for char in _MDV2_SPECIAL:
            assert escape_markdown_v2(char) == f"\\{char}"

    def test_leaves_plain_text_untouched(self) -> None:
        assert escape_markdown_v2("BTCUSDT Analyse") == "BTCUSDT Analyse"

    def test_escapes_decimal_point_in_prices(self) -> None:
        """Ein unmaskierter Punkt fuehrt bei Telegram zu HTTP 400."""
        assert escape_markdown_v2("67.200") == "67\\.200"

    def test_escapes_minus_in_negative_numbers(self) -> None:
        assert escape_markdown_v2("-12") == "\\-12"


class TestPriceFormatting:
    def test_uses_german_separators(self) -> None:
        assert format_price(67200.5, 2) == "67.200,50"

    def test_respects_precision(self) -> None:
        assert format_price(1.23456789, 6) == "1,234568"

    def test_handles_none(self) -> None:
        assert format_price(None) == "n/a"

    def test_handles_small_values(self) -> None:
        assert format_price(0.00012345, 8) == "0,00012345"


class TestSignalMessage:
    def test_contains_disclaimer(self) -> None:
        message = format_signal_message(make_result())
        assert escape_markdown_v2(DISCLAIMER) in message

    @pytest.mark.parametrize(
        "direction",
        list(SignalDirection),
    )
    def test_disclaimer_present_for_every_direction(self, direction: SignalDirection) -> None:
        message = format_signal_message(make_result(direction=direction))
        assert escape_markdown_v2(DISCLAIMER) in message

    def test_contains_core_signal_fields(self) -> None:
        message = format_signal_message(make_result())
        plain = message.replace("\\", "")
        assert "Alpha Trade Oracle" in plain
        assert plain.index("Alpha Trade Oracle") < plain.index("BTC/USDT")
        assert "BTC/USDT" in message
        assert "LONG" in message
        assert "Confidence: 72/100" in message

    def test_contains_risk_levels_for_actionable_signal(self) -> None:
        message = format_signal_message(make_result())
        assert "Entry" in message
        assert "SL" in message
        assert "TP1" in message
        assert "TP2" in message
        assert "TP3" in message
        assert "Plan" not in message
        assert "33/33/34" not in message
        # TPs stehen untereinander, nicht in einer Zeile.
        tp1_line = next(line for line in message.splitlines() if "TP1" in line)
        assert "TP2" not in tp1_line
        assert "TP3" not in tp1_line

    def test_shows_reason_for_no_trade(self) -> None:
        result = make_result(direction=SignalDirection.NO_TRADE)
        result.no_trade_reason = "Chance-Risiko-Verhaeltnis zu niedrig"
        message = format_signal_message(result)
        assert "Reason" in message
        assert escape_markdown_v2("Chance-Risiko-Verhaeltnis zu niedrig") in message

    def test_omits_entry_levels_when_not_actionable(self) -> None:
        message = format_signal_message(make_result(direction=SignalDirection.NEUTRAL))
        assert "TP1" not in message

    def test_includes_confirmations(self) -> None:
        result = make_result()
        result.reasons = [
            "Trend stack: 4h bullish, 1h bullish, 15m bullish",
            "EMA9 above EMA20; Supertrend bullish",
        ]
        message = format_signal_message(result)
        assert "*Confirmations:*" in message
        assert escape_markdown_v2("Trend stack: 4h bullish, 1h bullish, 15m bullish") in message

    def test_fits_telegram_limit(self) -> None:
        message = format_signal_message(make_result())
        assert len(message) < TELEGRAM_MAX_LENGTH

    def test_prices_use_configured_precision(self) -> None:
        message = format_signal_message(make_result(entry_mid=40_000.0), price_precision=2)
        assert escape_markdown_v2("39.900,00") in message


class TestLLMIsolation:
    def test_llm_prose_is_included(self) -> None:
        analysis = LLMAnalysisResponse(
            summary="Die technische Lage ist ueberwiegend konstruktiv, aber nicht eindeutig.",
            reasons=["EMA-Staffelung aufwaerts"],
            risks=["Widerstand in Reichweite"],
        )
        message = format_signal_message(make_result(), llm_analysis=analysis)
        assert escape_markdown_v2("EMA-Staffelung aufwaerts") in message
        assert "*Confirmations:*" in message

    def test_llm_cannot_change_prices(self) -> None:
        """Selbst eine halluzinierte Zahl im Text aendert keinen Kurs."""
        analysis = LLMAnalysisResponse(
            summary="Der Kurs steht bei 999.999 und der Stop liegt bei 111.111 USDT.",
            reasons=["Erfundener Grund mit 123.456"],
            risks=["Erfundenes Risiko"],
        )
        result = make_result(entry_mid=40_000.0)
        message = format_signal_message(result, llm_analysis=analysis)

        assert result.risk is not None
        # Die Risikozeilen tragen die berechneten Werte, nicht die des LLM.
        assert escape_markdown_v2(format_price(result.risk.stop_loss, 2)) in message
        # Halluzinierte Kurse duerfen nicht als SL/Entry erscheinen.
        before_confirmations = message.split("*Confirmations:*")[0]
        assert escape_markdown_v2("111.111") not in before_confirmations
        assert escape_markdown_v2("999.999") not in before_confirmations

    def test_message_without_llm_is_complete(self) -> None:
        """Das System muss ohne LLM voll funktionsfaehig bleiben."""
        result = make_result()
        result.reasons = ["Struktur: hoehere Hochs und hoehere Tiefs"]
        message = format_signal_message(result, llm_analysis=None)
        assert "Confirmations" in message
        assert escape_markdown_v2(DISCLAIMER) in message


class TestAnalysisMessage:
    def test_adds_note_for_non_actionable_result(self) -> None:
        message = format_analysis_message(make_result(direction=SignalDirection.NEUTRAL))
        assert "No tradeable setup" in message

    def test_no_extra_note_for_actionable_result(self) -> None:
        message = format_analysis_message(make_result(direction=SignalDirection.LONG))
        assert "No tradeable setup" not in message

    def test_contains_disclaimer(self) -> None:
        message = format_analysis_message(make_result(direction=SignalDirection.NEUTRAL))
        assert escape_markdown_v2(DISCLAIMER) in message

    def test_includes_llm_summary_for_analyze(self) -> None:
        analysis = LLMAnalysisResponse(
            summary="Die technische Lage ist ueberwiegend konstruktiv, aber nicht eindeutig.",
            reasons=["EMA-Staffelung aufwaerts"],
            risks=["Widerstand in Reichweite"],
        )
        message = format_analysis_message(make_result(), llm_analysis=analysis)
        assert "*Context:*" in message
        assert escape_markdown_v2(
            "Die technische Lage ist ueberwiegend konstruktiv, aber nicht eindeutig."
        ) in message


class TestScoreBreakdown:
    def test_contains_disclaimer(self) -> None:
        assert escape_markdown_v2(DISCLAIMER) in format_score_breakdown(make_result())

    def test_contains_total_score(self) -> None:
        assert "Total confidence" in format_score_breakdown(make_result(score=72.0))


class TestCaptionSplit:
    def test_short_text_fits_in_caption(self) -> None:
        caption, body = split_caption_and_body("hello")
        assert caption == "hello"
        assert body is None

    def test_long_text_keeps_brand_in_caption(self) -> None:
        head = "*Alpha Trade Oracle*\n*UNI/USDT* · *STRONG SHORT*\nConfidence: 78/100\n"
        text = head + ("x" * 1200)
        caption, body = split_caption_and_body(text, caption_limit=200)
        assert caption is not None
        assert "Alpha Trade Oracle" in caption
        assert body is not None
        assert len(caption) <= 200


class TestSplitting:
    def test_short_message_stays_whole(self) -> None:
        assert split_message("kurz") == ["kurz"]

    def test_long_message_is_split(self) -> None:
        text = "\n".join(f"Zeile {i}" for i in range(2_000))
        parts = split_message(text)
        assert len(parts) > 1
        assert all(len(part) <= SPLIT_LENGTH for part in parts)

    def test_split_preserves_all_lines(self) -> None:
        lines = [f"Zeile {i}" for i in range(2_000)]
        parts = split_message("\n".join(lines))
        assert "\n".join(parts).count("Zeile") == len(lines)

    def test_never_splits_within_a_line(self) -> None:
        lines = [f"Zeile {i} mit etwas mehr Text zur Fuellung" for i in range(400)]
        for part in split_message("\n".join(lines)):
            for line in part.split("\n"):
                assert line == "" or line in lines

    def test_oversized_single_line_is_hard_cut(self) -> None:
        parts = split_message("x" * (SPLIT_LENGTH * 2 + 10))
        assert len(parts) == 3
        assert all(len(part) <= SPLIT_LENGTH for part in parts)


def _sample_paper_position(**overrides) -> PaperPosition:
    from datetime import UTC, datetime
    from decimal import Decimal

    defaults = {
        "account_id": 1,
        "symbol": "BTCUSDT",
        "direction": "STRONG_LONG",
        "status": "open",
        "timeframe": "1h",
        "entry_price": Decimal("100000"),
        "stop_loss": Decimal("98000"),
        "current_stop": Decimal("98000"),
        "take_profit_1": Decimal("104000"),
        "take_profit_2": Decimal("108000"),
        "take_profit_3": Decimal("112000"),
        "initial_quantity": Decimal("0.01"),
        "remaining_quantity": Decimal("0.01"),
        "margin_used": Decimal("100"),
        "notional": Decimal("1000"),
        "leverage": 10.0,
        "fees": Decimal("1"),
        "signal_score": 82.0,
        "opened_at": datetime(2024, 6, 1, 12, tzinfo=UTC),
    }
    defaults.update(overrides)
    return PaperPosition(**defaults)


class TestPaperTradeFormatting:
    def test_open_message_contains_disclaimer(self) -> None:
        message = format_paper_trade_open_message(_sample_paper_position())
        assert DISCLAIMER in message.replace("\\", "")

    def test_open_message_labels_retest_fill(self) -> None:
        message = format_paper_trade_open_message(
            _sample_paper_position(), retest_fill=True
        )
        assert "Retest" in message.replace("\\", "")

    def test_open_message_includes_reasons(self) -> None:
        message = format_paper_trade_open_message(
            _sample_paper_position(),
            reasons=["Trend stack bullish", "EMA9 above EMA20"],
        )
        assert "Confirmations" in message
        assert escape_markdown_v2("Trend stack bullish") in message

    def test_close_message_contains_pnl(self) -> None:
        from decimal import Decimal

        position = _sample_paper_position(
            status="closed",
            realized_pnl=Decimal("12.5"),
            exit_reason="take_profit_1",
            closed_at=_sample_paper_position().opened_at,
        )
        message = format_paper_trade_close_message(position)
        assert "12,50" in message
        assert DISCLAIMER in message.replace("\\", "")


class TestPaperDigestFormatting:
    def test_digest_contains_open_pnl_and_tp_status(self) -> None:
        from datetime import UTC, datetime

        from app.services.paper_trading_service import (
            PaperDigestCloseRow,
            PaperDigestOpenRow,
            PaperDigestSnapshot,
            PaperDigestWindowStats,
            PaperSummary,
        )

        snapshot = PaperDigestSnapshot(
            as_of=datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
            summary=PaperSummary(
                cash_balance=4210.0,
                initial_balance=5000.0,
                realized_pnl=98.4,
                open_positions=1,
                open_margin=100.0,
                equity=5142.3,
                win_rate=0.58,
                closed_trades=12,
                profit_factor=1.42,
                pending_positions=2,
                total_r=1.92,
                expectancy_r=0.16,
            ),
            equity_return_pct=2.8,
            hour_closed_count=1,
            hour_closed_r=0.62,
            hour_closed_pnl=31.0,
            hour_opened_count=1,
            open_rows=[
                PaperDigestOpenRow(
                    symbol="BTCUSDT",
                    direction="LONG",
                    unrealized_usd=42.1,
                    unrealized_r=0.84,
                    mark=68420.0,
                    current_stop=67100.0,
                    rem_pct=50.0,
                    tp1_filled=True,
                    tp2_filled=True,
                    tp3_filled=False,
                )
            ],
            hour_closes=[
                PaperDigestCloseRow(
                    symbol="SOLUSDT",
                    direction="LONG",
                    realized_usd=31.0,
                    realized_r=0.62,
                    exit_reason="take_profit_1",
                )
            ],
            total_open_upnl_usd=42.1,
            total_open_upnl_r=0.84,
            risk_per_trade=50.0,
            leverage=10.0,
            max_notional=1500.0,
            max_open=20,
            windows=[
                PaperDigestWindowStats(
                    label="1h",
                    closed_count=1,
                    closed_pnl=31.0,
                    closed_r=0.62,
                    opened_count=1,
                    win_count=1,
                    equity_delta=12.5,
                ),
                PaperDigestWindowStats(
                    label="24h",
                    closed_count=4,
                    closed_pnl=80.0,
                    closed_r=1.5,
                    opened_count=3,
                    win_count=3,
                    equity_delta=40.0,
                ),
                PaperDigestWindowStats(
                    label="7d",
                    closed_count=12,
                    closed_pnl=98.4,
                    closed_r=1.92,
                    opened_count=15,
                    win_count=7,
                    equity_delta=142.3,
                ),
            ],
        )
        message = format_paper_digest_message(snapshot)
        plain = message.replace("\\", "")
        assert "Performance Dashboard" in plain
        assert "Alpha Trade Oracle" in plain
        assert plain.index("Alpha Trade Oracle") < plain.index("Performance Dashboard")
        assert "Performance Dashboard  ·  " in plain
        assert "Value  $5.142,30" in plain
        assert "Cash + Open PnL" in plain
        assert "ACCOUNT" in plain
        assert "Value" in plain
        assert "PERFORMANCE" not in plain
        assert "Eq +$12,50" not in plain
        assert "+$42,10" in plain
        assert "+0.84R" not in plain
        assert "+0.00R" not in plain
        assert "TP ✓1 ✓2 ·3" in plain
        assert "rem 50%" in plain
        assert "+$31,00" in plain
        assert "+$8,20/Trade" in plain
        assert "Signal 2" not in plain
        assert "Risiko $" not in plain
        assert DISCLAIMER in plain
