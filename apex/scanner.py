"""
Scanner — Main market scan loop for APEX.

Scans the top-N Bitget USDT perpetual pairs, runs the full signal engine
on each pair, and returns all valid signals sorted by confidence.
"""

import logging
import time
from typing import List, Optional, Tuple, Union

from apex import config
from apex.bitget_client import BitgetClient
from apex.data_manager import DataManager
from apex.risk_manager import RiskManager
from apex.signal_engine import SignalEngine, ApexSignal, NoTradeSignal
from apex.signal_formatter import format_signal

logger = logging.getLogger(__name__)


class Scanner:
    """
    Orchestrates a full market scan across the top Bitget perpetual pairs.

    Usage:
        scanner = Scanner(client, risk_manager)
        signals = scanner.scan(n_pairs=20)
        for sig in signals:
            print(format_signal(sig))
    """

    def __init__(
        self,
        client: BitgetClient,
        risk_manager: Optional[RiskManager] = None,
        n_pairs: int = 20,
    ) -> None:
        self._client = client
        self._dm = DataManager(client)
        self._engine = SignalEngine()
        self._risk_manager = risk_manager or RiskManager()
        self._n_pairs = n_pairs

    def scan(
        self,
        pairs: Optional[List[str]] = None,
        show_no_trades: bool = False,
    ) -> List[Union[ApexSignal, NoTradeSignal]]:
        """
        Run a full scan across the top pairs.

        Parameters
        ----------
        pairs : list, optional
            Override the pairs to scan. If None, fetches top-N by volume.
        show_no_trades : bool
            If True, the returned list includes NoTradeSignal entries as well.

        Returns
        -------
        List of signals sorted by confidence (valid signals first).
        """
        self._risk_manager.check_period_reset()

        if self._risk_manager.trading_halted:
            logger.warning(
                "Trading is halted: %s — scan aborted.", self._risk_manager.halt_reason
            )
            return []

        # ---- Fetch pairs ----
        if pairs is None:
            logger.info("Fetching top %d pairs by 24h volume...", self._n_pairs)
            try:
                pairs = self._dm.get_top_pairs(n=self._n_pairs)
            except Exception as exc:
                logger.warning("Could not fetch top pairs from exchange: %s — using defaults.", exc)
                pairs = config.TOP_PAIRS[: self._n_pairs]

        logger.info("Scanning %d pairs: %s", len(pairs), ", ".join(pairs))

        results: List[Union[ApexSignal, NoTradeSignal]] = []

        for symbol in pairs:
            logger.info("--- Analysing %s ---", symbol)
            try:
                timeframes = self._dm.get_multi_timeframe(symbol)
            except Exception as exc:
                logger.error("Data fetch failed for %s: %s — skipping.", symbol, exc)
                continue

            try:
                signal = self._engine.analyse(symbol, timeframes)
            except Exception as exc:
                logger.error("Signal engine error for %s: %s — skipping.", symbol, exc)
                continue

            if signal.valid or show_no_trades:
                results.append(signal)

            # Rate limiting — be polite to the exchange
            time.sleep(0.3)

        # Sort: valid signals first, then by confidence descending
        valid = sorted(
            [s for s in results if s.valid],
            key=lambda s: getattr(s, "confidence", 0),
            reverse=True,
        )
        invalid = [s for s in results if not s.valid]

        return valid + (invalid if show_no_trades else [])

    def scan_single(self, symbol: str) -> Union[ApexSignal, NoTradeSignal]:
        """
        Run a full analysis on a single pair.

        Parameters
        ----------
        symbol : str
            e.g. 'BTC/USDT:USDT'
        """
        logger.info("Single-pair analysis: %s", symbol)
        try:
            timeframes = self._dm.get_multi_timeframe(symbol, force_refresh=True)
        except Exception as exc:
            logger.error("Data fetch failed for %s: %s", symbol, exc)
            from apex.signal_engine import NoTradeSignal
            return NoTradeSignal(pair=symbol, reason=f"Data fetch error: {exc}")

        return self._engine.analyse(symbol, timeframes)

    def print_scan_results(
        self,
        signals: List[Union[ApexSignal, NoTradeSignal]],
        use_color: bool = True,
    ) -> None:
        """Pretty-print all signals to stdout."""
        valid = [s for s in signals if s.valid]
        total = len(signals)

        print(f"\n{'═' * 68}")
        print(f"  APEX SCAN COMPLETE  —  {len(valid)} signal(s) from {total} pairs analysed")
        print(f"{'═' * 68}\n")

        if not valid:
            print("  No trade signals generated. Market conditions not optimal.")
            print("  Wait for clearer setups or check funding rates and news.\n")
            return

        for i, sig in enumerate(valid, 1):
            print(f"\n  [{i}/{len(valid)}]")
            print(format_signal(sig, use_color=use_color))
            print()
