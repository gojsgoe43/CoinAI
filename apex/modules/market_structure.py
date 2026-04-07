"""
Market Structure Analyst Module — BOS/CHoCH, S/R Mapping, Fibonacci Retracement

BOS (Break of Structure):
  - Bullish BOS: Price closes beyond the most recent swing high
  - Bearish BOS: Price closes beyond the most recent swing low

CHoCH (Change of Character):
  - After a series of Higher Highs (uptrend): price breaks below the most
    recent Higher Low → bearish CHoCH (momentum shift)
  - After a series of Lower Lows (downtrend): price breaks above the most
    recent Lower High → bullish CHoCH

Support / Resistance:
  - Swing highs → resistance levels
  - Swing lows  → support levels
  - Cluster swing levels within tolerance

Fibonacci Retracement:
  - Identify last significant swing (high→low or low→high)
  - Map standard Fib levels: 23.6%, 38.2%, 50%, 61.8%, 78.6%
  - Check if current price is near key Fib confluence
"""

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from apex import config
from apex.modules import ModuleSignal, Vote

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Swing high / low detection
# ---------------------------------------------------------------------------

def _find_swings(
    df: pd.DataFrame, lookback: int = 10
) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    """
    Find swing highs and swing lows.

    A swing high: df.high[i] is the max in a window of ±lookback bars.
    A swing low:  df.low[i]  is the min in a window of ±lookback bars.

    Returns (swing_highs, swing_lows) — each is a list of (index, price).
    """
    n = len(df)
    swing_highs: List[Tuple[int, float]] = []
    swing_lows: List[Tuple[int, float]] = []

    for i in range(lookback, n - lookback):
        window_start = i - lookback
        window_end = i + lookback + 1
        high_window = df["high"].iloc[window_start:window_end]
        low_window = df["low"].iloc[window_start:window_end]

        if df["high"].iloc[i] == high_window.max():
            swing_highs.append((i, df["high"].iloc[i]))
        if df["low"].iloc[i] == low_window.min():
            swing_lows.append((i, df["low"].iloc[i]))

    return swing_highs, swing_lows


def _detect_bos(
    df: pd.DataFrame,
    swing_highs: List[Tuple[int, float]],
    swing_lows: List[Tuple[int, float]],
    lookback: int = 50,
) -> Tuple[str, Optional[float]]:
    """
    Detect the most recent Break of Structure.

    Returns (direction, level) where direction is 'BULLISH', 'BEARISH', or 'NONE'.
    """
    if not swing_highs and not swing_lows:
        return "NONE", None

    current_close = df["close"].iloc[-1]

    # Filter swings within lookback
    recent_start = len(df) - lookback
    recent_highs = [(i, p) for i, p in swing_highs if i >= recent_start]
    recent_lows = [(i, p) for i, p in swing_lows if i >= recent_start]

    # Bullish BOS: close above most recent swing high
    if recent_highs:
        last_swing_high_idx, last_swing_high_price = recent_highs[-1]
        # Check if any candle after the swing high closed above it
        if len(df) > last_swing_high_idx + 1:
            post_swing = df["close"].iloc[last_swing_high_idx + 1:]
            if (post_swing > last_swing_high_price).any():
                return "BULLISH", last_swing_high_price

    # Bearish BOS: close below most recent swing low
    if recent_lows:
        last_swing_low_idx, last_swing_low_price = recent_lows[-1]
        if len(df) > last_swing_low_idx + 1:
            post_swing = df["close"].iloc[last_swing_low_idx + 1:]
            if (post_swing < last_swing_low_price).any():
                return "BEARISH", last_swing_low_price

    return "NONE", None


