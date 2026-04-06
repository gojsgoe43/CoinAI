"""
Bitget API Client — CCXT-based wrapper for APEX.

Provides authenticated access to Bitget futures (swap) markets:
  - Fetching OHLCV data
  - Account balance / equity
  - Open positions
  - Order placement helpers
"""

import logging
import time
from typing import Dict, List, Optional, Tuple

import ccxt
import pandas as pd

from apex import config

logger = logging.getLogger(__name__)


class BitgetClient:
    """Thin wrapper around ccxt.bitget for APEX operations."""

    def __init__(
        self,
        api_key: str = "",
        secret: str = "",
        passphrase: str = "",
        sandbox: bool = False,
    ) -> None:
        self._exchange = ccxt.bitget(
            {
                "apiKey": api_key or config.BITGET_API_KEY,
                "secret": secret or config.BITGET_SECRET,
                "password": passphrase or config.BITGET_PASSPHRASE,
                "options": {
                    "defaultType": config.DEFAULT_MARKET_TYPE,
                },
                "enableRateLimit": True,
            }
        )
        if sandbox:
            self._exchange.set_sandbox_mode(True)

        self._markets_loaded = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_markets(self) -> None:
        if not self._markets_loaded:
            try:
                self._exchange.load_markets()
                self._markets_loaded = True
                logger.debug("Markets loaded successfully.")
            except Exception as exc:
                logger.error("Failed to load markets: %s", exc)
                raise

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 200,
        since: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV candles and return a clean DataFrame.

        Columns: timestamp (datetime), open, high, low, close, volume
        """
        self._load_markets()
        try:
            raw = self._exchange.fetch_ohlcv(
                symbol,
                timeframe=timeframe,
                limit=limit,
                since=since,
                params={"productType": "USDT-FUTURES"},
            )
        except ccxt.BadSymbol:
            # Try without product-type param for older endpoints
            raw = self._exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, limit=limit, since=since
            )

        if not raw:
            logger.warning("No OHLCV data returned for %s %s", symbol, timeframe)
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.sort_values("timestamp").reset_index(drop=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df.dropna(subset=["open", "high", "low", "close"], inplace=True)
        logger.debug("Fetched %d candles for %s %s", len(df), symbol, timeframe)
        return df

    def fetch_multi_timeframe(
        self, symbol: str
    ) -> Dict[str, pd.DataFrame]:
        """
        Fetch all relevant timeframes for a symbol.
        Returns dict keyed by timeframe string.
        """
        result: Dict[str, pd.DataFrame] = {}
        for tf_name, tf_str in config.TIMEFRAMES.items():
            limit = config.CANDLE_LIMIT.get(tf_str, 200)
            try:
                df = self.fetch_ohlcv(symbol, tf_str, limit=limit)
                result[tf_str] = df
                logger.debug("  %s: %d candles", tf_str, len(df))
            except Exception as exc:
                logger.warning("Could not fetch %s %s: %s", symbol, tf_str, exc)
                result[tf_str] = pd.DataFrame(
                    columns=["timestamp", "open", "high", "low", "close", "volume"]
                )
        return result

    def fetch_ticker(self, symbol: str) -> Dict:
        """Return the latest ticker for a symbol."""
        self._load_markets()
        return self._exchange.fetch_ticker(symbol)

    def get_top_pairs_by_volume(self, n: int = 20) -> List[str]:
        """
        Discover the top-N USDT-margined perpetual pairs by 24 h volume
        from the exchange.  Falls back to the hard-coded list on error.
        """
        self._load_markets()
        try:
            tickers = self._exchange.fetch_tickers()
            usdt_perp = {
                sym: t
                for sym, t in tickers.items()
                if sym.endswith(":USDT")
                and t.get("quoteVolume") is not None
            }
            sorted_pairs = sorted(
                usdt_perp.items(),
                key=lambda x: x[1].get("quoteVolume", 0),
                reverse=True,
            )
            pairs = [sym for sym, _ in sorted_pairs[:n]]
            logger.info("Discovered %d top pairs by volume.", len(pairs))
            return pairs if pairs else config.TOP_PAIRS[:n]
        except Exception as exc:
            logger.warning("Could not fetch tickers for top-pairs ranking: %s", exc)
            return config.TOP_PAIRS[:n]

    # ------------------------------------------------------------------
    # Account / Position data (requires auth)
    # ------------------------------------------------------------------

    def fetch_balance(self) -> Dict:
        """Return the full balance dictionary from the exchange."""
        self._load_markets()
        try:
            return self._exchange.fetch_balance({"type": "swap"})
        except Exception as exc:
            logger.error("fetch_balance failed: %s", exc)
            return {}

    def get_usdt_equity(self) -> float:
        """Return the total USDT equity in the futures account."""
        balance = self.fetch_balance()
        try:
            total = balance.get("total", {})
            return float(total.get("USDT", 0.0))
        except (TypeError, ValueError):
            return 0.0

    def fetch_open_positions(self) -> List[Dict]:
        """Return a list of currently open swap positions."""
        self._load_markets()
        try:
            positions = self._exchange.fetch_positions()
            return [p for p in positions if float(p.get("contracts", 0) or 0) != 0]
        except Exception as exc:
            logger.error("fetch_open_positions failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Order helpers
    # ------------------------------------------------------------------

    def place_limit_order(
        self,
        symbol: str,
        side: str,           # "buy" or "sell"
        amount: float,
        price: float,
        reduce_only: bool = False,
        params: Optional[Dict] = None,
    ) -> Dict:
        """Place a limit order on Bitget futures."""
        self._load_markets()
        extra = params or {}
        if reduce_only:
            extra["reduceOnly"] = True
        try:
            order = self._exchange.create_limit_order(
                symbol, side, amount, price, extra
            )
            logger.info(
                "Placed %s limit order %s %s @ %.6f (id=%s)",
                side.upper(), amount, symbol, price, order.get("id"),
            )
            return order
        except Exception as exc:
            logger.error("place_limit_order failed: %s", exc)
            raise

    def set_leverage(self, symbol: str, leverage: int, margin_mode: str = "isolated") -> None:
        """Set leverage and margin mode for a symbol."""
        self._load_markets()
        try:
            self._exchange.set_leverage(leverage, symbol, {"marginMode": margin_mode})
            logger.info("Leverage set to %dx (%s) for %s", leverage, margin_mode, symbol)
        except Exception as exc:
            logger.warning("set_leverage failed for %s: %s", symbol, exc)

    def cancel_order(self, order_id: str, symbol: str) -> Dict:
        """Cancel an open order."""
        self._load_markets()
        return self._exchange.cancel_order(order_id, symbol)
