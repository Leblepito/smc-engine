"""ReconcileLoop testleri — drift detection (Spec §4.7).

5A: detect-only. Auto-fix YOK. Drift bulunursa kill_switch.trigger_external.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from smc_engine.execution._base import (
    Account,
    OrderResponse,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)
from smc_engine.execution.audit_log import AuditLog
from smc_engine.execution.kill_switch import KillSwitch
from smc_engine.execution.position_tracker import PositionTracker, TrackedPosition
from smc_engine.execution.reconcile import ReconcileLoop


def _make_position(order_id="12345", symbol="BTCUSDT", side="BUY"):
    return TrackedPosition(
        order_id=order_id, symbol=symbol, side=side,
        qty=0.002, entry=78329.30, sl=77435.0, tp=79670.0,
        placed_at=datetime(2026, 5, 17, 3, 15, 5),
        timeout_at=datetime(2026, 5, 17, 4, 15, 5),
        signal_at_bar=datetime(2026, 5, 17, 3, 15, 0),
        risk_dollar=2.0, leverage=10,
    )


def _setup(tmp_path):
    audit = AuditLog(log_dir=str(tmp_path / "audit"), engine_sha="x", testnet=True)
    ks = KillSwitch(
        consecutive_loss_threshold=3, daily_loss_threshold=5.0, equity_minimum=15.0,
        state_path=tmp_path / "ks.json", audit_log=audit,
    )
    tracker = PositionTracker()
    client = MagicMock()
    client.get_open_orders.return_value = []
    client.get_position = MagicMock()
    rl = ReconcileLoop(
        order_client=client, position_tracker=tracker,
        audit_log=audit, kill_switch=ks,
    )
    return rl, client, tracker, audit, ks


# ============================================================
# Check 1: local PENDING but no Binance order
# ============================================================


def test_drift_pending_missing_in_binance(tmp_path):
    rl, client, tracker, audit, ks = _setup(tmp_path)
    tracker.add(_make_position("12345"))
    client.get_open_orders.return_value = []  # Binance'te yok

    rl.tick()
    assert ks.is_triggered() is True
    log = list((tmp_path / "audit").glob("trades-*.jsonl"))[0].read_text()
    assert "RECONCILE_DRIFT" in log


# ============================================================
# Check 2: local ACTIVE but no Binance position
# ============================================================


def test_drift_active_position_missing(tmp_path):
    rl, client, tracker, audit, ks = _setup(tmp_path)
    p = _make_position("12345")
    tracker.add(p)
    tracker.on_fill("12345", sl_order_id="2", tp_order_id="3",
                    fill_price=78327.50, fill_qty=0.002)
    # Binance'te artık position yok (sıfır)
    client.get_position.return_value = Position(
        symbol="BTCUSDT", qty=0.0, entry_price=0.0,
        unrealized_pnl=0.0, liquidation_price=0.0,
    )
    # SL+TP orders still NEW on Binance to suppress check 1 noise
    client.get_open_orders.return_value = [
        OrderResponse(order_id="2", symbol="BTCUSDT", side=OrderSide.SELL,
                      type=OrderType.STOP_MARKET, qty=0.002, price=None,
                      status=OrderStatus.NEW),
        OrderResponse(order_id="3", symbol="BTCUSDT", side=OrderSide.SELL,
                      type=OrderType.LIMIT, qty=0.002, price=79670.0,
                      status=OrderStatus.NEW),
    ]
    rl.tick()
    assert ks.is_triggered() is True


# ============================================================
# Check 3: Binance order not in local state
# ============================================================


def test_drift_unknown_binance_order(tmp_path):
    rl, client, tracker, audit, ks = _setup(tmp_path)
    # Local'de hiÃ§bir order yok
    client.get_open_orders.return_value = [
        OrderResponse(order_id="99999", symbol="BTCUSDT", side=OrderSide.BUY,
                      type=OrderType.LIMIT, qty=0.001, price=78000.0,
                      status=OrderStatus.NEW),
    ]
    rl.tick()
    assert ks.is_triggered() is True
    log = list((tmp_path / "audit").glob("trades-*.jsonl"))[0].read_text()
    assert "RECONCILE_DRIFT" in log
    assert "99999" in log


# ============================================================
# Check 4: quantity mismatch
# ============================================================


def test_drift_qty_mismatch(tmp_path):
    rl, client, tracker, audit, ks = _setup(tmp_path)
    p = _make_position("12345")
    tracker.add(p)
    tracker.on_fill("12345", sl_order_id="2", tp_order_id="3",
                    fill_price=78327.50, fill_qty=0.002)
    # Binance'te position 0.005 (local 0.002 — drift)
    client.get_position.return_value = Position(
        symbol="BTCUSDT", qty=0.005, entry_price=78327.50,
        unrealized_pnl=0.0, liquidation_price=70000.0,
    )
    client.get_open_orders.return_value = [
        OrderResponse(order_id="2", symbol="BTCUSDT", side=OrderSide.SELL,
                      type=OrderType.STOP_MARKET, qty=0.002, price=None,
                      status=OrderStatus.NEW),
        OrderResponse(order_id="3", symbol="BTCUSDT", side=OrderSide.SELL,
                      type=OrderType.LIMIT, qty=0.002, price=79670.0,
                      status=OrderStatus.NEW),
    ]
    rl.tick()
    assert ks.is_triggered() is True


# ============================================================
# No drift cases
# ============================================================


def test_no_drift_when_all_match(tmp_path):
    rl, client, tracker, audit, ks = _setup(tmp_path)
    p = _make_position("12345")
    tracker.add(p)
    tracker.on_fill("12345", sl_order_id="2", tp_order_id="3",
                    fill_price=78327.50, fill_qty=0.002)
    client.get_position.return_value = Position(
        symbol="BTCUSDT", qty=0.002, entry_price=78327.50,
        unrealized_pnl=0.0, liquidation_price=70000.0,
    )
    client.get_open_orders.return_value = [
        OrderResponse(order_id="2", symbol="BTCUSDT", side=OrderSide.SELL,
                      type=OrderType.STOP_MARKET, qty=0.002, price=None,
                      status=OrderStatus.NEW),
        OrderResponse(order_id="3", symbol="BTCUSDT", side=OrderSide.SELL,
                      type=OrderType.LIMIT, qty=0.002, price=79670.0,
                      status=OrderStatus.NEW),
    ]
    rl.tick()
    assert ks.is_triggered() is False


def test_no_drift_with_empty_state(tmp_path):
    """Empty tracker + empty Binance = nothing to reconcile."""
    rl, client, tracker, audit, ks = _setup(tmp_path)
    client.get_open_orders.return_value = []
    rl.tick()
    assert ks.is_triggered() is False
