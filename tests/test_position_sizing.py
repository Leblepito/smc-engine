"""Position sizing testleri — R-based sizing + lot rounding + margin check (Spec §10.1)."""

from __future__ import annotations

import pytest

from smc_engine.execution._base import Account
from smc_engine.execution.position_sizing import (
    InsufficientMargin,
    InvalidStopLoss,
    OrderSizeBelowMinimum,
    calc_position_size,
    quantize_to_tick,
)
from smc_engine.types import SymbolMeta


# ============================================================
# Bug C (2026-05-18): quantize_to_tick — Binance -1111 önler
# ============================================================


def test_quantize_to_tick_btc_with_long_float_noise():
    """sl=77217.01299999999 + tick=0.10 → 77217.0 (incident'taki gerçek değer)."""
    assert quantize_to_tick(77217.01299999999, 0.10) == 77217.0


def test_quantize_to_tick_already_on_grid_passthrough():
    """Tam grid'de olan değer aynen döner."""
    assert quantize_to_tick(77585.0, 0.10) == 77585.0


def test_quantize_to_tick_half_down_conservative():
    """0.05 → HALF_DOWN ile 0.0'a iner (ROUND_HALF_DOWN: 0.5 → 0)."""
    assert quantize_to_tick(0.05, 0.10) == 0.0


def test_quantize_to_tick_zero_tick_size_no_op():
    """tick_size <= 0 → no-op (defensive)."""
    assert quantize_to_tick(123.456, 0.0) == 123.456


def test_quantize_to_tick_tp_array_value():
    """TP1 fiyatı (incident'taki) 78287.6825 + tick=0.10 → 78287.7."""
    assert quantize_to_tick(78287.6825, 0.10) == 78287.7


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
