"""
Signal Engine — Confluence aggregation, signal generation, level calculation.

Orchestrates the 4 strategy modules and combines their votes into a
structured trading signal with entry zone, SL, and TP levels.

Confluence rules:
  - 4/4 modules LONG or SHORT → Grade A+ (full size, up to 10x leverage)
  - 3/4 modules LONG or SHORT → Grade A  (standard size, up to 7x leverage)
  - <3/4 → NO TRADE

Entry protocol:
  - Split entry: 70% at primary zone, 30% at secondary (deeper)
  - Limit orders only
  - SL: beyond structure (ATR-based, avoiding round numbers)
  - TP1: 40% at 1:1.5 R:R
  - TP2: 35% at 1:3 R:R
  - TP3: 25% trailing
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from apex import config
from apex.modules import ModuleSignal, Vote
from apex.modules.trend_master import TrendMaster
from apex.modules.momentum_scanner import MomentumScanner
from apex.modules.smart_money import SmartMoneyTracker
from apex.modules.market_structure import MarketStructureAnalyst

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal dataclass
# ---------------------------------------------------------------------------

@dataclass
class ApexSignal:
    """A fully structured APEX trading signal."""
    # Core info
    pair: str
    direction: str                  # 'LONG' or 'SHORT'
    grade: str                      # 'A+' or 'A'
    confidence: float               # 0–100 aggregate confidence

    # Market context
    timeframe: str = "1h"
    market_condition: str = "TRENDING"

    # Entry levels
    entry_primary: float = 0.0      # 70% fill price
    entry_secondary: float = 0.0    # 30% fill price (deeper)
    stop_loss: float = 0.0
    tp1: float = 0.0                # 40% at 1.5R
    tp2: float = 0.0                # 35% at 3R
    tp3: float = 0.0                # 25% trailing
    leverage: int = 1
    rr_ratio: float = 0.0           # Actual R:R to TP2

    # Module votes
    module_signals: List[ModuleSignal] = field(default_factory=list)
    vote_tally: Dict[str, int] = field(default_factory=dict)

    # Metadata
    atr: float = 0.0
    key_risks: List[str] = field(default_factory=list)
    analysis_summary: str = ""

    # Flag for validity
    valid: bool = True


@dataclass
class NoTradeSignal:
    """Returned when confluence is insufficient for a trade."""
    pair: str
    reason: str
    module_signals: List[ModuleSignal] = field(default_factory=list)
    vote_tally: Dict[str, int] = field(default_factory=dict)
    valid: bool = False


# ---------------------------------------------------------------------------
# Helper calculations
# ---------------------------------------------------------------------------

def _calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Average True Range."""
    if len(df) < period + 1:
        return df["close"].iloc[-1] * 0.005  # fallback: 0.5% of price

    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return float(tr.rolling(window=period).mean().iloc[-1])


def _detect_market_condition(df: pd.DataFrame, atr: float) -> str:
    """
    Classify current market regime:
      TRENDING, RANGING, HIGH_VOLATILITY, PRE_EVENT (not detectable from OHLCV alone)
    """
    if len(df) < 30:
        return "TRENDING"

    close = df["close"]

    # ADX proxy — use price range relative to ATR
    # High volatility: current ATR > 2x rolling 20-period ATR average
    rolling_atr = []
    for i in range(max(0, len(df) - 20), len(df)):
        sub = df.iloc[max(0, i - 14):i + 1]
        if len(sub) >= 5:
            rolling_atr.append(_calculate_atr(sub, period=min(14, len(sub) - 1)))

    if rolling_atr:
        avg_atr = np.mean(rolling_atr)
        if avg_atr > 0 and atr > avg_atr * config.HIGH_VOLATILITY_ATR_MULTIPLIER:
            return "HIGH_VOLATILITY"

    # Simple trend vs range: compare price range to ATR * period
    recent = close.iloc[-20:]
    price_range = recent.max() - recent.min()
    atr_range = atr * 10

    if price_range < atr_range * 0.5:
        return "RANGING"

    return "TRENDING"


