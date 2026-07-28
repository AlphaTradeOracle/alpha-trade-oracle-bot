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
    format_price,
    format_score_breakdown,
    format_signal_message,
    split_message,
)
from app.core.enums import SignalDirection
from app.llm.schemas import LLMAnalysisResponse
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
        assert "BTC/USDT" in message
        assert "LONG" in message
        assert "Score: 72/100" in message

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
        assert "Grund" in message
        assert escape_markdown_v2("Chance-Risiko-Verhaeltnis zu niedrig") in message

    def test_omits_entry_levels_when_not_actionable(self) -> None:
        message = format_signal_message(make_result(direction=SignalDirection.NEUTRAL))
        assert "TP1" not in message

    def test_includes_confirmations(self) -> None:
        result = make_result()
        result.reasons = [
            "Trendlage: 4h bullisch, 1h bullisch, 15m bullisch",
            "EMA9 ueber EMA20; Supertrend bullisch",
        ]
        message = format_signal_message(result)
        assert "*Bestaetigungen:*" in message
        assert escape_markdown_v2("Trendlage: 4h bullisch, 1h bullisch, 15m bullisch") in message

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
        assert "*Bestaetigungen:*" in message

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
        before_confirmations = message.split("*Bestaetigungen:*")[0]
        assert escape_markdown_v2("111.111") not in before_confirmations
        assert escape_markdown_v2("999.999") not in before_confirmations

    def test_message_without_llm_is_complete(self) -> None:
        """Das System muss ohne LLM voll funktionsfaehig bleiben."""
        result = make_result()
        result.reasons = ["Struktur: hoehere Hochs und hoehere Tiefs"]
        message = format_signal_message(result, llm_analysis=None)
        assert "Bestaetigungen" in message
        assert escape_markdown_v2(DISCLAIMER) in message


class TestAnalysisMessage:
    def test_adds_note_for_non_actionable_result(self) -> None:
        message = format_analysis_message(make_result(direction=SignalDirection.NEUTRAL))
        assert "kein handelbares Setup" in message

    def test_no_extra_note_for_actionable_result(self) -> None:
        message = format_analysis_message(make_result(direction=SignalDirection.LONG))
        assert "kein handelbares Setup" not in message

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
        assert "*Einordnung:*" in message
        assert escape_markdown_v2(
            "Die technische Lage ist ueberwiegend konstruktiv, aber nicht eindeutig."
        ) in message


class TestScoreBreakdown:
    def test_contains_disclaimer(self) -> None:
        assert escape_markdown_v2(DISCLAIMER) in format_score_breakdown(make_result())

    def test_contains_total_score(self) -> None:
        assert "Gesamtscore" in format_score_breakdown(make_result(score=72.0))


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
