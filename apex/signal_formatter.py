"""
Signal Formatter — Format ApexSignal output in the APEX structured signal format.

Produces both console-friendly coloured output and plain-text versions
suitable for logging or Telegram/Discord notifications.
"""

import logging
from datetime import datetime, timezone
from typing import Union

from apex.signal_engine import ApexSignal, NoTradeSignal
from apex import config

logger = logging.getLogger(__name__)

# Try to import colorama for coloured terminal output
try:
    from colorama import Fore, Style, init as colorama_init
    colorama_init(autoreset=True)
    _COLORAMA_AVAILABLE = True
except ImportError:
    _COLORAMA_AVAILABLE = False
    # Provide no-op stubs
    class _NoColor:
        def __getattr__(self, _): return ""
    Fore = Style = _NoColor()


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _color(text: str, color_code: str) -> str:
    if _COLORAMA_AVAILABLE:
        return f"{color_code}{text}{Style.RESET_ALL}"
    return text


def _direction_color(direction: str) -> str:
    if direction == "LONG":
        return Fore.GREEN
    elif direction == "SHORT":
        return Fore.RED
    return Fore.YELLOW


def _grade_color(grade: str) -> str:
    if grade == "A+":
        return Fore.CYAN
    return Fore.WHITE


def _confidence_bar(confidence: float, width: int = 20) -> str:
    filled = int(confidence / 100 * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {confidence:.0f}%"


def _format_price(price: float, symbol: str = "") -> str:
    """Format price with appropriate decimal places."""
    if price >= 1000:
        return f"{price:,.2f}"
    elif price >= 1:
        return f"{price:.4f}"
    else:
        return f"{price:.6f}"


def _tp_label(fraction: float, rr: float) -> str:
    return f"({fraction*100:.0f}% @ {rr}R)"


# ---------------------------------------------------------------------------
# Main formatters
# ---------------------------------------------------------------------------

def format_signal(
    signal: Union[ApexSignal, NoTradeSignal],
    use_color: bool = True,
    compact: bool = False,
) -> str:
    """
    Format a signal for terminal display.

    Parameters
    ----------
    signal : ApexSignal or NoTradeSignal
    use_color : bool
        Apply colorama colors for terminal output.
    compact : bool
        Short one-line summary instead of full display.
    """
    if not signal.valid:
        return _format_no_trade(signal, use_color)

    if compact:
        return _format_compact(signal, use_color)

    return _format_full(signal, use_color)


def _format_no_trade(signal: NoTradeSignal, use_color: bool) -> str:
    lines = []
    sep = "─" * 60

    pair = signal.pair.replace(":USDT", "").replace("/USDT", "/USDT")
    header = f"  NO TRADE — {pair}  "
    lines.append(sep)
    lines.append(header.center(60))
    lines.append(sep)
    lines.append(f"  Reason: {signal.reason}")

    if signal.module_signals:
        lines.append("")
        lines.append("  Module Votes:")
        for ms in signal.module_signals:
            vote_str = ms.vote.value
            if use_color and _COLORAMA_AVAILABLE:
                if ms.vote.value == "LONG":
                    vote_str = _color(vote_str, Fore.GREEN)
                elif ms.vote.value == "SHORT":
                    vote_str = _color(vote_str, Fore.RED)
                else:
                    vote_str = _color(vote_str, Fore.YELLOW)
            lines.append(f"    • {ms.module_name:<26} {vote_str} ({ms.confidence:.0f}%)")

    if hasattr(signal, "vote_tally") and signal.vote_tally:
        tally = signal.vote_tally
        lines.append(f"\n  Tally: LONG={tally.get('LONG', 0)} | SHORT={tally.get('SHORT', 0)} | NEUTRAL={tally.get('NEUTRAL', 0)}")

    lines.append(sep)
    return "\n".join(lines)


def _format_compact(signal: ApexSignal, use_color: bool) -> str:
    dir_str = signal.direction
    if use_color and _COLORAMA_AVAILABLE:
        dir_str = _color(dir_str, _direction_color(signal.direction))

    grade_str = signal.grade
    if use_color and _COLORAMA_AVAILABLE:
        grade_str = _color(grade_str, _grade_color(signal.grade))

    pair = signal.pair.replace(":USDT", "")
    return (
        f"{pair} {dir_str} {grade_str} | "
        f"Entry: {_format_price(signal.entry_primary)} | "
        f"SL: {_format_price(signal.stop_loss)} | "
        f"TP2: {_format_price(signal.tp2)} | "
        f"R:R {signal.rr_ratio:.1f} | "
        f"Conf: {signal.confidence:.0f}% | "
        f"Lev: {signal.leverage}x"
    )


def _format_full(signal: ApexSignal, use_color: bool) -> str:
    lines = []
    wide_sep = "═" * 68
    thin_sep = "─" * 68
    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    pair = signal.pair.replace(":USDT", "")
    dir_str = signal.direction
    grade_str = signal.grade

    if use_color and _COLORAMA_AVAILABLE:
        dir_str = _color(signal.direction, _direction_color(signal.direction))
        grade_str = _color(signal.grade, _grade_color(signal.grade))

    # ---- Header ----
    lines.append(wide_sep)
    lines.append(f"  ⚡ APEX SIGNAL  —  {pair}  —  {now_str}")
    lines.append(wide_sep)

    # ---- Direction / Grade / Confidence ----
    lines.append(f"  Direction  : {dir_str}")
    lines.append(f"  Grade      : {grade_str}  ({['3/4', '4/4'][signal.grade == 'A+']} confluence)")
    lines.append(f"  Confidence : {_confidence_bar(signal.confidence)}")
    lines.append(f"  Timeframe  : {signal.timeframe}  |  Condition: {signal.market_condition}")
    lines.append(thin_sep)

    # ---- Entry Zone ----
    lines.append("  ENTRY ZONE")
    lines.append(f"    Primary   (70%) : {_format_price(signal.entry_primary, pair)}")
    lines.append(f"    Secondary (30%) : {_format_price(signal.entry_secondary, pair)}")
    lines.append(thin_sep)

    # ---- Risk Levels ----
    lines.append("  RISK LEVELS")
    lines.append(f"    Stop Loss       : {_format_price(signal.stop_loss, pair)}")
    lines.append(f"    TP1 {_tp_label(config.TP1_FRACTION, config.TP1_RR):18}: {_format_price(signal.tp1, pair)}")
    lines.append(f"    TP2 {_tp_label(config.TP2_FRACTION, config.TP2_RR):18}: {_format_price(signal.tp2, pair)}")
    lines.append(f"    TP3 (25% trail) : {_format_price(signal.tp3, pair)}")
    lines.append(f"    ATR (14)        : {_format_price(signal.atr, pair)}")
    lines.append(thin_sep)

    # ---- Trade Parameters ----
    lines.append("  TRADE PARAMETERS")
    lines.append(f"    Leverage        : {signal.leverage}x")
    lines.append(f"    R:R Ratio       : {signal.rr_ratio:.2f}:1  (min: {config.MIN_RR}:1)")
    lines.append(thin_sep)

    # ---- Module Votes ----
    lines.append("  MODULE VOTES")
    vote_icons = {"LONG": "▲", "SHORT": "▼", "NEUTRAL": "◆"}
    for ms in signal.module_signals:
        vote_str = f"{vote_icons.get(ms.vote.value, '?')} {ms.vote.value}"
        if use_color and _COLORAMA_AVAILABLE:
            if ms.vote.value == "LONG":
                vote_str = _color(vote_str, Fore.GREEN)
            elif ms.vote.value == "SHORT":
                vote_str = _color(vote_str, Fore.RED)
            else:
                vote_str = _color(vote_str, Fore.YELLOW)

        conf_bar = _confidence_bar(ms.confidence, width=12)
        lines.append(f"    {ms.module_name:<28} {vote_str:<20} {conf_bar}")

    tally = signal.vote_tally
    lines.append(
        f"\n    Tally: LONG={tally.get('LONG', 0)} | "
        f"SHORT={tally.get('SHORT', 0)} | "
        f"NEUTRAL={tally.get('NEUTRAL', 0)}"
    )
    lines.append(thin_sep)

    # ---- Analysis Summary ----
    lines.append("  ANALYSIS SUMMARY")
    # Word-wrap at ~64 chars
    summary = signal.analysis_summary
    words = summary.split()
    current_line = "    "
    for word in words:
        if len(current_line) + len(word) + 1 > 68:
            lines.append(current_line)
            current_line = "    " + word + " "
        else:
            current_line += word + " "
    if current_line.strip():
        lines.append(current_line)
    lines.append(thin_sep)

    # ---- Key Risks ----
    lines.append("  KEY RISKS")
    for risk in signal.key_risks:
        lines.append(f"    ⚠ {risk}")
    lines.append(wide_sep)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Plain-text variant (for logging / notifications)
# ---------------------------------------------------------------------------

def format_signal_plain(signal: Union[ApexSignal, NoTradeSignal]) -> str:
    """Return a plain-text (no ANSI codes) signal representation."""
    return format_signal(signal, use_color=False)


# ---------------------------------------------------------------------------
# JSON / dict export
# ---------------------------------------------------------------------------

def signal_to_dict(signal: Union[ApexSignal, NoTradeSignal]) -> dict:
    """
    Convert a signal to a plain dictionary suitable for JSON serialisation
    or downstream processing.
    """
    if not signal.valid:
        return {
            "valid": False,
            "pair": signal.pair,
            "reason": signal.reason,
            "module_votes": {
                ms.module_name: {
                    "vote": ms.vote.value,
                    "confidence": ms.confidence,
                    "reasons": ms.reasons,
                }
                for ms in signal.module_signals
            },
            "vote_tally": signal.vote_tally,
        }

    return {
        "valid": True,
        "pair": signal.pair,
        "direction": signal.direction,
        "grade": signal.grade,
        "confidence": signal.confidence,
        "timeframe": signal.timeframe,
        "market_condition": signal.market_condition,
        "entry": {
            "primary": signal.entry_primary,
            "secondary": signal.entry_secondary,
        },
        "stop_loss": signal.stop_loss,
        "take_profits": {
            "tp1": {"price": signal.tp1, "fraction": config.TP1_FRACTION, "rr": config.TP1_RR},
            "tp2": {"price": signal.tp2, "fraction": config.TP2_FRACTION, "rr": config.TP2_RR},
            "tp3": {"price": signal.tp3, "fraction": 0.25, "type": "trailing"},
        },
        "leverage": signal.leverage,
        "rr_ratio": signal.rr_ratio,
        "atr": signal.atr,
        "module_votes": {
            ms.module_name: {
                "vote": ms.vote.value,
                "confidence": ms.confidence,
                "reasons": ms.reasons,
            }
            for ms in signal.module_signals
        },
        "vote_tally": signal.vote_tally,
        "analysis_summary": signal.analysis_summary,
        "key_risks": signal.key_risks,
    }