def _detect_choch(
    df: pd.DataFrame,
    swing_highs: List[Tuple[int, float]],
    swing_lows: List[Tuple[int, float]],
    lookback: int = 50,
) -> Tuple[str, Optional[float]]:
    """
    Detect Change of Character.

    Bearish CHoCH: uptrend (series of HH) → price breaks below most recent HL
    Bullish CHoCH: downtrend (series of LL) → price breaks above most recent LH

    Returns (direction, level).
    """
    if len(swing_highs) < 3 or len(swing_lows) < 3:
        return "NONE", None

    recent_start = len(df) - lookback
    recent_highs = [(i, p) for i, p in swing_highs if i >= recent_start]
    recent_lows = [(i, p) for i, p in swing_lows if i >= recent_start]

    current_close = df["close"].iloc[-1]

    # Detect uptrend: at least 2 consecutively higher swing highs
    if len(recent_highs) >= 2:
        is_uptrend = all(
            recent_highs[j][1] > recent_highs[j - 1][1]
            for j in range(1, min(3, len(recent_highs)))
        )
        if is_uptrend and recent_lows:
            # Most recent higher low
            last_hl_price = recent_lows[-1][1]
            # Check if current close broke below it
            if current_close < last_hl_price:
                return "BEARISH", last_hl_price

    # Detect downtrend: at least 2 consecutively lower swing lows
    if len(recent_lows) >= 2:
        is_downtrend = all(
            recent_lows[j][1] < recent_lows[j - 1][1]
            for j in range(1, min(3, len(recent_lows)))
        )
        if is_downtrend and recent_highs:
            # Most recent lower high
            last_lh_price = recent_highs[-1][1]
            if current_close > last_lh_price:
                return "BULLISH", last_lh_price

    return "NONE", None


def _get_sr_levels(
    swing_highs: List[Tuple[int, float]],
    swing_lows: List[Tuple[int, float]],
    tolerance_pct: float = 0.003,
    lookback: int = 50,
    df_len: int = 200,
) -> Tuple[List[float], List[float]]:
    """
    Cluster swing highs → resistance levels, swing lows → support levels.
    Returns (supports, resistances).
    """
    recent_start = df_len - lookback

    def cluster(levels: List[float], tol: float) -> List[float]:
        if not levels:
            return []
        sorted_lvls = sorted(levels)
        clusters: List[List[float]] = [[sorted_lvls[0]]]
        for lvl in sorted_lvls[1:]:
            if (lvl - clusters[-1][-1]) / clusters[-1][-1] <= tol:
                clusters[-1].append(lvl)
            else:
                clusters.append([lvl])
        return [float(np.mean(c)) for c in clusters if len(c) >= 1]

    recent_highs = [p for i, p in swing_highs if i >= recent_start]
    recent_lows = [p for i, p in swing_lows if i >= recent_start]

    resistances = cluster(recent_highs, tolerance_pct)
    supports = cluster(recent_lows, tolerance_pct)

    return supports, resistances


