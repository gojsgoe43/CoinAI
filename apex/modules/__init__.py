"""
APEX Strategy Modules

Each module analyses OHLCV data and returns a ModuleSignal dataclass
containing a directional vote, confidence score and human-readable reasons.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List


class Vote(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


@dataclass
class ModuleSignal:
    """Result produced by every strategy module."""
    module_name: str
    vote: Vote
    confidence: float          # 0 – 100
    reasons: List[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"[{self.module_name}] {self.vote.value} "
            f"({self.confidence:.0f}%) — {'; '.join(self.reasons)}"
        )
