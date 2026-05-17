"""R-based position sizing — Spec §10.1.

risk_dollar / (entry-sl distance) = raw size; round-down to lot_size grid;
check min_notional + 80% margin buffer; raise typed exceptions.

Decimal arithmetic where lot rounding matters (avoid binary FP surprises).
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from smc_engine.execution._base import Account
from smc_engine.types import SymbolMeta


# ============================================================
# Exceptions
# ============================================================


class OrderSizeBelowMinimum(Exception):
    """Computed notional below exchange minimum."""

    def __init__(self, notional: float, minimum: float) -> None:
        self.notional = notional
        self.minimum = minimum
        super().__init__(f"notional {notional:.4f} < min_notional {minimum:.4f}")


class InsufficientMargin(Exception):
    def __init__(self, required: float, available: float) -> None:
        self.required = required
        self.available = available
        super().__init__(f"margin required {required:.4f} > 80% of available {available:.4f}")


class InvalidStopLoss(Exception):
    def __init__(self, entry: float, sl: float) -> None:
        super().__init__(f"invalid SL: entry={entry} sl={sl} (distance=0)")


# ============================================================
# Core
# ============================================================


_MARGIN_BUFFER = Decimal("0.80")


def calc_position_size(
    risk_dollar: float,
    entry: float,
    sl: float,
    leverage: int,
    symbol_meta: SymbolMeta,
    account: Account,
    min_notional: float = 5.0,
) -> float:
    """R-based size; raise on invalid params or guard breach."""
    if entry == sl:
        raise InvalidStopLoss(entry, sl)

    risk_d = Decimal(str(risk_dollar))
    entry_d = Decimal(str(entry))
    sl_d = Decimal(str(sl))
    lot_d = Decimal(str(symbol_meta.lot_size))

    distance = abs(entry_d - sl_d)
    raw_size = risk_d / distance

    # Round DOWN to lot grid (never round-up; could blow the risk budget)
    rounded = (raw_size / lot_d).to_integral_value(rounding=ROUND_DOWN) * lot_d
    size = float(rounded)

    # Min notional check
    notional = size * float(entry_d)
    if notional < min_notional:
        raise OrderSizeBelowMinimum(notional, min_notional)

    # Margin check (80% buffer)
    required_margin = notional / leverage
    margin_cap = float(_MARGIN_BUFFER) * account.available_margin
    if required_margin > margin_cap:
        raise InsufficientMargin(required_margin, account.available_margin)

    return size