def _avoid_round_number(price: float, direction: str, buffer_pct: float = 0.001) -> float:
    """
    Nudge a stop-loss price away from round numbers.
    Round numbers: prices ending in 000, 500, 00 at meaningful magnitude.
    """
    # Find the magnitude of the number
    magnitude = 10 ** (len(str(int(price))) - 2)  # e.g. 50000 → magnitude 1000

    # Check if near a round number
    nearest_round = round(price / magnitude) * magnitude
    distance_to_round = abs(price - nearest_round)
    threshold = magnitude * 0.5

    if distance_to_round < threshold * 0.1:  # within 10% of half-magnitude
        # Push SL further away from round number
        if direction == "LONG":
            # SL is below price — push it lower
            return nearest_round - magnitude * buffer_pct * 10
        else:
            # SL is above price — push it higher
            return nearest_round + magnitude * buffer_pct * 10

    return price


def _calculate_levels(
    direction: str,
    entry_price: float,
    atr: float,
    df: pd.DataFrame,
    atr_sl_multiplier: float = 1.5,
) -> Tuple[float, float, float, float, float, float]:
    """
    Calculate entry zone, SL, and TP levels.

    Returns:
        (entry_primary, entry_secondary, stop_loss, tp1, tp2, tp3)

    Strategy:
      - SL: entry ± (ATR * multiplier), placed beyond structure
      - Entry secondary: 30% fill at slightly deeper price
      - TP1: 1.5R from average entry
      - TP2: 3.0R from average entry
      - TP3: calculated from TP2 as trailing reference
    """
    risk = atr * atr_sl_multiplier

    if direction == "LONG":
        stop_loss = entry_price - risk
        stop_loss = _avoid_round_number(stop_loss, "LONG")

        # Secondary entry is lower (better price)
        entry_secondary = entry_price - atr * 0.3
        avg_entry = entry_price * config.ENTRY_SPLIT_PRIMARY + entry_secondary * config.ENTRY_SPLIT_SECONDARY
        actual_risk = avg_entry - stop_loss

        tp1 = avg_entry + actual_risk * config.TP1_RR
        tp2 = avg_entry + actual_risk * config.TP2_RR
        tp3 = avg_entry + actual_risk * config.TP2_RR * 1.5  # extended trailing target
    else:
        stop_loss = entry_price + risk
        stop_loss = _avoid_round_number(stop_loss, "SHORT")

        # Secondary entry is higher (better price for short)
        entry_secondary = entry_price + atr * 0.3
        avg_entry = entry_price * config.ENTRY_SPLIT_PRIMARY + entry_secondary * config.ENTRY_SPLIT_SECONDARY
        actual_risk = stop_loss - avg_entry

        tp1 = avg_entry - actual_risk * config.TP1_RR
        tp2 = avg_entry - actual_risk * config.TP2_RR
        tp3 = avg_entry - actual_risk * config.TP2_RR * 1.5

    return entry_price, entry_secondary, stop_loss, tp1, tp2, tp3


# ---------------------------------------------------------------------------
# Signal Engine
# ---------------------------------------------------------------------------

