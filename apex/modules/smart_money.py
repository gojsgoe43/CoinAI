"""
Smart Money Tracker Module — Order Blocks, Fair Value Gaps, Liquidity, Wyckoff

SMC (Smart Money Concepts) analysis:

Order Blocks:
  - Bullish OB: Last bearish candle before a 3+ candle bullish impulse
  - Bearish OB: Last bullish candle before a 3+ candle bearish impulse

Fair Value Gaps (FVG):
  - Bullish FVG: candle[i-1].high < candle[i+1].low  (gap to the upside)
  - Bearish FVG: candle[i-1].low  > candle[i+1].high (gap to the downside)

Liquidity Pools:
  - Equal highs / equal lows (within 0.1%) — resting liquidity
  - Buy-side liquidity: above swing highs
  - Sell-side liquidity: below swing lows

Wyckoff Phases:
  - Accumulation: down-trending price with decreasing volume on down moves,
    increasing volume on up moves, culminating in a spring/test
  - Distribution: up-trending price with increasing volume on up moves,
    decreasing on pullbacks — but then volume dries up at highs
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from apex import config
from apex.modules import ModuleSignal, Vote

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures for SMC elements
# ---------------------------------------------------------------------------

@dataclass
class OrderBlock:
    index: int
    direction: str        # 'BULLISH' or 'BEARISH'
    high: float
    low: float
    open_: float
    close_: float
    timestamp: Optional[object] = None
    active: bool = True   # becomes False when price trades through it


@dataclass
class FairValueGap:
    index: int            # index of the middle candle (i)
    direction: str        # 'BULLISH' or 'BEARISH'
    upper: float          # upper boundary of the gap
    lower: float          # lower boundary of the gap
    timestamp: Optional[object] = None
    filled: bool = False


@dataclass
class LiquidityPool:
    price: float
    side: str             # 'BUY_SIDE' (above highs) or 'SELL_SIDE' (below lows)
    touches: int = 1


# ---------------------------------------------------------------------------
# Detection algorithms
# ---------------------------------------------------------------------------

def _is_impulse(df: pd.DataFrame, start: int, direction: str, min_candles: int = 3) -> bool:
    """
    Check if there's a directional impulse of at least min_candles consecutive
    candles starting at index `start`.
    direction: 'UP' or 'DOWN'
    """
    if start + min_candles > len(df):
        return False
    for i in range(start, start + min_candles):
        if direction == "UP":
            if df["close"].iloc[i] <= df["open"].iloc[i]:
                return False
        else:
            if df["close"].iloc[i] >= df["open"].iloc[i]:
                return False
    return True


def detect_order_blocks(df: pd.DataFrame, lookback: int = 50) -> List[OrderBlock]:
    """
    Identify Order Blocks in the last `lookback` candles.

    Bullish OB: last bearish candle before a 3+ candle bullish impulse
    Bearish OB: last bullish candle before a 3+ candle bearish impulse
    """
    obs: List[OrderBlock] = []
    start_idx = max(0, len(df) - lookback - config.ORDER_BLOCK_IMPULSE_CANDLES)
    end_idx = len(df) - config.ORDER_BLOCK_IMPULSE_CANDLES

    for i in range(start_idx, end_idx):
        candle = df.iloc[i]
        is_bearish_candle = candle["close"] < candle["open"]
        is_bullish_candle = candle["close"] > candle["open"]

        # Bullish OB: bearish candle followed by bullish impulse
        if is_bearish_candle:
            if _is_impulse(df, i + 1, "UP", config.ORDER_BLOCK_IMPULSE_CANDLES):
                ts = candle["timestamp"] if "timestamp" in df.columns else None
                obs.append(OrderBlock(
                    index=i,
                    direction="BULLISH",
                    high=candle["high"],
                    low=candle["low"],
                    open_=candle["open"],
                    close_=candle["close"],
                    timestamp=ts,
                ))

        # Bearish OB: bullish candle followed by bearish impulse
        elif is_bullish_candle:
            if _is_impulse(df, i + 1, "DOWN", config.ORDER_BLOCK_IMPULSE_CANDLES):
                ts = candle["timestamp"] if "timestamp" in df.columns else None
                obs.append(OrderBlock(
                    index=i,
                    direction="BEARISH",
                    high=candle["high"],
                    low=candle["low"],
                    open_=candle["open"],
                    close_=candle["close"],
                    timestamp=ts,
                ))

    # Mark order blocks that have been traded through (invalidated)
    current_price = df["close"].iloc[-1]
    for ob in obs:
        if ob.direction == "BULLISH" and current_price < ob.low:
            ob.active = False
        elif ob.direction == "BEARISH" and current_price > ob.high:
            ob.active = False

    return obs


def detect_fvgs(df: pd.DataFrame, lookback: int = 50) -> List[FairValueGap]:
    """
    Identify Fair Value Gaps.

    Bullish FVG:  candle[i-1].high < candle[i+1].low   → gap between them
    Bearish FVG:  candle[i-1].low  > candle[i+1].high  → gap between them

    The 'middle' candle index i is the strong impulse candle.
    """
    fvgs: List[FairValueGap] = []
    start_idx = max(1, len(df) - lookback - 1)
    end_idx = len(df) - 1

    for i in range(start_idx, end_idx):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]
        nxt = df.iloc[i + 1]

        # Bullish FVG
        if prev["high"] < nxt["low"]:
            ts = curr["timestamp"] if "timestamp" in df.columns else None
            fvgs.append(FairValueGap(
                index=i,
                direction="BULLISH",
                upper=nxt["low"],
                lower=prev["high"],
                timestamp=ts,
            ))

        # Bearish FVG
        elif prev["low"] > nxt["high"]:
            ts = curr["timestamp"] if "timestamp" in df.columns else None
            fvgs.append(FairValueGap(
                index=i,
                direction="BEARISH",
                upper=prev["low"],
                lower=nxt["high"],
                timestamp=ts,
            ))

    # Mark filled FVGs
    current_price = df["close"].iloc[-1]
    for fvg in fvgs:
        if fvg.direction == "BULLISH" and current_price >= fvg.lower:
            if current_price <= fvg.upper:
                pass  # Price is inside the FVG — still relevant
            elif current_price > fvg.upper:
                fvg.filled = True
        elif fvg.direction == "BEARISH" and current_price <= fvg.upper:
            if current_price >= fvg.lower:
                pass  # Inside the FVG
            elif current_price < fvg.lower:
                fvg.filled = True

    return fvgs


def detect_liquidity_pools(
    df: pd.DataFrame, swing_lookback: int = 10, tolerance_pct: float = 0.001
) -> List[LiquidityPool]:
    """
    Identify liquidity pools — clusters of equal highs or equal lows.

    Buy-side liquidity (BSL): above equal swing highs — target for short squeezes.
    Sell-side liquidity (SSL): below equal swing lows — target for long squeezes.
    """
    pools: List[LiquidityPool] = []
    n = len(df)
    if n < swing_lookback * 2 + 1:
        return pools

    # Find swing highs and lows
    swing_highs = []
    swing_lows = []
    for i in range(swing_lookback, n - swing_lookback):
        window_h = df["high"].iloc[i - swing_lookback: i + swing_lookback + 1]
        window_l = df["low"].iloc[i - swing_lookback: i + swing_lookback + 1]
        if df["high"].iloc[i] == window_h.max():
            swing_highs.append(df["high"].iloc[i])
        if df["low"].iloc[i] == window_l.min():
            swing_lows.append(df["low"].iloc[i])

    # Cluster near-equal highs
    def cluster_levels(levels: List[float], tol: float) -> List[Tuple[float, int]]:
        if not levels:
            return []
        sorted_lvls = sorted(levels)
        clusters = []
        current_cluster = [sorted_lvls[0]]
        for lvl in sorted_lvls[1:]:
            if (lvl - current_cluster[-1]) / current_cluster[-1] <= tol:
                current_cluster.append(lvl)
            else:
                clusters.append((np.mean(current_cluster), len(current_cluster)))
                current_cluster = [lvl]
        clusters.append((np.mean(current_cluster), len(current_cluster)))
        return clusters

    high_clusters = cluster_levels(swing_highs, tolerance_pct)
    low_clusters = cluster_levels(swing_lows, tolerance_pct)

    for price, touches in high_clusters:
        if touches >= 2:  # Needs at least 2 touches to be a liquidity pool
            pools.append(LiquidityPool(price=price, side="BUY_SIDE", touches=touches))

    for price, touches in low_clusters:
        if touches >= 2:
            pools.append(LiquidityPool(price=price, side="SELL_SIDE", touches=touches))

    return pools


def detect_wyckoff_phase(df: pd.DataFrame, lookback: int = 50) -> str:
    """
    Simplified Wyckoff phase detection using volume analysis.

    Accumulation signals:
      - Price in a downtrend or flat range
      - Volume decreasing on down moves (supply drying up)
      - Volume increasing on up moves (demand appearing)
      - Spring: price briefly breaks below range then reverses with high volume

    Distribution signals:
      - Price in uptrend or at highs
      - Volume increasing on up moves initially, then diverging
      - Volume decreasing as price rises to new highs (upthrust)
      - High volume on down moves

    Returns: 'ACCUMULATION', 'DISTRIBUTION', 'MARKUP', 'MARKDOWN', 'NEUTRAL'
    """
    if len(df) < lookback:
        return "NEUTRAL"

    sub = df.iloc[-lookback:].copy()
    close = sub["close"]
    volume = sub["volume"]

    # Separate up-candles and down-candles
    up_mask = sub["close"] > sub["open"]
    down_mask = sub["close"] < sub["open"]

    up_vol_avg = volume[up_mask].mean() if up_mask.any() else 0
    down_vol_avg = volume[down_mask].mean() if down_mask.any() else 0

    # Price direction: use linear regression slope
    x = np.arange(len(close))
    if len(x) > 1:
        slope = np.polyfit(x, close.values, 1)[0]
        price_trending_up = slope > 0
        price_trending_down = slope < 0
    else:
        price_trending_up = False
        price_trending_down = False

    # Recent vs older price comparison
    first_half_close = close.iloc[: lookback // 2].mean()
    second_half_close = close.iloc[lookback // 2:].mean()
    price_rising = second_half_close > first_half_close
    price_falling = second_half_close < first_half_close

    # Volume trend (is volume on up moves > volume on down moves?)
    volume_bullish = up_vol_avg > down_vol_avg

    # Check for spring: recent low below prior range with recovery
    range_low = close.iloc[:-5].min() if len(close) > 5 else close.min()
    recent_low = close.iloc[-5:].min()
    current_price = close.iloc[-1]
    spring_detected = (recent_low < range_low) and (current_price > range_low)

    if price_falling and volume_bullish and not price_trending_down:
        return "ACCUMULATION"
    elif spring_detected and volume_bullish:
        return "ACCUMULATION"
    elif price_rising and volume_bullish and price_trending_up:
        return "MARKUP"
    elif price_rising and not volume_bullish:
        return "DISTRIBUTION"
    elif price_falling and not volume_bullish and price_trending_down:
        return "MARKDOWN"

    return "NEUTRAL"


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------

class SmartMoneyTracker:
    """
    Analyses Smart Money Concepts: Order Blocks, FVGs, Liquidity, Wyckoff.
    """

    NAME = "Smart Money Tracker"

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
            Multi-timeframe data. Runs SMC on 4H for higher-quality structures.
        """
        if df is None or len(df) < 30:
            return ModuleSignal(
                module_name=self.NAME,
                vote=Vote.NEUTRAL,
                confidence=0.0,
                reasons=["Insufficient data for SMC analysis"],
            )

        current_price = df["close"].iloc[-1]
        reasons = []
        long_score = 0
        short_score = 0
        max_score = 8

        # ---- Order Blocks ----
        obs = detect_order_blocks(df, lookback=config.ORDER_BLOCK_LOOKBACK)
        active_obs = [ob for ob in obs if ob.active]
        bullish_obs = [ob for ob in active_obs if ob.direction == "BULLISH"]
        bearish_obs = [ob for ob in active_obs if ob.direction == "BEARISH"]

        # Check if price is at or near an active order block
        ob_proximity_pct = 0.005  # 0.5% proximity threshold

        price_in_bull_ob = any(
            ob.low <= current_price <= ob.high * (1 + ob_proximity_pct)
            for ob in bullish_obs[-3:]  # Check most recent 3
        )
        price_in_bear_ob = any(
            ob.low * (1 - ob_proximity_pct) <= current_price <= ob.high
            for ob in bearish_obs[-3:]
        )

        reasons.append(
            f"Order Blocks: {len(bullish_obs)} bullish, {len(bearish_obs)} bearish active"
        )
        if price_in_bull_ob:
            long_score += 2
            reasons.append("Price at/near active bullish order block — institutional demand zone")
        elif bullish_obs:
            nearest_bull_ob = min(bullish_obs, key=lambda ob: abs(ob.high - current_price))
            dist_pct = abs(nearest_bull_ob.high - current_price) / current_price * 100
            reasons.append(f"Nearest bullish OB: {dist_pct:.2f}% away at {nearest_bull_ob.high:.4f}")

        if price_in_bear_ob:
            short_score += 2
            reasons.append("Price at/near active bearish order block — institutional supply zone")
        elif bearish_obs:
            nearest_bear_ob = min(bearish_obs, key=lambda ob: abs(ob.low - current_price))
            dist_pct = abs(nearest_bear_ob.low - current_price) / current_price * 100
            reasons.append(f"Nearest bearish OB: {dist_pct:.2f}% away at {nearest_bear_ob.low:.4f}")

        # ---- Fair Value Gaps ----
        fvgs = detect_fvgs(df, lookback=config.FVG_LOOKBACK)
        active_fvgs = [fvg for fvg in fvgs if not fvg.filled]
        bull_fvgs = [fvg for fvg in active_fvgs if fvg.direction == "BULLISH"]
        bear_fvgs = [fvg for fvg in active_fvgs if fvg.direction == "BEARISH"]

        reasons.append(f"FVGs: {len(bull_fvgs)} bullish, {len(bear_fvgs)} bearish active")

        # Price inside FVG or approaching from the right side
        in_bull_fvg = any(fvg.lower <= current_price <= fvg.upper for fvg in bull_fvgs[-5:])
        in_bear_fvg = any(fvg.lower <= current_price <= fvg.upper for fvg in bear_fvgs[-5:])
        # Price approaching bullish FVG from above (pulled back into it)
        approaching_bull_fvg = any(
            current_price <= fvg.upper * 1.002 and current_price >= fvg.lower * 0.998
            for fvg in bull_fvgs[-5:]
        )
        approaching_bear_fvg = any(
            current_price >= fvg.lower * 0.998 and current_price <= fvg.upper * 1.002
            for fvg in bear_fvgs[-5:]
        )

        if in_bull_fvg or approaching_bull_fvg:
            long_score += 2
            reasons.append("Price inside/at bullish FVG — likely to be filled (long bias)")
        if in_bear_fvg or approaching_bear_fvg:
            short_score += 2
            reasons.append("Price inside/at bearish FVG — likely to be filled (short bias)")

        # ---- Liquidity Pools ----
        pools = detect_liquidity_pools(df, swing_lookback=config.SWING_LOOKBACK)
        buy_side = [p for p in pools if p.side == "BUY_SIDE"]
        sell_side = [p for p in pools if p.side == "SELL_SIDE"]

        reasons.append(f"Liquidity pools: {len(buy_side)} buy-side, {len(sell_side)} sell-side")

        # Is price likely hunting buy-side liquidity (bearish sweep) or approaching it?
        if buy_side:
            closest_bsl = min(buy_side, key=lambda p: abs(p.price - current_price))
            bsl_dist_pct = (closest_bsl.price - current_price) / current_price * 100
            if 0 < bsl_dist_pct < 1.0:
                long_score += 1
                reasons.append(
                    f"Buy-side liquidity at {closest_bsl.price:.4f} ({bsl_dist_pct:.2f}% above) — "
                    "price may run to sweep it (short-term long target)"
                )
            elif bsl_dist_pct < 0:  # Price already above BSL
                reasons.append(f"Price above buy-side liquidity at {closest_bsl.price:.4f} — BSL swept")

        if sell_side:
            closest_ssl = min(sell_side, key=lambda p: abs(p.price - current_price))
            ssl_dist_pct = (current_price - closest_ssl.price) / current_price * 100
            if 0 < ssl_dist_pct < 1.0:
                short_score += 1
                reasons.append(
                    f"Sell-side liquidity at {closest_ssl.price:.4f} ({ssl_dist_pct:.2f}% below) — "
                    "price may sweep it (short-term short target)"
                )
            elif ssl_dist_pct < 0:  # Price already below SSL
                reasons.append(f"Price below sell-side liquidity at {closest_ssl.price:.4f} — SSL swept")

        # ---- Wyckoff Phase ----
        wyckoff_phase = detect_wyckoff_phase(df, lookback=50)
        reasons.append(f"Wyckoff phase: {wyckoff_phase}")

        if wyckoff_phase in ("ACCUMULATION", "MARKUP"):
            long_score += 1
            reasons.append(f"Wyckoff {wyckoff_phase} — bullish institutional footprint")
        elif wyckoff_phase in ("DISTRIBUTION", "MARKDOWN"):
            short_score += 1
            reasons.append(f"Wyckoff {wyckoff_phase} — bearish institutional footprint")

        # ---- Also run on 4H if available ----
        if timeframes:
            df_4h = timeframes.get("4h")
            if df_4h is not None and len(df_4h) >= 30:
                wyckoff_4h = detect_wyckoff_phase(df_4h, lookback=50)
                reasons.append(f"4H Wyckoff: {wyckoff_4h}")
                if wyckoff_4h in ("ACCUMULATION", "MARKUP"):
                    long_score += 1
                    reasons.append(f"4H confirms bullish Wyckoff: {wyckoff_4h}")
                elif wyckoff_4h in ("DISTRIBUTION", "MARKDOWN"):
                    short_score += 1
                    reasons.append(f"4H confirms bearish Wyckoff: {wyckoff_4h}")
                max_score += 1

        # ---- Final vote ----
        if long_score > short_score and long_score >= 3:
            vote = Vote.LONG
            confidence = min(100.0, (long_score / max_score) * 100)
        elif short_score > long_score and short_score >= 3:
            vote = Vote.SHORT
            confidence = min(100.0, (short_score / max_score) * 100)
        else:
            vote = Vote.NEUTRAL
            confidence = max(0.0, 50.0 - abs(long_score - short_score) * 8)
            reasons.append(f"Mixed SMC signals: long={long_score}, short={short_score}")

        logger.debug(
            "SmartMoney: vote=%s conf=%.1f long=%d short=%d wyckoff=%s",
            vote, confidence, long_score, short_score, wyckoff_phase,
        )
        return ModuleSignal(
            module_name=self.NAME,
            vote=vote,
            confidence=round(confidence, 1),
            reasons=reasons,
        )
