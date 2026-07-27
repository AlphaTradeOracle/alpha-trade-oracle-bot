"""Telegram-Nachrichtenformatierung (MarkdownV2).

Zentrale Regel dieses Moduls: **alle Zahlen stammen aus dem Signal-Objekt**, nie
aus der LLM-Antwort. Das LLM liefert ausschliesslich Prosa. Selbst eine
halluzinierte Zahl in der Zusammenfassung kann damit keinen falschen Kurs in die
Nachricht bringen.

Jede versandfertige Nachricht enthaelt den Risikohinweis. Das ist durch einen
Test abgesichert.
"""

from __future__ import annotations

from app.core.enums import Confidence, SignalDirection
from app.core.time import format_display_time
from app.llm.schemas import LLMAnalysisResponse
from app.signals.multi_timeframe import describe_timeframe_trends
from app.signals.types import SignalResult

#: Pflicht-Risikohinweis. Erscheint in jeder ausgehenden Analyse-Nachricht.
DISCLAIMER = "Keine Finanzberatung. Kryptowaehrungen sind hochriskant."

#: Telegram-Limit pro Nachricht; mit Sicherheitsabstand.
TELEGRAM_MAX_LENGTH = 4096
SPLIT_LENGTH = 3900

#: In MarkdownV2 reservierte Zeichen.
_MDV2_SPECIAL = r"_*[]()~`>#+-=|{}.!"

_DIRECTION_LABELS: dict[SignalDirection, str] = {
    SignalDirection.STRONG_LONG: "STARKES LONG",
    SignalDirection.LONG: "LONG",
    SignalDirection.NEUTRAL: "NEUTRAL",
    SignalDirection.SHORT: "SHORT",
    SignalDirection.STRONG_SHORT: "STARKES SHORT",
    SignalDirection.NO_TRADE: "KEIN TRADE",
}

_CONFIDENCE_LABELS: dict[Confidence, str] = {
    Confidence.HIGH: "Hoch",
    Confidence.MEDIUM: "Mittel",
    Confidence.LOW: "Niedrig",
}

_PHASE_LABELS = {
    "UPTREND": "Aufwaertstrend",
    "DOWNTREND": "Abwaertstrend",
    "RANGE": "Seitwaertsbereich",
    "VOLATILE": "Hohe Volatilitaet",
}


def escape_markdown_v2(text: str) -> str:
    """Alle in MarkdownV2 reservierten Zeichen maskieren.

    Ohne diese Maskierung lehnt Telegram Nachrichten mit Kursen wie ``67.200``
    (der Punkt ist reserviert) mit HTTP 400 ab.
    """
    return "".join(f"\\{char}" if char in _MDV2_SPECIAL else char for char in text)


def format_price(value: float | None, precision: int = 2) -> str:
    """Preis mit deutscher Tausender- und Dezimaltrennung."""
    if value is None:
        return "n/a"
    formatted = f"{value:,.{precision}f}"
    # en-US -> de-DE: Tausenderpunkt und Dezimalkomma tauschen.
    return formatted.replace(",", "\u00a0").replace(".", ",").replace("\u00a0", ".")


def format_signal_message(
    result: SignalResult,
    *,
    price_precision: int = 2,
    display_timezone: str = "Europe/Berlin",
    llm_analysis: LLMAnalysisResponse | None = None,
) -> str:
    """Vollstaendige Signal-Nachricht in MarkdownV2 erzeugen."""
    symbol_label = _pretty_symbol(result.symbol)
    lines: list[str] = [
        f"*{escape_markdown_v2('Alpha Trade Oracle Signal')}*",
        "",
        f"*Asset:* {escape_markdown_v2(symbol_label)}",
        f"*Signal:* {escape_markdown_v2(_DIRECTION_LABELS[result.direction])}",
        f"*Staerke:* {escape_markdown_v2(f'{result.score:.0f}/100')}",
        f"*Konfidenz:* {escape_markdown_v2(_CONFIDENCE_LABELS[result.confidence])}",
        f"*Marktphase:* {escape_markdown_v2(_PHASE_LABELS.get(result.market_phase.value, result.market_phase.value))}",  # noqa: E501
    ]

    if result.direction == SignalDirection.NO_TRADE and result.no_trade_reason:
        lines += ["", f"*Grund:* {escape_markdown_v2(result.no_trade_reason)}"]

    if result.risk is not None and result.direction.is_actionable:
        risk = result.risk
        quote = _quote_asset(result.symbol)
        lines += [
            "",
            "*Entry:*",
            escape_markdown_v2(
                f"{format_price(risk.entry_low, price_precision)}"
                f"-{format_price(risk.entry_high, price_precision)} {quote}"
            ),
            "",
            "*Stop-Loss:*",
            escape_markdown_v2(f"{format_price(risk.stop_loss, price_precision)} {quote}"),
            "",
            "*Take Profit:*",
            escape_markdown_v2(f"TP1: {format_price(risk.take_profit_1, price_precision)} {quote}"),
            escape_markdown_v2(f"TP2: {format_price(risk.take_profit_2, price_precision)} {quote}"),
            escape_markdown_v2(f"TP3: {format_price(risk.take_profit_3, price_precision)} {quote}"),
            "",
            "*Chance-Risiko-Verhaeltnis:*",
            escape_markdown_v2(f"{risk.risk_reward_ratio:.2f}".replace(".", ",")),
            "",
            "*Positionsgroesse (nur informativ):*",
            escape_markdown_v2(
                f"{format_price(risk.suggested_position_size, 6)} bei "
                f"{format_price(risk.risk_percent, 2)}% Risiko"
            ),
        ]

    trends = describe_timeframe_trends(result.assessments)
    if trends:
        lines += ["", "*Trend:*"]
        lines += [escape_markdown_v2(trend) for trend in trends]

    summary = llm_analysis.summary if llm_analysis else None
    if summary:
        lines += ["", "*Einordnung:*", escape_markdown_v2(summary)]

    reasons = llm_analysis.reasons if llm_analysis else result.reasons
    if reasons:
        lines += ["", "*Bestaetigungen:*"]
        lines += [f"• {escape_markdown_v2(item)}" for item in reasons[:6]]

    risks = llm_analysis.risks if llm_analysis else result.counter_arguments
    if risks:
        lines += ["", "*Risiken:*"]
        lines += [f"• {escape_markdown_v2(item)}" for item in risks[:6]]
    elif result.direction.is_actionable:
        lines += [
            "",
            "*Risiken:*",
            f"• {escape_markdown_v2('Keine wesentlichen Gegenargumente erkannt')}",
        ]

    if llm_analysis and llm_analysis.uncertainty_note:
        lines += ["", "*Unsicherheiten:*", escape_markdown_v2(llm_analysis.uncertainty_note)]

    if llm_analysis and llm_analysis.market_sentiment_note:
        lines += ["", "*Marktstimmung:*", escape_markdown_v2(llm_analysis.market_sentiment_note)]

    if result.risk is not None and result.direction.is_actionable:
        lines += ["", "*Ungueltig bei:*", escape_markdown_v2(result.risk.invalidation_note)]

    lines += [
        "",
        escape_markdown_v2(
            f"Timeframes: {', '.join(result.analyzed_timeframes)} | "
            f"Datenqualitaet: {result.data_quality:.0f}/100"
        ),
        escape_markdown_v2(
            f"Erstellt: {format_display_time(result.created_at, display_timezone)} | "
            f"Gueltig bis: {format_display_time(result.expires_at, display_timezone)}"
        ),
        "",
        f"⚠️ {escape_markdown_v2(DISCLAIMER)}",
    ]

    return "\n".join(lines)


