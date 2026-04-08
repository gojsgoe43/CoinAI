"""
Momentum Scanner Module — RSI divergence, MACD histogram, Volume surge, StochRSI

Votes LONG when:
  - RSI showing bullish divergence or exiting oversold (< 30 → recovering)
  - MACD histogram turning positive / histogram rising
  - Volume surge > 1.5x 20-period average on bullish candles
  - StochRSI in oversold zone and turning up

Votes SHORT for mirror conditions.
Votes NEUTRAL when signals are mixed.
"""

import logging
from typing import Dict, Optional

import numpy as np
import pandas as pd

from apex import config
from apex.modules import ModuleSignal, Vote

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Indicator calculations
# ---------------------------------------------------------------------------

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI using Wilder's smoothing (RMA)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    # Wilder RMA
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(close: pd.Series, fast: int, slow: int, signal: int):
    """
    Returns (macd_line, signal_line, histogram) as pd.Series.
    """
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _stoch_rsi(close: pd.Series, rsi_period: int = 14, stoch_period: int = 14,
               smooth_k: int = 3, smooth_d: int = 3):
    """
    Stochastic RSI: apply stochastic formula to RSI values.
    Returns (K, D) as pd.Series.
    """
    rsi_vals = _rsi(close, rsi_period)
    rsi_min = rsi_vals.rolling(window=stoch_period).min()
    rsi_max = rsi_vals.rolling(window=stoch_period).max()

    denom = (rsi_max - rsi_min).replace(0, np.nan)
    stoch_raw = 100 * (rsi_vals - rsi_min) / denom

    k = stoch_raw.rolling(window=smooth_k).mean()
    d = k.rolling(window=smooth_d).mean()
    return k, d


def _detect_rsi_divergence(df: pd.DataFrame, rsi_series: pd.Series, lookback: int = 20) -> str:
    """
    Detect bullish or bearish RSI divergence.

    Bullish divergence: price makes lower low but RSI makes higher low.
    Bearish divergence: price makes higher high but RSI makes lower high.

    Returns 'BULLISH', 'BEARISH', or 'NONE'.
    """
    if len(df) < lookback + 5:
        return "NONE"

    close = df["close"]
    recent_close = close.iloc[-lookback:]
    recent_rsi = rsi_series.iloc[-lookback:].dropna()

    if len(recent_rsi) < 10:
        return "NONE"

    # Find local swing lows in price (for bullish divergence check)
    # A swing low: low[i] < low[i-1] and low[i] < low[i+1]
    lows_price = []
    lows_rsi = []
    highs_price = []
    highs_rsi = []

    for i in range(2, len(recent_close) - 1):
        idx = recent_close.index[i]
        if idx not in recent_rsi.index:
            continue
        p_val = recent_close.iloc[i]
        r_val = recent_rsi.loc[idx] if idx in recent_rsi.index else np.nan
        if np.isnan(r_val):
            continue
        p_prev = recent_close.iloc[i - 1]
        p_next = recent_close.iloc[i + 1]

        if p_val < p_prev and p_val < p_next:
            lows_price.append(p_val)
            lows_rsi.append(r_val)
        if p_val > p_prev and p_val > p_next:
            highs_price.append(p_val)
            highs_rsi.append(r_val)

    # Bullish divergence: last two lows — price lower, RSI higher
    if len(lows_price) >= 2:
        if lows_price[-1] < lows_price[-2] and lows_rsi[-1] > lows_rsi[-2]:
            return "BULLISH"

    # Bearish divergence: last two highs — price higher, RSI lower
    if len(highs_price) >= 2:
        if highs_price[-1] > highs_price[-2] and highs_rsi[-1] < highs_rsi[-2]:
            return "BEARISH"

    return "NONE"


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------

