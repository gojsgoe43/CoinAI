"""
Risk Manager — Position sizing, drawdown tracking, hard stops.

Responsibilities:
  - Calculate position size based on 1% risk rule
  - Track daily and weekly PnL
  - Enforce 3% daily drawdown and 7% weekly drawdown hard stops
  - Track open position count (max 5)
  - Determine appropriate leverage based on setup grade and volatility
  - Validate trade entry against all risk rules
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from apex import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PositionInfo:
    """Represents a tracked open position."""
    symbol: str
    direction: str              # 'LONG' or 'SHORT'
    entry_price: float
    stop_loss: float
    size_usdt: float            # Position size in USDT notional
    leverage: int
    opened_at: float = field(default_factory=time.time)
    grade: str = "A"            # 'A+' or 'A'
    unrealized_pnl: float = 0.0


@dataclass
class RiskCheckResult:
    """Result of a pre-trade risk check."""
    approved: bool
    reason: str
    max_position_usdt: float = 0.0
    suggested_leverage: int = 1
    risk_amount_usdt: float = 0.0


# ---------------------------------------------------------------------------
# Risk Manager
# ---------------------------------------------------------------------------

class RiskManager:
    """
    Central risk management component for APEX.

    Usage:
        rm = RiskManager(account_balance=10_000)
        check = rm.check_new_trade(grade="A+", entry=50000, sl=49000)
        if check.approved:
            size = check.max_position_usdt
    """

    def __init__(
        self,
        account_balance: float = 10_000.0,
        daily_start_equity: Optional[float] = None,
        weekly_start_equity: Optional[float] = None,
    ) -> None:
        self.account_balance = account_balance
        self.daily_start_equity = daily_start_equity or account_balance
        self.weekly_start_equity = weekly_start_equity or account_balance

        self.daily_pnl: float = 0.0
        self.weekly_pnl: float = 0.0

        self._open_positions: Dict[str, PositionInfo] = {}  # symbol → PositionInfo

        # Track daily reset
        self._last_daily_reset: datetime = datetime.now(tz=timezone.utc).date()
        self._last_weekly_reset: int = datetime.now(tz=timezone.utc).isocalendar()[1]  # week number

        self.trading_halted: bool = False
        self.halt_reason: Optional[str] = None

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def open_position_count(self) -> int:
        return len(self._open_positions)

    @property
    def daily_drawdown_pct(self) -> float:
        """Current daily drawdown as a percentage of daily start equity."""
        if self.daily_start_equity <= 0:
            return 0.0
        return (self.daily_start_equity - self.account_balance) / self.daily_start_equity

    @property
    def weekly_drawdown_pct(self) -> float:
        """Current weekly drawdown as a percentage of weekly start equity."""
        if self.weekly_start_equity <= 0:
            return 0.0
        return (self.weekly_start_equity - self.account_balance) / self.weekly_start_equity

    # ------------------------------------------------------------------
    # Daily/Weekly reset
    # ------------------------------------------------------------------

    def check_period_reset(self) -> None:
        """
        Automatically reset daily/weekly counters when a new day/week begins.
        Should be called at the start of each scan cycle.
        """
        now = datetime.now(tz=timezone.utc)
        today = now.date()
        current_week = now.isocalendar()[1]

        if today > self._last_daily_reset:
            logger.info(
                "New trading day — resetting daily PnL. Previous: %.2f USDT", self.daily_pnl
            )
            self.daily_start_equity = self.account_balance
            self.daily_pnl = 0.0
            self._last_daily_reset = today
            # Re-evaluate halt on new day
            if self.trading_halted and self.halt_reason and "daily" in self.halt_reason.lower():
                logger.info("Daily reset — resuming trading.")
                self.trading_halted = False
                self.halt_reason = None

        if current_week > self._last_weekly_reset:
            logger.info(
                "New trading week — resetting weekly PnL. Previous: %.2f USDT", self.weekly_pnl
            )
            self.weekly_start_equity = self.account_balance
            self.weekly_pnl = 0.0
            self._last_weekly_reset = current_week

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def calculate_position_size(
        self,
        entry_price: float,
        stop_loss_price: float,
        leverage: int,
        risk_fraction: float = config.MAX_RISK_PER_TRADE,
    ) -> Tuple[float, float]:
        """
        Calculate the position size based on the 1% risk rule.

        Risk amount = account_balance * risk_fraction
        Stop distance = |entry_price - stop_loss_price| / entry_price
        Position size (USDT notional) = risk_amount / stop_distance

        Returns:
            (position_size_usdt, risk_amount_usdt)
        """
        if entry_price <= 0 or stop_loss_price <= 0:
            return 0.0, 0.0

        risk_amount_usdt = self.account_balance * risk_fraction
        stop_distance_pct = abs(entry_price - stop_loss_price) / entry_price

        if stop_distance_pct <= 0:
            logger.warning("Stop distance is 0, cannot size position.")
            return 0.0, 0.0

        # Without leverage: how much notional to risk exactly risk_amount_usdt?
        position_size_usdt = risk_amount_usdt / stop_distance_pct

        # Cap at leverage * available margin (don't exceed leverage)
        max_notional = self.account_balance * leverage
        position_size_usdt = min(position_size_usdt, max_notional)

        logger.debug(
            "Position size: risk=%.2f USDT, stop_dist=%.3f%%, notional=%.2f USDT (lev %dx)",
            risk_amount_usdt, stop_distance_pct * 100, position_size_usdt, leverage,
        )
        return round(position_size_usdt, 2), round(risk_amount_usdt, 2)

    def determine_leverage(
        self, grade: str, is_high_volatility: bool = False
    ) -> int:
        """
        Determine appropriate leverage based on setup grade and volatility.

          A+ (4/4): up to 10x
          A  (3/4): up to 7x
          High volatility: cap at 3-5x
          Absolute max: 15x (never exceed)
        """
        if is_high_volatility:
            return min(config.LEVERAGE_VOLATILE, config.LEVERAGE_MAX_ABSOLUTE)

        if grade == "A+":
            leverage = config.LEVERAGE_A_PLUS
        elif grade == "A":
            leverage = config.LEVERAGE_A
        else:
            leverage = 3  # Conservative for lower-quality setups

        return min(leverage, config.LEVERAGE_MAX_ABSOLUTE)

    # ------------------------------------------------------------------
    # Pre-trade risk check
    # ------------------------------------------------------------------

    def check_new_trade(
        self,
        grade: str,
        entry_price: float,
        stop_loss_price: float,
        is_high_volatility: bool = False,
        symbol: Optional[str] = None,
    ) -> RiskCheckResult:
        """
        Validate a potential trade against all risk rules.

        Returns RiskCheckResult with approval status and recommended size.
        """
        self.check_period_reset()

        # Hard stop: trading halted
        if self.trading_halted:
            return RiskCheckResult(
                approved=False,
                reason=f"Trading halted: {self.halt_reason}",
            )

        # Hard stop: max open positions
        if self.open_position_count >= config.MAX_OPEN_POSITIONS:
            return RiskCheckResult(
                approved=False,
                reason=(
                    f"Max open positions reached ({self.open_position_count}/"
                    f"{config.MAX_OPEN_POSITIONS})"
                ),
            )

        # Already in this symbol?
        if symbol and symbol in self._open_positions:
            return RiskCheckResult(
                approved=False,
                reason=f"Already have an open position in {symbol}",
            )

        # Daily drawdown hard stop
        if self.daily_drawdown_pct >= config.DAILY_DRAWDOWN_HARD_STOP:
            reason = (
                f"Daily drawdown limit reached: "
                f"{self.daily_drawdown_pct*100:.2f}% >= "
                f"{config.DAILY_DRAWDOWN_HARD_STOP*100:.0f}%"
            )
            self._halt_trading(reason)
            return RiskCheckResult(approved=False, reason=reason)

        # Weekly drawdown hard stop
        if self.weekly_drawdown_pct >= config.WEEKLY_DRAWDOWN_HARD_STOP:
            reason = (
                f"Weekly drawdown limit reached: "
                f"{self.weekly_drawdown_pct*100:.2f}% >= "
                f"{config.WEEKLY_DRAWDOWN_HARD_STOP*100:.0f}%"
            )
            self._halt_trading(reason)
            return RiskCheckResult(approved=False, reason=reason)

        # Minimum grade check
        if grade not in ("A+", "A"):
            return RiskCheckResult(
                approved=False,
                reason=f"Setup grade '{grade}' below minimum threshold (need A or A+)",
            )

        # Determine leverage
        leverage = self.determine_leverage(grade, is_high_volatility)

        # Calculate position size
        position_size_usdt, risk_amount_usdt = self.calculate_position_size(
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            leverage=leverage,
        )

        if position_size_usdt <= 0:
            return RiskCheckResult(
                approved=False,
                reason="Position size calculated as zero — check entry/SL prices",
            )

        logger.info(
            "Trade approved: grade=%s lev=%dx size=%.2f USDT risk=%.2f USDT",
            grade, leverage, position_size_usdt, risk_amount_usdt,
        )
        return RiskCheckResult(
            approved=True,
            reason="All risk checks passed",
            max_position_usdt=position_size_usdt,
            suggested_leverage=leverage,
            risk_amount_usdt=risk_amount_usdt,
        )

    # ------------------------------------------------------------------
    # Position tracking
    # ------------------------------------------------------------------

    def register_position(self, position: PositionInfo) -> None:
        """Register a newly opened position."""
        self._open_positions[position.symbol] = position
        logger.info(
            "Position registered: %s %s entry=%.4f sl=%.4f size=%.2f USDT lev=%dx",
            position.symbol, position.direction, position.entry_price,
            position.stop_loss, position.size_usdt, position.leverage,
        )

    def close_position(self, symbol: str, exit_price: float) -> float:
        """
        Close a tracked position and update PnL.
        Returns the realised PnL in USDT.
        """
        if symbol not in self._open_positions:
            logger.warning("Tried to close unknown position: %s", symbol)
            return 0.0

        pos = self._open_positions.pop(symbol)

        # Realised PnL (simplified — notional * price_change_pct * leverage)
        if pos.direction == "LONG":
            pnl = pos.size_usdt * (exit_price - pos.entry_price) / pos.entry_price
        else:
            pnl = pos.size_usdt * (pos.entry_price - exit_price) / pos.entry_price

        self.daily_pnl += pnl
        self.weekly_pnl += pnl
        self.account_balance += pnl

        logger.info(
            "Position closed: %s exit=%.4f pnl=%.2f USDT (daily: %.2f, weekly: %.2f)",
            symbol, exit_price, pnl, self.daily_pnl, self.weekly_pnl,
        )

        # Re-check drawdown after close
        self._check_drawdown_limits()
        return pnl

    def update_position_pnl(self, symbol: str, current_price: float) -> float:
        """Update unrealized PnL for an open position. Returns unrealized PnL."""
        if symbol not in self._open_positions:
            return 0.0

        pos = self._open_positions[symbol]
        if pos.direction == "LONG":
            unrealized = pos.size_usdt * (current_price - pos.entry_price) / pos.entry_price
        else:
            unrealized = pos.size_usdt * (pos.entry_price - current_price) / pos.entry_price

        pos.unrealized_pnl = unrealized
        return unrealized

    def get_open_positions(self) -> List[PositionInfo]:
        """Return list of all currently open positions."""
        return list(self._open_positions.values())

    # ------------------------------------------------------------------
    # Balance sync
    # ------------------------------------------------------------------

    def sync_balance(self, new_balance: float) -> None:
        """
        Sync account balance from exchange data.
        Used to keep risk calculations accurate after live trades.
        """
        old_balance = self.account_balance
        self.account_balance = new_balance

        if old_balance != new_balance:
            delta = new_balance - old_balance
            self.daily_pnl += delta
            self.weekly_pnl += delta
            logger.debug("Balance synced: %.2f → %.2f (delta: %.2f)", old_balance, new_balance, delta)

        self._check_drawdown_limits()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _halt_trading(self, reason: str) -> None:
        """Set the trading halt flag."""
        if not self.trading_halted:
            self.trading_halted = True
            self.halt_reason = reason
            logger.warning("TRADING HALTED: %s", reason)

    def _check_drawdown_limits(self) -> None:
        """Check and enforce drawdown hard stops."""
        if self.daily_drawdown_pct >= config.DAILY_DRAWDOWN_HARD_STOP:
            self._halt_trading(
                f"Daily drawdown {self.daily_drawdown_pct*100:.2f}% exceeded "
                f"{config.DAILY_DRAWDOWN_HARD_STOP*100:.0f}% hard stop"
            )
        elif self.weekly_drawdown_pct >= config.WEEKLY_DRAWDOWN_HARD_STOP:
            self._halt_trading(
                f"Weekly drawdown {self.weekly_drawdown_pct*100:.2f}% exceeded "
                f"{config.WEEKLY_DRAWDOWN_HARD_STOP*100:.0f}% hard stop"
            )

    # ------------------------------------------------------------------
    # Status report
    # ------------------------------------------------------------------

    def get_status_report(self) -> Dict:
        """Return a dictionary summarising current risk state."""
        self.check_period_reset()
        return {
            "account_balance": round(self.account_balance, 2),
            "daily_start_equity": round(self.daily_start_equity, 2),
            "weekly_start_equity": round(self.weekly_start_equity, 2),
            "daily_pnl": round(self.daily_pnl, 2),
            "weekly_pnl": round(self.weekly_pnl, 2),
            "daily_drawdown_pct": round(self.daily_drawdown_pct * 100, 2),
            "weekly_drawdown_pct": round(self.weekly_drawdown_pct * 100, 2),
            "daily_drawdown_limit_pct": config.DAILY_DRAWDOWN_HARD_STOP * 100,
            "weekly_drawdown_limit_pct": config.WEEKLY_DRAWDOWN_HARD_STOP * 100,
            "open_positions": self.open_position_count,
            "max_positions": config.MAX_OPEN_POSITIONS,
            "trading_halted": self.trading_halted,
            "halt_reason": self.halt_reason,
            "positions": [
                {
                    "symbol": p.symbol,
                    "direction": p.direction,
                    "entry_price": p.entry_price,
                    "stop_loss": p.stop_loss,
                    "size_usdt": p.size_usdt,
                    "leverage": p.leverage,
                    "grade": p.grade,
                    "unrealized_pnl": round(p.unrealized_pnl, 2),
                }
                for p in self._open_positions.values()
            ],
        }
