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
from app.signals.types import SignalResult

#: Pflicht-Risikohinweis. Erscheint in jeder ausgehenden Analyse-Nachricht.
DISCLAIMER = "Keine Finanzberatung. Kryptowaehrungen sind hochriskant."

#: Telegram-Limit pro Nachricht; mit Sicherheitsabstand.
TELEGRAM_MAX_LENGTH = 4096
#: Caption-Limit fuer Fotos.
TELEGRAM_CAPTION_MAX = 1024
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
    """Kompakte Signal-Nachricht: Levels + Plan + Bestaetigungen."""
    symbol_label = _pretty_symbol(result.symbol)
    direction = _DIRECTION_LABELS[result.direction]
    lines: list[str] = [
        f"*{escape_markdown_v2(symbol_label)}* · *{escape_markdown_v2(direction)}*",
        escape_markdown_v2(f"Score: {result.score:.0f}/100"),
    ]

    if result.direction == SignalDirection.NO_TRADE and result.no_trade_reason:
        lines += ["", f"*Grund:* {escape_markdown_v2(result.no_trade_reason)}"]

    if result.risk is not None and result.direction.is_actionable:
        risk = result.risk
        quote = _quote_asset(result.symbol)
        lines += [
            "",
            escape_markdown_v2(
                f"Entry  {format_price(risk.entry_low, price_precision)}"
                f" – {format_price(risk.entry_high, price_precision)}"
            ),
            escape_markdown_v2(f"SL     {format_price(risk.stop_loss, price_precision)} {quote}"),
            escape_markdown_v2(f"TP1    {format_price(risk.take_profit_1, price_precision)}"),
            escape_markdown_v2(f"TP2    {format_price(risk.take_profit_2, price_precision)}"),
            escape_markdown_v2(f"TP3    {format_price(risk.take_profit_3, price_precision)}"),
        ]

    reasons = llm_analysis.reasons if llm_analysis else result.reasons
    if reasons:
        lines += ["", "*Bestaetigungen:*"]
        lines += [f"• {escape_markdown_v2(item)}" for item in reasons[:8]]

    risks = llm_analysis.risks if llm_analysis else result.counter_arguments
    if risks:
        lines += ["", "*Risiken:*"]
        lines += [f"• {escape_markdown_v2(item)}" for item in risks[:4]]

    if result.risk is not None and result.direction.is_actionable and result.risk.invalidation_note:
        lines += ["", f"*Ungueltig:* {escape_markdown_v2(result.risk.invalidation_note)}"]

    phase = _PHASE_LABELS.get(result.market_phase.value, result.market_phase.value)
    meta = (
        f"{result.primary_timeframe} · {phase} · "
        f"Daten {result.data_quality:.0f}/100"
    )
    lines += [
        "",
        escape_markdown_v2(meta),
        escape_markdown_v2(
            f"Bis {format_display_time(result.expires_at, display_timezone)}"
        ),
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
    """Analyse-Nachricht fuer ``/analyze`` — kompakt plus optionaler Einordnung."""
    message = format_signal_message(
        result,
        price_precision=price_precision,
        display_timezone=display_timezone,
        llm_analysis=llm_analysis,
    )

    extras: list[str] = []
    if not result.direction.is_actionable:
        extras.append(
            "_"
            + escape_markdown_v2(
                "Aktuell liegt kein handelbares Setup vor. Die Analyse dient der "
                "Einordnung der Marktlage."
            )
            + "_"
        )

    if llm_analysis and llm_analysis.summary:
        extras.append(f"*Einordnung:*\n{escape_markdown_v2(llm_analysis.summary)}")

    if llm_analysis and llm_analysis.uncertainty_note:
        extras.append(
            f"*Unsicherheiten:*\n{escape_markdown_v2(llm_analysis.uncertainty_note)}"
        )

    if not extras:
        return message

    # Extras vor dem Disclaimer einfuegen.
    marker = f"⚠️ {escape_markdown_v2(DISCLAIMER)}"
    insertion = "\n\n".join(extras) + "\n\n"
    if marker in message:
        return message.replace(marker, insertion + marker, 1)
    return message + "\n\n" + insertion.rstrip()


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


def split_caption_and_body(
    text: str, *, caption_limit: int = TELEGRAM_CAPTION_MAX
) -> tuple[str | None, str | None]:
    """Text fuer Photo-Caption aufteilen.

    Passt alles in die Caption, gibt ``(caption, None)`` zurueck.
    Sonst ``(None, text)`` — Chart ohne Caption, voller Text darunter.
    """
    if len(text) <= caption_limit:
        return text, None
    return None, text


def _pretty_symbol(symbol: str) -> str:
    """``BTCUSDT`` als ``BTC/USDT`` darstellen."""
    for quote in ("USDT", "USDC", "FDUSD", "BUSD", "TUSD", "EUR", "TRY", "BTC", "ETH", "BNB"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return f"{symbol[: -len(quote)]}/{quote}"
    return symbol


def _quote_asset(symbol: str) -> str:
    pretty = _pretty_symbol(symbol)
    return pretty.split("/")[1] if "/" in pretty else ""