def _fibonacci_levels(swing_low: float, swing_high: float) -> Dict[float, float]:
    """
    Calculate standard Fibonacci retracement levels from swing_low to swing_high.
    """
    diff = swing_high - swing_low
    return {
        level: swing_high - level * diff
        for level in config.FIBONACCI_LEVELS
    }


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    """Average True Range — used to determine proximity threshold."""
    high = df["high"]
    low = df["low"]
    close = df["close"].shift(1)
    tr = pd.concat(
        [high - low, (high - close).abs(), (low - close).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(window=period).mean().iloc[-1]


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------

class MarketStructureAnalyst:
    """
    Analyses market structure: BOS, CHoCH, support/resistance, Fibonacci.
    """

    NAME = "Market Structure Analyst"

    def analyse(
        self,
        df: pd.DataFrame,
        timeframes: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> ModuleSignal:
        """
        Parameters
        ----------
        df : DataFrame
            Primary timeframe OHLCV (1H recommended).
        timeframes : dict, optional
            Multi-timeframe data for HTF structure confirmation.
        """
        if df is None or len(df) < config.SWING_LOOKBACK * 3:
            return ModuleSignal(
                module_name=self.NAME,
                vote=Vote.NEUTRAL,
                confidence=0.0,
                reasons=["Insufficient data for market structure analysis"],
            )

        current_price = df["close"].iloc[-1]
        reasons = []
        long_score = 0
        short_score = 0
        max_score = 8

        # ---- Swing points ----
        swing_highs, swing_lows = _find_swings(df, lookback=config.SWING_LOOKBACK)

        # ---- BOS detection ----
        bos_dir, bos_level = _detect_bos(df, swing_highs, swing_lows, lookback=config.BOS_LOOKBACK)
        reasons.append(f"BOS: {bos_dir}" + (f" at {bos_level:.4f}" if bos_level else ""))

        if bos_dir == "BULLISH":
            long_score += 2
            reasons.append(f"Bullish BOS — price broke above swing high at {bos_level:.4f}, structure turned bullish")
        elif bos_dir == "BEARISH":
            short_score += 2
            reasons.append(f"Bearish BOS — price broke below swing low at {bos_level:.4f}, structure turned bearish")

        # ---- CHoCH detection ----
        choch_dir, choch_level = _detect_choch(df, swing_highs, swing_lows, lookback=config.BOS_LOOKBACK)
        if choch_dir != "NONE":
            reasons.append(f"CHoCH: {choch_dir} at {choch_level:.4f}")
            if choch_dir == "BULLISH":
                long_score += 2
                reasons.append("Bullish CHoCH — downtrend broken, first sign of reversal to upside")
            elif choch_dir == "BEARISH":
                short_score += 2
                reasons.append("Bearish CHoCH — uptrend broken, momentum shift to downside")
        else:
            reasons.append("No CHoCH detected")

        # ---- Support/Resistance levels ----
        supports, resistances = _get_sr_levels(
            swing_highs, swing_lows, tolerance_pct=0.003,
            lookback=config.BOS_LOOKBACK, df_len=len(df)
        )

        # ATR for proximity check
        try:
            atr_val = _atr(df)
        except Exception:
            atr_val = current_price * 0.005

        proximity_threshold = atr_val * 0.5  # within half ATR

        # Check if price is near support
        near_support = any(abs(current_price - s) <= proximity_threshold for s in supports)
        near_resistance = any(abs(current_price - r) <= proximity_threshold for r in resistances)

        if supports:
            closest_support = min(supports, key=lambda s: abs(s - current_price))
            reasons.append(f"Nearest support: {closest_support:.4f} (distance: {abs(current_price - closest_support):.4f})")
        if resistances:
            closest_resistance = min(resistances, key=lambda r: abs(r - current_price))
            reasons.append(f"Nearest resistance: {closest_resistance:.4f} (distance: {abs(current_price - closest_resistance):.4f})")

        if near_support and not near_resistance:
            long_score += 1
            reasons.append("Price at key support level — potential bounce zone")
        elif near_resistance and not near_support:
            short_score += 1
            reasons.append("Price at key resistance level — potential rejection zone")
        elif near_support and near_resistance:
            reasons.append("Price between support and resistance — compression zone")

        # ---- Fibonacci retracement ----
        # Use the last significant swing
        if swing_highs and swing_lows:
            all_swings = [(i, p, "H") for i, p in swing_highs] + [(i, p, "L") for i, p in swing_lows]
            all_swings.sort(key=lambda x: x[0])

            if len(all_swings) >= 2:
                # Find the last significant swing range
                recent_swings = all_swings[-10:]
                if recent_swings:
                    max_swing = max(recent_swings, key=lambda x: x[1])
                    min_swing = min(recent_swings, key=lambda x: x[1])
                    swing_high_val = max_swing[1]
                    swing_low_val = min_swing[1]

                    if swing_high_val > swing_low_val:
                        fib_levels = _fibonacci_levels(swing_low_val, swing_high_val)

                        # Check which Fibonacci level current price is near
                        for fib_ratio, fib_price in fib_levels.items():
                            if fib_ratio in (0.0, 1.0):
                                continue
                            if abs(current_price - fib_price) <= proximity_threshold:
                                # Determine bullish or bearish context
                                most_recent_swing = all_swings[-1]
                                was_upswing = most_recent_swing[2] == "H"

                                if was_upswing:
                                    # Retracement after upswing — support at Fib
                                    long_score += 1
                                    reasons.append(
                                        f"Price at Fibonacci {fib_ratio*100:.1f}% retracement "
                                        f"({fib_price:.4f}) — potential bullish support"
                                    )
                                else:
                                    # Retracement after downswing — resistance at Fib
                                    short_score += 1
                                    reasons.append(
                                        f"Price at Fibonacci {fib_ratio*100:.1f}% retracement "
                                        f"({fib_price:.4f}) — potential bearish resistance"
                                    )
                                break
                        else:
                            # No Fib level confluence
                            key_fibs = {k: v for k, v in fib_levels.items() if k in (0.382, 0.5, 0.618)}
                            if key_fibs:
                                closest_fib = min(key_fibs.items(), key=lambda x: abs(x[1] - current_price))
                                reasons.append(
                                    f"Closest key Fib ({closest_fib[0]*100:.1f}%) at {closest_fib[1]:.4f} "
                                    f"({abs(closest_fib[1] - current_price):.4f} away)"
                                )

        # ---- HTF Structure (4H) ----
        if timeframes:
            df_4h = timeframes.get("4h")
            if df_4h is not None and len(df_4h) >= config.SWING_LOOKBACK * 3:
                sh_4h, sl_4h = _find_swings(df_4h, lookback=config.SWING_LOOKBACK)
                bos_4h_dir, bos_4h_level = _detect_bos(df_4h, sh_4h, sl_4h, lookback=config.BOS_LOOKBACK)

                if bos_4h_dir != "NONE":
                    reasons.append(f"4H BOS: {bos_4h_dir} at {bos_4h_level:.4f}")
                    if bos_4h_dir == "BULLISH":
                        long_score += 1
                        max_score += 1
                        reasons.append("4H bullish BOS confirms higher-timeframe bullish structure")
                    elif bos_4h_dir == "BEARISH":
                        short_score += 1
                        max_score += 1
                        reasons.append("4H bearish BOS confirms higher-timeframe bearish structure")

        # ---- Trend continuation vs reversal context ----
        # Check HH/HL or LL/LH pattern using recent swings
        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            last_two_highs = swing_highs[-2:]
            last_two_lows = swing_lows[-2:]
            higher_highs = last_two_highs[-1][1] > last_two_highs[-2][1]
            higher_lows = last_two_lows[-1][1] > last_two_lows[-2][1]
            lower_lows = last_two_lows[-1][1] < last_two_lows[-2][1]
            lower_highs = last_two_highs[-1][1] < last_two_highs[-2][1]

            if higher_highs and higher_lows:
                long_score += 1
                reasons.append("Higher Highs + Higher Lows pattern — uptrend intact")
            elif lower_lows and lower_highs:
                short_score += 1
                reasons.append("Lower Lows + Lower Highs pattern — downtrend intact")

        # ---- Final vote ----
        if long_score > short_score and long_score >= 2:
            vote = Vote.LONG
            confidence = min(100.0, (long_score / max_score) * 100)
        elif short_score > long_score and short_score >= 2:
            vote = Vote.SHORT
            confidence = min(100.0, (short_score / max_score) * 100)
        else:
            vote = Vote.NEUTRAL
            confidence = max(0.0, 50.0 - abs(long_score - short_score) * 8)
            reasons.append(f"Unclear market structure: long={long_score}, short={short_score}")

        logger.debug(
            "MarketStructure: vote=%s conf=%.1f long=%d short=%d bos=%s choch=%s",
            vote, confidence, long_score, short_score, bos_dir, choch_dir,
        )
        return ModuleSignal(
            module_name=self.NAME,
            vote=vote,
            confidence=round(confidence, 1),
            reasons=reasons,
        )
