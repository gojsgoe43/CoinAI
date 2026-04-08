"""
Trend Master Module — EMA 21/55/200, ADX, Higher Timeframe Bias

Votes LONG when:
  - Price is above EMA21 > EMA55 > EMA200 (bullish alignment)
  - ADX > 25 (strong trend present)
  - HTF bias agrees (4H and 1D both bullish)

Votes SHORT for mirror bearish conditions.
Votes NEUTRAL when trend is weak or mixed.
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

def _ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def _calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average Directional Index (ADX) using Wilder's smoothing.
    Returns a Series of ADX values.
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]

    # True Range
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # Directional Movement
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    dm_plus = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    dm_minus = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    dm_plus_s = pd.Series(dm_plus, index=df.index)
    dm_minus_s = pd.Series(dm_minus, index=df.index)

    # Wilder smoothing (RMA)
    def wilder_smooth(series: pd.Series, n: int) -> pd.Series:
        result = series.copy().astype(float)
        result.iloc[:n] = np.nan
        first_valid = series.iloc[:n].sum()
        result.iloc[n - 1] = first_valid
        for i in range(n, len(series)):
            result.iloc[i] = result.iloc[i - 1] - (result.iloc[i - 1] / n) + series.iloc[i]
        return result

    atr_s = wilder_smooth(tr, period)
    dm_plus_sm = wilder_smooth(dm_plus_s, period)
    dm_minus_sm = wilder_smooth(dm_minus_s, period)

    di_plus = 100 * dm_plus_sm / atr_s.replace(0, np.nan)
    di_minus = 100 * dm_minus_sm / atr_s.replace(0, np.nan)

    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    adx = wilder_smooth(dx.fillna(0), period)

    return adx


def _htf_bias(df: pd.DataFrame) -> str:
    """
    Determine higher-timeframe directional bias using EMA alignment.
    Returns 'BULLISH', 'BEARISH', or 'NEUTRAL'.
    """
    if df is None or len(df) < config.EMA_SLOW:
        return "NEUTRAL"

    close = df["close"]
    ema21 = _ema(close, config.EMA_FAST).iloc[-1]
    ema55 = _ema(close, config.EMA_MID).iloc[-1]
    ema200 = _ema(close, config.EMA_SLOW).iloc[-1]
    price = close.iloc[-1]

    if price > ema21 > ema55 > ema200:
        return "BULLISH"
    elif price < ema21 < ema55 < ema200:
        return "BEARISH"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------

class TrendMaster:
    """
    Analyses multi-timeframe EMA structure and ADX to determine trend direction.
    """

    NAME = "Trend Master"

    def analyse(
        self,
        df_ltf: pd.DataFrame,           # primary / LTF (1h or 15m)
        timeframes: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> ModuleSignal:
        """
        Parameters
        ----------
        df_ltf : DataFrame
            The primary entry-timeframe OHLCV data (1H).
        timeframes : dict, optional
            Dict of {tf_string: DataFrame} for all available timeframes.
            Used for HTF bias extraction.
        """
        timeframes = timeframes or {}

        if df_ltf is None or len(df_ltf) < config.EMA_SLOW:
            return ModuleSignal(
                module_name=self.NAME,
                vote=Vote.NEUTRAL,
                confidence=0.0,
                reasons=["Insufficient data for EMA-200"],
            )

        close = df_ltf["close"]
        ema21 = _ema(close, config.EMA_FAST)
        ema55 = _ema(close, config.EMA_MID)
        ema200 = _ema(close, config.EMA_SLOW)

        price = close.iloc[-1]
        e21 = ema21.iloc[-1]
        e55 = ema55.iloc[-1]
        e200 = ema200.iloc[-1]

        # ADX on primary timeframe
        adx_series = _calculate_adx(df_ltf, config.ADX_PERIOD)
        adx_val = adx_series.dropna().iloc[-1] if not adx_series.dropna().empty else 0.0

        # HTF biases
        df_4h = timeframes.get("4h", pd.DataFrame())
        df_1d = timeframes.get("1d", pd.DataFrame())
        bias_4h = _htf_bias(df_4h)
        bias_1d = _htf_bias(df_1d)

        reasons = []
        long_score = 0
        short_score = 0
        max_score = 6   # EMA alignment (3pts) + ADX (1pt) + 4H (1pt) + 1D (1pt)

        # ---- EMA alignment check ----
        bullish_ema = price > e21 > e55 > e200
        bearish_ema = price < e21 < e55 < e200
        partial_bull = (price > e55) and not bullish_ema
        partial_bear = (price < e55) and not bearish_ema

        if bullish_ema:
            long_score += 3
            reasons.append(f"Full bullish EMA stack (price={price:.4f} > EMA21={e21:.4f} > EMA55={e55:.4f} > EMA200={e200:.4f})")
        elif bearish_ema:
            short_score += 3
            reasons.append(f"Full bearish EMA stack (price={price:.4f} < EMA21={e21:.4f} < EMA55={e55:.4f} < EMA200={e200:.4f})")
        elif partial_bull:
            long_score += 1
            reasons.append(f"Partial bullish EMA (price > EMA55 but not full stack)")
        elif partial_bear:
            short_score += 1
            reasons.append(f"Partial bearish EMA (price < EMA55 but not full stack)")
        else:
            reasons.append("EMA structure is mixed/choppy")

        # ---- ADX check ----
        trend_label = "strong trend" if adx_val >= config.ADX_TREND_THRESHOLD else "weak trend/range"
        reasons.append(f"ADX({config.ADX_PERIOD})={adx_val:.1f} — {trend_label}")

        if adx_val >= config.ADX_TREND_THRESHOLD:
            # Amplifies whichever direction EMA supports
            if bullish_ema:
                long_score += 1
            elif bearish_ema:
                short_score += 1
        # If ADX < threshold: no directional score, but reasons noted

        # ---- HTF bias ----
        for bias, label in [(bias_4h, "4H"), (bias_1d, "1D")]:
            reasons.append(f"HTF {label} bias: {bias}")
            if bias == "BULLISH":
                long_score += 1
            elif bias == "BEARISH":
                short_score += 1

        # ---- Determine vote and confidence ----
        if long_score > short_score and long_score >= 3 and adx_val >= config.ADX_RANGE_THRESHOLD:
            vote = Vote.LONG
            confidence = min(100.0, (long_score / max_score) * 100)
        elif short_score > long_score and short_score >= 3 and adx_val >= config.ADX_RANGE_THRESHOLD:
            vote = Vote.SHORT
            confidence = min(100.0, (short_score / max_score) * 100)
        else:
            vote = Vote.NEUTRAL
            confidence = max(0.0, 50.0 - abs(long_score - short_score) * 10)
            if adx_val < config.ADX_RANGE_THRESHOLD:
                reasons.append(f"ADX {adx_val:.1f} < {config.ADX_RANGE_THRESHOLD} — ranging market, no strong trend")

        logger.debug(
            "TrendMaster: vote=%s conf=%.1f long=%d short=%d adx=%.1f",
            vote, confidence, long_score, short_score, adx_val,
        )
        return ModuleSignal(
            module_name=self.NAME,
            vote=vote,
            confidence=round(confidence, 1),
            reasons=reasons,
        )
