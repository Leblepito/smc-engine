"""Position sizing testleri — R-based sizing + lot rounding + margin check (Spec §10.1)."""

from __future__ import annotations

import pytest

from smc_engine.execution._base import Account
from smc_engine.execution.position_sizing import (
    InsufficientMargin,
    InvalidStopLoss,
    OrderSizeBelowMinimum,
    calc_position_size,
)
from smc_engine.types import SymbolMeta


def _btc_meta(min_notional: float = 5.0):
    """BTCUSDT-like meta with min_notional support."""
    return SymbolMeta(
        symbol="BTCUSDT",
        tick_size=0.1,
        lot_size=0.001,
        min_qty=0.001,
        price_precision=1,
        qty_precision=3,
    ), min_notional


def test_normal_btc_long_size():
    """risk=$2, entry=78329, sl=77435 → distance=894 → raw=0.002237 → round to 0.002."""
    meta, min_notional = _btc_meta()
    acc = Account(equity=25.0, available_margin=20.0, used_margin=5.0)
    size = calc_position_size(
        risk_dollar=2.0, entry=78329.0, sl=77435.0,
        leverage=10, symbol_meta=meta, account=acc, min_notional=min_notional,
    )
    assert size == 0.002


def test_lot_size_rounding_down():
    """raw=0.002247 → lot_size 0.001 → 0.002 (NOT round-up)."""
    meta, _ = _btc_meta()
    acc = Account(equity=25.0, available_margin=20.0, used_margin=5.0)
    size = calc_position_size(
        risk_dollar=2.0, entry=78329.0, sl=77439.0,  # distance=890 → raw=0.002247
        leverage=10, symbol_meta=meta, account=acc, min_notional=5.0,
    )
    assert size == 0.002  # 0.002247 → 0.002 not 0.003


def test_below_min_notional_raises():
    """Çok dar SL → tiny size → notional < min_notional → raise.

    risk=$0.50, distance=1.0 → raw=0.5 BTC ... HAYIR risk=$0.05 ile dene:
    risk=$0.05, distance=80 → raw=0.000625 → lot 0.001 → 0.000 → notional=0
    """
    meta, _ = _btc_meta(min_notional=5.0)
    acc = Account(equity=25.0, available_margin=20.0, used_margin=5.0)
    with pytest.raises(OrderSizeBelowMinimum):
        calc_position_size(
            risk_dollar=0.05, entry=78329.0, sl=78249.0,  # distance=80
            leverage=10, symbol_meta=meta, account=acc, min_notional=5.0,
        )


def test_insufficient_margin_raises():
    """size çok büyük → required margin > 80% of available → raise."""
    meta, _ = _btc_meta()
    acc = Account(equity=25.0, available_margin=5.0, used_margin=20.0)  # az margin
    with pytest.raises(InsufficientMargin):
        calc_position_size(
            risk_dollar=200.0, entry=78329.0, sl=78320.0,  # huge size
            leverage=10, symbol_meta=meta, account=acc, min_notional=5.0,
        )


def test_invalid_sl_zero_distance_raises():
    meta, _ = _btc_meta()
    acc = Account(equity=25.0, available_margin=20.0, used_margin=5.0)
    with pytest.raises(InvalidStopLoss):
        calc_position_size(
            risk_dollar=2.0, entry=78329.0, sl=78329.0,
            leverage=10, symbol_meta=meta, account=acc, min_notional=5.0,
        )


def test_short_position_uses_abs_distance():
    """SHORT: sl > entry → abs(distance) ile aynı sonuç."""
    meta, _ = _btc_meta()
    acc = Account(equity=25.0, available_margin=20.0, used_margin=5.0)
    size = calc_position_size(
        risk_dollar=2.0, entry=78329.0, sl=79223.0,  # distance=894, SHORT
        leverage=10, symbol_meta=meta, account=acc, min_notional=5.0,
    )
    assert size == 0.002