class SignalEngine:
    """
    Aggregates 4 module votes and generates structured APEX signals.
    """

    def __init__(self) -> None:
        self._trend_master = TrendMaster()
        self._momentum_scanner = MomentumScanner()
        self._smart_money = SmartMoneyTracker()
        self._market_structure = MarketStructureAnalyst()

    def analyse(
        self,
        symbol: str,
        timeframes: Dict[str, pd.DataFrame],
        risk_is_high_vol: bool = False,
    ) -> "ApexSignal | NoTradeSignal":
        """
        Run full APEX analysis on a symbol.

        Parameters
        ----------
        symbol : str
            Trading pair e.g. 'BTC/USDT:USDT'
        timeframes : dict
            {tf_string: DataFrame} for all relevant timeframes
        risk_is_high_vol : bool
            Override high-volatility flag from risk manager

        Returns
        -------
        ApexSignal if confluence threshold met, else NoTradeSignal
        """
        # Select primary timeframe for analysis (1H for entry signals)
        df_1h = timeframes.get("1h", pd.DataFrame())
        df_4h = timeframes.get("4h", pd.DataFrame())
        df_1d = timeframes.get("1d", pd.DataFrame())
        df_15m = timeframes.get("15m", pd.DataFrame())

        # Prefer 1H, fall back to 15m or 4H if not available
        if df_1h is None or df_1h.empty:
            df_primary = df_15m if (df_15m is not None and not df_15m.empty) else df_4h
        else:
            df_primary = df_1h

        if df_primary is None or df_primary.empty:
            logger.warning("No usable OHLCV data for %s", symbol)
            return NoTradeSignal(
                pair=symbol,
                reason="No usable OHLCV data available",
            )

        # ---- Run all 4 modules ----
        logger.info("Analysing %s — running 4 strategy modules...", symbol)

        sig_trend = self._trend_master.analyse(df_primary, timeframes)
        sig_momentum = self._momentum_scanner.analyse(df_primary, timeframes)
        sig_smart = self._smart_money.analyse(df_primary, timeframes)
        sig_structure = self._market_structure.analyse(df_primary, timeframes)

        module_signals = [sig_trend, sig_momentum, sig_smart, sig_structure]

        for sig in module_signals:
            logger.debug("  %s", sig)

        # ---- Tally votes ----
        vote_tally = {v.value: 0 for v in Vote}
        for sig in module_signals:
            vote_tally[sig.vote.value] += 1

        long_votes = vote_tally[Vote.LONG.value]
        short_votes = vote_tally[Vote.SHORT.value]
        neutral_votes = vote_tally[Vote.NEUTRAL.value]

        logger.info(
            "%s votes — LONG: %d, SHORT: %d, NEUTRAL: %d",
            symbol, long_votes, short_votes, neutral_votes,
        )

        # ---- Determine direction and grade ----
        if long_votes >= config.GRADE_A_PLUS_MIN_VOTES:
            direction = "LONG"
            grade = "A+"
        elif short_votes >= config.GRADE_A_PLUS_MIN_VOTES:
            direction = "SHORT"
            grade = "A+"
        elif long_votes >= config.GRADE_A_MIN_VOTES:
            direction = "LONG"
            grade = "A"
        elif short_votes >= config.GRADE_A_MIN_VOTES:
            direction = "SHORT"
            grade = "A"
        else:
            return NoTradeSignal(
                pair=symbol,
                reason=(
                    f"Insufficient confluence: LONG={long_votes}, SHORT={short_votes}, "
                    f"NEUTRAL={neutral_votes} — need 3+ aligned votes"
                ),
                module_signals=module_signals,
                vote_tally=vote_tally,
            )

        # ---- Aggregate confidence ----
        aligned_signals = [
            s for s in module_signals
            if s.vote.value == direction
        ]
        avg_confidence = (
            sum(s.confidence for s in aligned_signals) / len(aligned_signals)
            if aligned_signals else 0.0
        )

        # ---- Market condition ----
        atr = _calculate_atr(df_primary, period=14)
        market_condition = _detect_market_condition(df_primary, atr)

        if market_condition == "HIGH_VOLATILITY":
            risk_is_high_vol = True

        # ---- Current price ----
        current_price = float(df_primary["close"].iloc[-1])

        # ---- Calculate levels ----
        # Use ATR multiplier based on market condition
        sl_multiplier = 2.0 if market_condition == "HIGH_VOLATILITY" else 1.5

        entry_primary, entry_secondary, stop_loss, tp1, tp2, tp3 = _calculate_levels(
            direction=direction,
            entry_price=current_price,
            atr=atr,
            df=df_primary,
            atr_sl_multiplier=sl_multiplier,
        )

        # ---- Validate R:R ----
        if direction == "LONG":
            risk_distance = entry_primary - stop_loss
            reward_distance = tp2 - entry_primary
        else:
            risk_distance = stop_loss - entry_primary
            reward_distance = entry_primary - tp2

        rr_ratio = reward_distance / risk_distance if risk_distance > 0 else 0.0

        if rr_ratio < config.MIN_RR:
            return NoTradeSignal(
                pair=symbol,
                reason=(
                    f"R:R too low: {rr_ratio:.2f} < minimum {config.MIN_RR:.1f}. "
                    f"Volatility too high or SL too close."
                ),
                module_signals=module_signals,
                vote_tally=vote_tally,
            )

        # ---- Leverage ----
        from apex.risk_manager import RiskManager
        rm = RiskManager()  # Temporary instance just for leverage calc
        leverage = rm.determine_leverage(grade, risk_is_high_vol)

        # ---- Key risks ----
        key_risks = _identify_key_risks(
            direction=direction,
            grade=grade,
            market_condition=market_condition,
            module_signals=module_signals,
            neutral_votes=neutral_votes,
        )

        # ---- Analysis summary ----
        summary = _build_summary(
            symbol=symbol,
            direction=direction,
            grade=grade,
            market_condition=market_condition,
            module_signals=module_signals,
            long_votes=long_votes,
            short_votes=short_votes,
        )

        signal = ApexSignal(
            pair=symbol,
            direction=direction,
            grade=grade,
            confidence=round(avg_confidence, 1),
            timeframe="1h",
            market_condition=market_condition,
            entry_primary=round(entry_primary, 6),
            entry_secondary=round(entry_secondary, 6),
            stop_loss=round(stop_loss, 6),
            tp1=round(tp1, 6),
            tp2=round(tp2, 6),
            tp3=round(tp3, 6),
            leverage=leverage,
            rr_ratio=round(rr_ratio, 2),
            module_signals=module_signals,
            vote_tally=vote_tally,
            atr=round(atr, 6),
            key_risks=key_risks,
            analysis_summary=summary,
            valid=True,
        )

        logger.info(
            "Signal generated: %s %s %s | Entry: %.4f | SL: %.4f | TP2: %.4f | R:R: %.2f",
            symbol, direction, grade, entry_primary, stop_loss, tp2, rr_ratio,
        )

        return signal