def format_analysis_message(
    result: SignalResult,
    *,
    price_precision: int = 2,
    display_timezone: str = "Europe/Berlin",
    llm_analysis: LLMAnalysisResponse | None = None,
) -> str:
    """Analyse-Nachricht fuer ``/analyze``.

    Unterscheidet sich von :func:`format_signal_message` darin, dass auch
    neutrale Ergebnisse ausfuehrlich dargestellt werden — der Nutzer hat
    explizit nach einer Analyse gefragt, nicht nach einem handelbaren Setup.
    """
    message = format_signal_message(
        result,
        price_precision=price_precision,
        display_timezone=display_timezone,
        llm_analysis=llm_analysis,
    )

    if not result.direction.is_actionable:
        note = (
            "Aktuell liegt kein handelbares Setup vor. Die Analyse dient der "
            "Einordnung der Marktlage."
        )
        marker = f"*{escape_markdown_v2('Marktphase:')}*"
        insertion = f"\n\n_{escape_markdown_v2(note)}_"
        index = message.find("\n", message.find(marker))
        if index != -1:
            message = message[:index] + insertion + message[index:]

    return message


def format_score_breakdown(result: SignalResult) -> str:
    """Score-Breakdown als eigene Nachricht — nachvollziehbar statt Blackbox."""
    lines = [f"*{escape_markdown_v2(f'Score-Aufschluesselung {result.symbol}')}*", ""]
    for component in sorted(result.components, key=lambda c: -abs(c.weighted_score)):
        lines.append(
            escape_markdown_v2(
                f"{component.category.value}: roh {component.raw_score:+.1f} "
                f"x {component.weight:.3f} = {component.weighted_score:+.2f}"
            )
        )
        if component.detail:
            lines.append(f"  _{escape_markdown_v2(component.detail[:180])}_")
    lines += ["", escape_markdown_v2(f"Gesamtscore: {result.score:.2f}/100")]
    lines += ["", f"⚠️ {escape_markdown_v2(DISCLAIMER)}"]
    return "\n".join(lines)


def split_message(text: str, limit: int = SPLIT_LENGTH) -> list[str]:
    """Lange Nachricht an Zeilengrenzen aufteilen.

    Es wird nie mitten in einer Zeile getrennt, weil das MarkdownV2-Auszeichnungen
    zerreissen und zu HTTP 400 fuehren wuerde.
    """
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    current: list[str] = []
    current_length = 0

    for line in text.split("\n"):
        line_length = len(line) + 1
        if current_length + line_length > limit and current:
            parts.append("\n".join(current))
            current = [line]
            current_length = line_length
        else:
            current.append(line)
            current_length += line_length

    if current:
        parts.append("\n".join(current))

    # Eine einzelne Zeile laenger als das Limit muss hart geschnitten werden.
    result: list[str] = []
    for part in parts:
        while len(part) > limit:
            result.append(part[:limit])
            part = part[limit:]
        if part:
            result.append(part)
    return result


def _pretty_symbol(symbol: str) -> str:
    """``BTCUSDT`` als ``BTC/USDT`` darstellen."""
    for quote in ("USDT", "USDC", "FDUSD", "BUSD", "TUSD", "EUR", "TRY", "BTC", "ETH", "BNB"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return f"{symbol[: -len(quote)]}/{quote}"
    return symbol


def _quote_asset(symbol: str) -> str:
    pretty = _pretty_symbol(symbol)
    return pretty.split("/")[1] if "/" in pretty else ""
