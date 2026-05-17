"""PositionTracker state machine + persistence (Spec §4.3, §7, §8)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from smc_engine.execution.position_tracker import (
    IllegalStateTransition,
    PositionState,
    PositionTracker,
    TrackedPosition,
)


def _make_position(order_id="12345", symbol="BTCUSDT", side="BUY", **kw):
    defaults = dict(
        order_id=order_id, symbol=symbol, side=side,
        qty=0.002, entry=78329.30, sl=77435.0, tp=79670.0,
        placed_at=datetime(2026, 5, 17, 3, 15, 5),
        timeout_at=datetime(2026, 5, 17, 4, 15, 5),
        signal_at_bar=datetime(2026, 5, 17, 3, 15, 0),
        risk_dollar=2.0, leverage=10,
    )
    defaults.update(kw)
    return TrackedPosition(**defaults)


# ============================================================
# Add + initial state
# ============================================================


def test_add_creates_pending_position():
    tr = PositionTracker()
    p = _make_position()
    tr.add(p)
    pendings = tr.pending()
    assert len(pendings) == 1
    assert pendings[0].order_id == "12345"
    assert pendings[0].state is PositionState.PENDING


def test_add_duplicate_order_id_raises():
    tr = PositionTracker()
    tr.add(_make_position("12345"))
    with pytest.raises(ValueError, match="duplicate"):
        tr.add(_make_position("12345"))


# ============================================================
# State transitions
# ============================================================


def test_pending_to_active_on_fill():
    tr = PositionTracker()
    tr.add(_make_position("1"))
    tr.on_fill("1", sl_order_id="2", tp_order_id="3", fill_price=78327.50, fill_qty=0.002)
    actives = tr.active()
    assert len(actives) == 1
    p = actives[0]
    assert p.state is PositionState.ACTIVE
    assert p.sl_order_id == "2"
    assert p.tp_order_id == "3"
    assert p.fill_price == 78327.50


def test_pending_to_aborted_on_timeout():
    tr = PositionTracker()
    tr.add(_make_position("1"))
    tr.on_timeout("1")
    assert tr.pending() == []
    closed = tr.closed_or_aborted()
    assert len(closed) == 1
    assert closed[0].state is PositionState.ABORTED
    assert closed[0].abort_reason == "TIMEOUT"


def test_pending_to_aborted_on_reject():
    tr = PositionTracker()
    tr.add(_make_position("1"))
    tr.on_reject("1", reason="-2010 NEW_ORDER_REJECTED")
    closed = tr.closed_or_aborted()
    assert closed[0].state is PositionState.ABORTED
    assert "-2010" in closed[0].abort_reason


def test_active_to_closed_win_on_tp_hit():
    tr = PositionTracker()
    tr.add(_make_position("1"))
    tr.on_fill("1", sl_order_id="2", tp_order_id="3", fill_price=78327.50, fill_qty=0.002)
    tr.on_tp_hit("1", exit_price=79670.0, pnl_dollar=3.01)
    closed = tr.closed_or_aborted()
    assert closed[0].state is PositionState.CLOSED_WIN
    assert closed[0].exit_price == 79670.0
    assert closed[0].pnl_dollar == 3.01


def test_active_to_closed_loss_on_sl_hit():
    tr = PositionTracker()
    tr.add(_make_position("1"))
    tr.on_fill("1", sl_order_id="2", tp_order_id="3", fill_price=78327.50, fill_qty=0.002)
    tr.on_sl_hit("1", exit_price=77435.0, pnl_dollar=-1.98)
    closed = tr.closed_or_aborted()
    assert closed[0].state is PositionState.CLOSED_LOSS
    assert closed[0].pnl_dollar == -1.98


def test_active_to_closed_manual():
    tr = PositionTracker()
    tr.add(_make_position("1"))
    tr.on_fill("1", sl_order_id="2", tp_order_id="3", fill_price=78327.50, fill_qty=0.002)
    tr.on_manual_close("1", exit_price=78500.0, pnl_dollar=0.35)
    closed = tr.closed_or_aborted()
    assert closed[0].state is PositionState.CLOSED_MANUAL


def test_active_to_closed_drift():
    tr = PositionTracker()
    tr.add(_make_position("1"))
    tr.on_fill("1", sl_order_id="2", tp_order_id="3", fill_price=78327.50, fill_qty=0.002)
    tr.on_drift("1", details="local_qty=0.002 binance_qty=0")
    closed = tr.closed_or_aborted()
    assert closed[0].state is PositionState.CLOSED_DRIFT


def test_invalid_transition_active_to_active_raises():
    """ACTIVE → on_fill → IllegalStateTransition."""
    tr = PositionTracker()
    tr.add(_make_position("1"))
    tr.on_fill("1", sl_order_id="2", tp_order_id="3", fill_price=78327.50, fill_qty=0.002)
    with pytest.raises(IllegalStateTransition):
        tr.on_fill("1", sl_order_id="4", tp_order_id="5", fill_price=78327.50, fill_qty=0.002)


def test_invalid_transition_pending_to_tp_hit_raises():
    """PENDING → on_tp_hit (filled olmadan) → IllegalStateTransition."""
    tr = PositionTracker()
    tr.add(_make_position("1"))
    with pytest.raises(IllegalStateTransition):
        tr.on_tp_hit("1", exit_price=79670.0, pnl_dollar=3.01)


def test_lookup_unknown_order_id_raises():
    tr = PositionTracker()
    with pytest.raises(KeyError):
        tr.on_fill("nonexistent", sl_order_id="x", tp_order_id="y",
                   fill_price=0, fill_qty=0)


# ============================================================
# Persistence (atomic save/load)
# ============================================================


def test_save_and_load_state_round_trip(tmp_path):
    tr = PositionTracker()
    tr.add(_make_position("1"))
    tr.add(_make_position("2"))
    tr.on_fill("1", sl_order_id="10", tp_order_id="11", fill_price=78327.50, fill_qty=0.002)
    path = tmp_path / "positions-state.json"
    tr.save_state(path)
    assert path.exists()

    tr2 = PositionTracker()
    tr2.load_state(path)
    pendings = tr2.pending()
    actives = tr2.active()
    assert len(pendings) == 1
    assert pendings[0].order_id == "2"
    assert len(actives) == 1
    assert actives[0].sl_order_id == "10"


def test_save_uses_atomic_temp_rename(tmp_path, monkeypatch):
    """save_state önce <path>.tmp'e yazıp rename eder (crash-safe)."""
    tr = PositionTracker()
    tr.add(_make_position("1"))
    path = tmp_path / "positions-state.json"
    tr.save_state(path)
    # Tmp dosya kalmamış olmalı (rename başarılı)
    assert not (tmp_path / "positions-state.json.tmp").exists()
    assert path.exists()


def test_load_state_missing_file_returns_empty(tmp_path):
    tr = PositionTracker()
    tr.load_state(tmp_path / "does_not_exist.json")
    assert tr.pending() == []
    assert tr.active() == []


def test_load_state_corrupted_file_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("this is not json {{{")
    tr = PositionTracker()
    with pytest.raises(json.JSONDecodeError):
        tr.load_state(path)


def test_save_schema_includes_version(tmp_path):
    tr = PositionTracker()
    path = tmp_path / "state.json"
    tr.save_state(path)
    data = json.loads(path.read_text())
    assert "version" in data
    assert data["version"] == 1
    assert "saved_at" in data
    assert "positions" in data