# ---------------------------------------------------------------------------
# Helper: build key risks
# ---------------------------------------------------------------------------

def _identify_key_risks(
    direction: str,
    grade: str,
    market_condition: str,
    module_signals: List[ModuleSignal],
    neutral_votes: int,
) -> List[str]:
    risks = []

    if grade == "A" and neutral_votes > 0:
        risks.append(f"{neutral_votes} module(s) voted NEUTRAL — partial confluence only")

    if market_condition == "HIGH_VOLATILITY":
        risks.append("High volatility environment — wider spreads and unexpected wicks")
    elif market_condition == "RANGING":
        risks.append("Ranging market — false breakouts possible, tighten TP targets")

    # Check for any module with low confidence
    weak_modules = [s for s in module_signals if s.vote.value == direction and s.confidence < 50]
    for m in weak_modules:
        risks.append(f"{m.module_name} has low confidence ({m.confidence:.0f}%)")

    if not risks:
        risks.append("Standard execution risk — monitor closely near TP/SL levels")

    return risks


def _build_summary(
    symbol: str,
    direction: str,
    grade: str,
    market_condition: str,
    module_signals: List[ModuleSignal],
    long_votes: int,
    short_votes: int,
) -> str:
    aligned = [s for s in module_signals if s.vote.value == direction]
    top_reasons = []
    for sig in aligned[:2]:  # Top 2 modules' first reason
        if sig.reasons:
            top_reasons.append(f"[{sig.module_name}] {sig.reasons[0]}")

    summary = (
        f"Grade {grade} {direction} setup on {symbol} with {max(long_votes, short_votes)}/4 "
        f"module alignment. Market condition: {market_condition}. "
        + " | ".join(top_reasons)
    )
    return summary
