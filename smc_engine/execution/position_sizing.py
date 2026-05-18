"""R-based position sizing — Spec §10.1.

risk_dollar / (entry-sl distance) = raw size; round-down to lot_size grid;
check min_notional + 80% margin buffer; raise typed exceptions.

Decimal arithmetic where lot rounding matters (avoid binary FP surprises).
"""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_DOWN, Decimal

from smc_engine.execution._base import Account
from smc_engine.types import SymbolMeta


# ============================================================
# Tick quantize — Bug C (2026-05-18): Binance -1111 "Precision is over
# the maximum defined for this asset" tüm price/stop_price field'lerinde
# tick_size'a hizalı olmalı. Float division (1.4999... gibi) ham float
# kullansak da hassasiyet aşar.
# ============================================================


def quantize_to_tick(price: float, tick_size: float) -> float:
    """Fiyatı tick_size grid'ine quantize et (HALF_DOWN, conservative).

    tick_size <= 0 → no-op (defansif; quantize anlamsız).
    Decimal aritmetik — ham float'ın binary surprise'larını engeller.
    """
    if tick_size <= 0:
        return price
    p = Decimal(str(price))
    t = Decimal(str(tick_size))
    # (p / t) integer'a yuvarla (HALF_DOWN), sonra t ile çarp.
    n = (p / t).quantize(Decimal("1"), rounding=ROUND_HALF_DOWN)
    return float(n * t)


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