class MomentumScanner:
    """
    Analyses RSI, MACD, Volume, and StochRSI to gauge momentum direction.
    """

    NAME = "Momentum Scanner"

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
            Additional timeframes (not heavily used by this module but accepted
            for a consistent interface).
        """
        if df is None or len(df) < 50:
            return ModuleSignal(
                module_name=self.NAME,
                vote=Vote.NEUTRAL,
                confidence=0.0,
                reasons=["Insufficient data for momentum analysis"],
            )

        close = df["close"]
        volume = df["volume"]

        # ---- RSI ----
        rsi_series = _rsi(close, config.RSI_PERIOD)
        rsi_val = rsi_series.dropna().iloc[-1] if not rsi_series.dropna().empty else 50.0
        rsi_prev = rsi_series.dropna().iloc[-2] if len(rsi_series.dropna()) >= 2 else rsi_val

        # ---- MACD ----
        macd_line, signal_line, histogram = _macd(
            close, config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL
        )
        hist_val = histogram.dropna().iloc[-1] if not histogram.dropna().empty else 0.0
        hist_prev = histogram.dropna().iloc[-2] if len(histogram.dropna()) >= 2 else hist_val
        hist_prev2 = histogram.dropna().iloc[-3] if len(histogram.dropna()) >= 3 else hist_prev

        # ---- Volume surge ----
        vol_avg = volume.rolling(window=config.VOLUME_AVERAGE_PERIOD).mean().iloc[-1]
        vol_current = volume.iloc[-1]
        vol_surge = vol_current > (vol_avg * config.VOLUME_SURGE_MULTIPLIER)
        vol_ratio = vol_current / vol_avg if vol_avg > 0 else 1.0

        # Direction of last candle for volume context
        last_bullish = df["close"].iloc[-1] > df["open"].iloc[-1]
        last_bearish = df["close"].iloc[-1] < df["open"].iloc[-1]

        # ---- StochRSI ----
        stoch_k, stoch_d = _stoch_rsi(
            close,
            rsi_period=config.STOCH_RSI_PERIOD,
            stoch_period=config.STOCH_RSI_PERIOD,
            smooth_k=config.STOCH_RSI_SMOOTH_K,
            smooth_d=config.STOCH_RSI_SMOOTH_D,
        )
        sk_val = stoch_k.dropna().iloc[-1] if not stoch_k.dropna().empty else 50.0
        sd_val = stoch_d.dropna().iloc[-1] if not stoch_d.dropna().empty else 50.0
        sk_prev = stoch_k.dropna().iloc[-2] if len(stoch_k.dropna()) >= 2 else sk_val

        # ---- RSI Divergence ----
        rsi_div = _detect_rsi_divergence(df, rsi_series, lookback=20)

        # ---- Scoring ----
        reasons = []
        long_score = 0
        short_score = 0
        max_score = 8

        # --- RSI ---
        reasons.append(f"RSI({config.RSI_PERIOD})={rsi_val:.1f}")
        if rsi_val < config.RSI_OVERSOLD:
            long_score += 2
            reasons.append(f"RSI oversold (<{config.RSI_OVERSOLD}) — potential bullish reversal")
        elif config.RSI_OVERSOLD <= rsi_val < 45:
            long_score += 1
            reasons.append("RSI recovering from oversold territory")
        elif rsi_val > config.RSI_OVERBOUGHT:
            short_score += 2
            reasons.append(f"RSI overbought (>{config.RSI_OVERBOUGHT}) — potential bearish reversal")
        elif 55 < rsi_val <= config.RSI_OVERBOUGHT:
            short_score += 1
            reasons.append("RSI approaching overbought territory")
        elif 45 <= rsi_val <= 55:
            reasons.append("RSI neutral zone")

        # RSI divergence bonus
        if rsi_div == "BULLISH":
            long_score += 1
            reasons.append("Bullish RSI divergence detected (lower price low, higher RSI low)")
        elif rsi_div == "BEARISH":
            short_score += 1
            reasons.append("Bearish RSI divergence detected (higher price high, lower RSI high)")

        # --- MACD histogram ---
        hist_rising = hist_val > hist_prev
        hist_accelerating = hist_prev > hist_prev2

        reasons.append(f"MACD histogram={hist_val:.6f} (prev={hist_prev:.6f})")
        if hist_val > 0 and hist_rising:
            long_score += 2
            reasons.append("MACD histogram positive and rising — bullish momentum")
        elif hist_val > 0 and not hist_rising:
            long_score += 1
            reasons.append("MACD histogram positive but decelerating")
        elif hist_val < 0 and not hist_rising:
            short_score += 2
            reasons.append("MACD histogram negative and falling — bearish momentum")
        elif hist_val < 0 and hist_rising:
            short_score += 1
            reasons.append("MACD histogram negative but recovering")

        # --- Volume surge ---
        reasons.append(f"Volume ratio={vol_ratio:.2f}x 20-period avg")
        if vol_surge:
            if last_bullish:
                long_score += 1
                reasons.append(f"Volume surge {vol_ratio:.1f}x avg on bullish candle — buying pressure")
            elif last_bearish:
                short_score += 1
                reasons.append(f"Volume surge {vol_ratio:.1f}x avg on bearish candle — selling pressure")
            else:
                reasons.append(f"Volume surge {vol_ratio:.1f}x avg (indecisive candle)")
        else:
            reasons.append("No volume surge detected")

        # --- StochRSI ---
        reasons.append(f"StochRSI K={sk_val:.1f}, D={sd_val:.1f}")
        stoch_turning_up = sk_val > sk_prev and sk_val < 20
        stoch_turning_down = sk_val < sk_prev and sk_val > 80
        k_cross_above_d = sk_val > sd_val and sk_prev <= sd_val  # K crossed above D
        k_cross_below_d = sk_val < sd_val and sk_prev >= sd_val  # K crossed below D

        if sk_val < 20 or stoch_turning_up:
            long_score += 1
            reasons.append("StochRSI oversold (<20) or turning up — potential long entry")
        elif k_cross_above_d and sk_val < 50:
            long_score += 1
            reasons.append("StochRSI K crossed above D in lower half — bullish signal")
        elif sk_val > 80 or stoch_turning_down:
            short_score += 1
            reasons.append("StochRSI overbought (>80) or turning down — potential short entry")
        elif k_cross_below_d and sk_val > 50:
            short_score += 1
            reasons.append("StochRSI K crossed below D in upper half — bearish signal")

        # ---- Final vote ----
        if long_score > short_score and long_score >= 3:
            vote = Vote.LONG
            confidence = min(100.0, (long_score / max_score) * 100)
        elif short_score > long_score and short_score >= 3:
            vote = Vote.SHORT
            confidence = min(100.0, (short_score / max_score) * 100)
        else:
            vote = Vote.NEUTRAL
            confidence = max(0.0, 50.0 - abs(long_score - short_score) * 5)
            reasons.append(f"Mixed signals: long_score={long_score}, short_score={short_score}")

        logger.debug(
            "MomentumScanner: vote=%s conf=%.1f rsi=%.1f macd_hist=%.6f vol_ratio=%.2f sk=%.1f",
            vote, confidence, rsi_val, hist_val, vol_ratio, sk_val,
        )
        return ModuleSignal(
            module_name=self.NAME,
            vote=vote,
            confidence=round(confidence, 1),
            reasons=reasons,
        )
