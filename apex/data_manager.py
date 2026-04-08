"""
Data Manager — Market data fetching with in-memory TTL cache.

The DataManager wraps the BitgetClient and caches OHLCV DataFrames
so that multiple modules can reuse the same candle data within one
scan cycle without redundant API calls.
"""

import logging
import time
from typing import Dict, Optional, Tuple

import pandas as pd

from apex.bitget_client import BitgetClient
from apex import config

logger = logging.getLogger(__name__)

# Cache entry: (DataFrame, timestamp_fetched)
_CacheEntry = Tuple[pd.DataFrame, float]


class DataManager:
    """
    Manages OHLCV data fetching and caching for APEX.

    Usage:
        dm = DataManager(client)
        df_1h = dm.get_ohlcv("BTC/USDT:USDT", "1h")
        mtf   = dm.get_multi_timeframe("BTC/USDT:USDT")
    """

    def __init__(self, client: BitgetClient, cache_ttl: int = config.DATA_CACHE_TTL_SECONDS) -> None:
        self._client = client
        self._ttl = cache_ttl
        # cache keyed by (symbol, timeframe)
        self._cache: Dict[Tuple[str, str], _CacheEntry] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: Optional[int] = None,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """
        Return OHLCV DataFrame for the given symbol/timeframe.
        Uses cache unless expired or force_refresh is True.
        """
        key = (symbol, timeframe)
        now = time.time()

        if not force_refresh and key in self._cache:
            df, fetched_at = self._cache[key]
            if now - fetched_at < self._ttl:
                logger.debug("Cache hit: %s %s (%d rows)", symbol, timeframe, len(df))
                return df.copy()

        candle_limit = limit or config.CANDLE_LIMIT.get(timeframe, 200)
        df = self._client.fetch_ohlcv(symbol, timeframe, limit=candle_limit)
        self._cache[key] = (df, now)
        return df.copy()

    def get_multi_timeframe(
        self,
        symbol: str,
        force_refresh: bool = False,
    ) -> Dict[str, pd.DataFrame]:
        """
        Return a dict of DataFrames for every APEX timeframe.
        Keys are the timeframe strings (e.g. "1d", "4h", "1h", "15m", "5m").
        """
        result: Dict[str, pd.DataFrame] = {}
        for _tf_name, tf_str in config.TIMEFRAMES.items():
            result[tf_str] = self.get_ohlcv(symbol, tf_str, force_refresh=force_refresh)
        return result

    def get_top_pairs(self, n: int = 20, force_refresh: bool = False) -> list:
        """Return the top-N pairs by 24 h USDT volume."""
        cache_key = ("__top_pairs__", str(n))
        now = time.time()

        if not force_refresh and cache_key in self._cache:
            # Re-use the stored list encoded as a dummy DataFrame with one column
            stored_df, fetched_at = self._cache[cache_key]
            if now - fetched_at < self._ttl * 10:   # longer TTL for pair list
                return stored_df["symbol"].tolist()

        pairs = self._client.get_top_pairs_by_volume(n=n)
        stub = pd.DataFrame({"symbol": pairs})
        self._cache[cache_key] = (stub, now)
        return pairs

    def invalidate(self, symbol: Optional[str] = None, timeframe: Optional[str] = None) -> None:
        """Invalidate cache entries for a symbol (and optionally a specific TF)."""
        if symbol is None:
            self._cache.clear()
            logger.debug("Full cache cleared.")
            return

        keys_to_remove = [
            k for k in self._cache
            if k[0] == symbol and (timeframe is None or k[1] == timeframe)
        ]
        for k in keys_to_remove:
            del self._cache[k]
        logger.debug("Invalidated %d cache entries for %s", len(keys_to_remove), symbol)

    # ------------------------------------------------------------------
    # Convenience helpers used by modules
    # ------------------------------------------------------------------

    @staticmethod
    def validate_df(df: pd.DataFrame, min_rows: int = 50) -> bool:
        """Return True if DataFrame has sufficient data for analysis."""
        if df is None or df.empty:
            return False
        if len(df) < min_rows:
            return False
        required = {"open", "high", "low", "close", "volume"}
        return required.issubset(set(df.columns))
