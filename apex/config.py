"""
APEX Configuration and Settings
"""

import os
import logging
from dataclasses import dataclass, field
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ---------------------------------------------------------------------------
# API Credentials
# ---------------------------------------------------------------------------

BITGET_API_KEY = os.getenv("BITGET_API_KEY", "")
BITGET_SECRET = os.getenv("BITGET_SECRET", "")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE", "")


# ---------------------------------------------------------------------------
# Exchange Settings
# ---------------------------------------------------------------------------

EXCHANGE_ID = "bitget"
DEFAULT_MARKET_TYPE = "swap"   # USDT perpetual futures

# Top 20 USDT perpetual pairs to scan (updated periodically by volume)
TOP_PAIRS: List[str] = [
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
    "DOGE/USDT:USDT",
    "ADA/USDT:USDT",
    "AVAX/USDT:USDT",
    "LINK/USDT:USDT",
    "DOT/USDT:USDT",
    "MATIC/USDT:USDT",
    "LTC/USDT:USDT",
    "UNI/USDT:USDT",
    "ATOM/USDT:USDT",
    "ETC/USDT:USDT",
    "FIL/USDT:USDT",
    "NEAR/USDT:USDT",
    "ARB/USDT:USDT",
    "OP/USDT:USDT",
    "SUI/USDT:USDT",
]


# ---------------------------------------------------------------------------
# Timeframe Settings
# ---------------------------------------------------------------------------

TIMEFRAMES = {
    "htf_macro": "1d",    # Higher time-frame macro bias
    "htf_trend": "4h",    # Higher time-frame trend confirmation
    "mtf": "1h",          # Mid time-frame trend direction
    "ltf_entry": "15m",   # Lower time-frame entry signal
    "ltf_fine": "5m",     # Fine-tune entry
}

# Number of candles to fetch per timeframe
CANDLE_LIMIT = {
    "1d": 200,
    "4h": 200,
    "1h": 200,
    "15m": 200,
    "5m": 100,
}


# ---------------------------------------------------------------------------
# Indicator Parameters
# ---------------------------------------------------------------------------

# Trend Master
EMA_FAST = 21
EMA_MID = 55
EMA_SLOW = 200
ADX_PERIOD = 14
ADX_TREND_THRESHOLD = 25
ADX_RANGE_THRESHOLD = 20

# Momentum Scanner
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
VOLUME_SURGE_MULTIPLIER = 1.5
VOLUME_AVERAGE_PERIOD = 20
STOCH_RSI_PERIOD = 14
STOCH_RSI_SMOOTH_K = 3
STOCH_RSI_SMOOTH_D = 3

# Smart Money
ORDER_BLOCK_LOOKBACK = 50
ORDER_BLOCK_IMPULSE_CANDLES = 3    # consecutive candles to define impulse
FVG_LOOKBACK = 50

# Market Structure
SWING_LOOKBACK = 10      # bars each side to define a swing
BOS_LOOKBACK = 50
FIBONACCI_LEVELS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]


# ---------------------------------------------------------------------------
# Risk Management
# ---------------------------------------------------------------------------

MAX_RISK_PER_TRADE = 0.01          # 1 % of account equity per trade
MAX_OPEN_POSITIONS = 5
DAILY_DRAWDOWN_HARD_STOP = 0.03    # 3 %
WEEKLY_DRAWDOWN_HARD_STOP = 0.07   # 7 %

# Leverage caps by grade
LEVERAGE_A_PLUS = 10
LEVERAGE_A = 7
LEVERAGE_VOLATILE = 5              # used when volatility is high
LEVERAGE_MAX_ABSOLUTE = 15

# Entry split
ENTRY_SPLIT_PRIMARY = 0.70         # 70 % at first entry zone
ENTRY_SPLIT_SECONDARY = 0.30       # 30 % at secondary (deeper) entry

# Take-profit allocation
TP1_FRACTION = 0.40   # close 40 % at TP1
TP2_FRACTION = 0.35   # close 35 % at TP2
TP3_FRACTION = 0.25   # trail remainder

# Minimum risk-to-reward
MIN_RR = 2.0

# TP multipliers (multiples of initial risk)
TP1_RR = 1.5
TP2_RR = 3.0


# ---------------------------------------------------------------------------
# Signal Grading
# ---------------------------------------------------------------------------

GRADE_A_PLUS_MIN_VOTES = 4   # 4/4 modules agree
GRADE_A_MIN_VOTES = 3        # 3/4 modules agree


# ---------------------------------------------------------------------------
# Market Condition Thresholds
# ---------------------------------------------------------------------------

HIGH_VOLATILITY_ATR_MULTIPLIER = 2.0   # ATR > 2x 20-period avg = high vol
PRE_EVENT_HOURS_BUFFER = 4             # Hours before major event to avoid


# ---------------------------------------------------------------------------
# Cache / Performance
# ---------------------------------------------------------------------------

DATA_CACHE_TTL_SECONDS = 60    # Re-fetch candles if older than 60 s


# ---------------------------------------------------------------------------
# Dataclass for runtime state
# ---------------------------------------------------------------------------

@dataclass
class RuntimeConfig:
    """Mutable runtime configuration injected at startup."""
    account_balance: float = 10_000.0   # USDT — updated from exchange
    open_positions: int = 0
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    daily_start_equity: float = 10_000.0
    weekly_start_equity: float = 10_000.0
    trading_enabled: bool = True
    halt_reason: Optional[str] = None
