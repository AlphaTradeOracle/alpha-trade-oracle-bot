"""Telegram-Nachrichtenformatierung (MarkdownV2).

Zentrale Regel dieses Moduls: **alle Zahlen stammen aus dem Signal-Objekt**, nie
aus der LLM-Antwort. Das LLM liefert ausschliesslich Prosa. Selbst eine
halluzinierte Zahl in der Zusammenfassung kann damit keinen falschen Kurs in die
Nachricht bringen.

Jede versandfertige Nachricht enthaelt den Risikohinweis. Das ist durch einen
Test abgesichert.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.enums import Confidence, ExitReason, MarketPhase, SignalDirection
from app.core.time import format_display_time, utc_now
from app.llm.schemas import LLMAnalysisResponse
from app.signals.types import RiskParameters, SignalResult

if TYPE_CHECKING:
    from app.models.paper import PaperPosition
    from app.services.paper_trading_service import PaperDigestSnapshot, PaperSummary

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


_EXIT_REASON_LABELS: dict[str, str] = {
    ExitReason.TAKE_PROFIT_1.value: "Take-Profit 1",
    ExitReason.TAKE_PROFIT_2.value: "Take-Profit 2",
    ExitReason.TAKE_PROFIT_3.value: "Take-Profit 3",
    ExitReason.STOP_LOSS.value: "Stop-Loss",
    ExitReason.EXPIRED.value: "Signal abgelaufen",
    ExitReason.END_OF_DATA.value: "Ende der Daten",
    ExitReason.RETEST_SKIPPED.value: "Retest uebersprungen",
}


def infer_price_precision(price: float) -> int:
    """Grobe Praezision fuer Telegram-Preisanzeige."""
    absolute = abs(price)
    if absolute >= 1000:
        return 2
    if absolute >= 1:
        return 4
    if absolute >= 0.01:
        return 6
    return 8


def signal_result_from_paper_position(
    position: PaperPosition,
    *,
    reasons: list[str] | None = None,
) -> SignalResult:
    """SignalResult aus einer eroeffneten Paper-Position fuer Telegram-Format."""
    entry = float(position.entry_price)
    spread = max(abs(entry) * 0.0005, 10 ** (-infer_price_precision(entry)))
    try:
        direction = SignalDirection(position.direction)
    except ValueError:
        direction = SignalDirection.LONG

    opened_at = position.opened_at or utc_now()
    expires_at = position.expires_at or opened_at

    return SignalResult(
        symbol=position.symbol,
        created_at=opened_at,
        expires_at=expires_at,
        direction=direction,
        score=float(position.signal_score or 0),
        confidence=Confidence.MEDIUM,
        market_phase=MarketPhase.RANGE,
        primary_timeframe=position.timeframe or "1h",
        analyzed_timeframes=[position.timeframe or "1h"],
        reference_price=entry,
        data_quality=100.0,
        components=[],
        assessments={},
        risk=RiskParameters(
            entry_low=entry - spread,
            entry_high=entry + spread,
            stop_loss=float(position.stop_loss),
            take_profit_1=float(position.take_profit_1),
            take_profit_2=float(position.take_profit_2),
            take_profit_3=float(position.take_profit_3),
            risk_reward_ratio=0.0,
            risk_percent=0.0,
            suggested_position_size=0.0,
            stop_distance_percent=0.0,
            invalidation_note="",
        ),
        reasons=list(reasons or []),
    )


def format_paper_trade_open_message(
    position: PaperPosition,
    *,
    price_precision: int = 2,
    display_timezone: str = "Europe/Berlin",
    retest_fill: bool = False,
    reasons: list[str] | None = None,
) -> str:
    """Paper-Trade-Eroeffnung (IST oder Retest-Fill)."""
    try:
        direction = _DIRECTION_LABELS[SignalDirection(position.direction)]
    except ValueError:
        direction = position.direction
    symbol_label = _pretty_symbol(position.symbol)
    quote = _quote_asset(position.symbol)
    entry_kind = "Retest-Fill" if retest_fill else "Paper-Trade"
    lines: list[str] = [
        f"📄 *{escape_markdown_v2(entry_kind)}* · *{escape_markdown_v2(symbol_label)}*",
        f"*{escape_markdown_v2(direction)}*",
    ]
    if position.signal_score is not None:
        lines.append(escape_markdown_v2(f"Score: {float(position.signal_score):.0f}/100"))
    if reasons:
        lines += ["", "*Bestaetigungen:*"]
        lines += [f"• {escape_markdown_v2(item)}" for item in reasons[:8]]
    lines += [
        "",
        escape_markdown_v2(f"Entry  {format_price(float(position.entry_price), price_precision)} {quote}"),
        escape_markdown_v2(f"SL     {format_price(float(position.stop_loss), price_precision)} {quote}"),
        escape_markdown_v2(f"TP1    {format_price(float(position.take_profit_1), price_precision)}"),
        escape_markdown_v2(f"TP2    {format_price(float(position.take_profit_2), price_precision)}"),
        escape_markdown_v2(f"TP3    {format_price(float(position.take_profit_3), price_precision)}"),
        "",
        escape_markdown_v2(
            f"Margin {format_price(float(position.margin_used), 2)} · "
            f"{float(position.leverage):.0f}x · "
            f"Notional {format_price(float(position.notional), 2)}"
        ),
        escape_markdown_v2(f"Risiko (1R) {format_price(float(position.risk_amount or 0), 2)}"),
        escape_markdown_v2(
            f"{position.timeframe or '1h'} · "
            f"Eroeffnet {format_display_time(position.opened_at, display_timezone)}"
        ),
        f"⚠️ {escape_markdown_v2(DISCLAIMER)}",
    ]
    return "\n".join(lines)


def _signed_usd(value: float) -> str:
    absolute = format_price(abs(value), 2)
    return f"+${absolute}" if value >= 0 else f"-${absolute}"


def _signed_r(value: float) -> str:
    return f"{value:+.2f}R"


def _expectancy_usd(summary: PaperSummary) -> float:
    """Mittlerer realisierter Dollar-Gewinn pro Closed Trade."""
    if summary.closed_trades <= 0:
        return 0.0
    return summary.realized_pnl / summary.closed_trades


def _tp_status(tp1: bool, tp2: bool, tp3: bool) -> str:
    def mark(hit: bool, n: int) -> str:
        return f"✓{n}" if hit else f"·{n}"

    return f"TP {mark(tp1, 1)} {mark(tp2, 2)} {mark(tp3, 3)}"


def _short_direction(direction: str) -> str:
    try:
        parsed = SignalDirection(direction)
    except ValueError:
        return direction
    if parsed.is_long:
        return "LONG"
    if parsed.is_short:
        return "SHORT"
    return direction


def format_paper_digest_message(
    snapshot: PaperDigestSnapshot,
    *,
    display_timezone: str = "Europe/Berlin",
    max_open: int = 5,
    max_closes: int = 5,
) -> str:
    """Stuendlicher Paper-Performance-Digest fuer Telegram."""
    summary = snapshot.summary
    stamp = format_display_time(snapshot.as_of, display_timezone)
    lines: list[str] = [
        f"*{escape_markdown_v2('Performance Dashboard')}*",
        escape_markdown_v2(f"Alpha Trade Oracle  ·  {stamp}"),
        "",
        f"*{escape_markdown_v2('DEPOT')}*",
        escape_markdown_v2(
            f"Equity    ${format_price(summary.equity, 2)}  "
            f"({snapshot.equity_return_pct:+.1f}%)"
        ),
        escape_markdown_v2(f"Cash      ${format_price(summary.cash_balance, 2)}"),
        escape_markdown_v2(f"Realized  {_signed_usd(summary.realized_pnl)}"),
        escape_markdown_v2(
            f"Win-Rate  {summary.win_rate * 100:.0f}%  ·  "
            f"PF {summary.profit_factor:.2f}  ·  n={summary.closed_trades}"
        ),
        escape_markdown_v2(
            f"Expect.   {_signed_usd(_expectancy_usd(summary))}/Trade"
        ),
        "",
        f"*{escape_markdown_v2('PERFORMANCE')}*",
    ]
    windows = snapshot.windows
    if windows:
        for win in windows:
            eq_part = (
                f"  ·  Eq {_signed_usd(win.equity_delta)}"
                if win.equity_delta is not None
                else ""
            )
            wr = (
                f"{win.win_count}/{win.closed_count}"
                if win.closed_count
                else "0/0"
            )
            lines.append(
                escape_markdown_v2(
                    f"{win.label:<3}  n={win.closed_count}  "
                    f"{_signed_usd(win.closed_pnl)}"
                    f"{eq_part}"
                )
            )
            lines.append(
                escape_markdown_v2(
                    f"     W {wr}  ·  Opened {win.opened_count}"
                )
            )
    else:
        lines.append(
            escape_markdown_v2(
                f"1h   Closed {snapshot.hour_closed_count}  ·  "
                f"{_signed_usd(snapshot.hour_closed_pnl)}"
            )
        )
        lines.append(
            escape_markdown_v2(f"Opened {snapshot.hour_opened_count}")
        )
    if summary.pending_positions > 0:
        lines.append(escape_markdown_v2(f"Signal {summary.pending_positions}"))

    open_header = (
        f"OFFEN ({summary.open_positions})  "
        f"uPnL {_signed_usd(snapshot.total_open_upnl_usd)}"
    )
    lines += ["", f"*{escape_markdown_v2(open_header)}*"]
    if not snapshot.open_rows:
        lines.append(escape_markdown_v2("keine offenen Positionen"))
    else:
        for row in snapshot.open_rows[:max_open]:
            side = _short_direction(row.direction)
            if row.unrealized_usd is None:
                pnl_line = f"{_pretty_symbol(row.symbol)} {side}  uPnL n/a"
            else:
                pnl_line = (
                    f"{_pretty_symbol(row.symbol)} {side}  "
                    f"{_signed_usd(row.unrealized_usd)}"
                )
            mark_txt = format_price(row.mark, infer_price_precision(row.mark)) if row.mark is not None else "n/a"
            stop_txt = format_price(row.current_stop, infer_price_precision(row.current_stop))
            lines.append(escape_markdown_v2(pnl_line))
            lines.append(
                escape_markdown_v2(
                    f"  mark {mark_txt}  SL {stop_txt}  rem {row.rem_pct:.0f}%"
                )
            )
            lines.append(escape_markdown_v2(f"  {_tp_status(row.tp1_filled, row.tp2_filled, row.tp3_filled)}"))
        rest = len(snapshot.open_rows) - max_open
        if rest > 0:
            lines.append(escape_markdown_v2(f"+{rest} weitere"))

    lines += ["", f"*{escape_markdown_v2('CLOSES (1h)')}*"]
    if not snapshot.hour_closes:
        lines.append(escape_markdown_v2("keine Abschluesse"))
    else:
        for row in snapshot.hour_closes[:max_closes]:
            side = _short_direction(row.direction)
            reason = _EXIT_REASON_LABELS.get(row.exit_reason or "", row.exit_reason or "-")
            body = (
                f"{_pretty_symbol(row.symbol)} {side}  "
                f"{_signed_usd(row.realized_usd)}  {reason}"
            )
            lines.append(escape_markdown_v2(body))
        rest_c = len(snapshot.hour_closes) - max_closes
        if rest_c > 0:
            lines.append(escape_markdown_v2(f"+{rest_c} weitere"))

    lines += ["", f"⚠️ {escape_markdown_v2(DISCLAIMER)}"]
    return "\n".join(lines)


def format_paper_trade_close_message(
    position: PaperPosition,
    *,
    price_precision: int = 2,
    display_timezone: str = "Europe/Berlin",
) -> str:
    """Paper-Trade-Schlussmeldung."""
    try:
        direction = _DIRECTION_LABELS[SignalDirection(position.direction)]
    except ValueError:
        direction = position.direction
    symbol_label = _pretty_symbol(position.symbol)
    reason = _EXIT_REASON_LABELS.get(
        position.exit_reason or "",
        position.exit_reason or "geschlossen",
    )
    pnl = float(position.realized_pnl)
    pnl_label = f"+{format_price(pnl, 2)}" if pnl >= 0 else format_price(pnl, 2)
    lines: list[str] = [
        f"📄 *Paper-Trade geschlossen* · *{escape_markdown_v2(symbol_label)}*",
        f"*{escape_markdown_v2(direction)}* · {escape_markdown_v2(reason)}",
        "",
        escape_markdown_v2(f"Entry  {format_price(float(position.entry_price), price_precision)}"),
        escape_markdown_v2(f"PnL    {pnl_label} USDT"),
        escape_markdown_v2(
            f"Gebuehren {format_price(float(position.fees), 2)} · "
            f"Geschlossen {format_display_time(position.closed_at or position.opened_at, display_timezone)}"
        ),
        f"⚠️ {escape_markdown_v2(DISCLAIMER)}",
    ]
    return "\n".join(lines)
