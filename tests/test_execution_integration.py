"""End-to-end integration tests — FakeOrderClient ile tüm execution stack.

GerÃ§ek Binance YOK. Signal → place → fill → TP/SL → close + audit + state.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Optional
from unittest.mock import MagicMock

import pytest

from smc_engine.config import SMCConfig
from smc_engine.execution._base import (
    Account,
    OrderRequest,
    OrderResponse,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)
from smc_engine.execution.audit_log import AuditLog
from smc_engine.execution.kill_switch import KillSwitch
from smc_engine.execution.order_manager import OrderManager
from smc_engine.execution.position_tracker import PositionTracker, PositionState
from smc_engine.types import (
    Bias,
    Direction,
    POIKind,
    POIRef,
    Setup,
    SymbolMeta,
    TimeFrame,
    ValidatedSetup,
    Zone,
    ZoneAnchor,
    ZoneKind,
    ZoneStatus,
)


# ============================================================
# Fake stack
# ============================================================


class FakeOrderClient:
    def __init__(self):
        self._oid = 1000
        self._book: dict[str, OrderResponse] = {}
        self._positions: dict[str, Position] = {}
        self._account = Account(equity=25.0, available_margin=20.0, used_margin=5.0)
        self._meta = SymbolMeta(symbol="BTCUSDT", tick_size=0.1, lot_size=0.001,
                                 min_qty=0.001, price_precision=1, qty_precision=3)
        self.placed: list[OrderRequest] = []

    def _next_oid(self) -> str:
        self._oid += 1
        return str(self._oid)

    def place_order(self, request: OrderRequest) -> OrderResponse:
        self.placed.append(request)
        oid = self._next_oid()
        resp = OrderResponse(
            order_id=oid, symbol=request.symbol, side=request.side,
            type=request.type, qty=request.qty, price=request.price,
            status=OrderStatus.NEW,
        )
        self._book[oid] = resp
        return resp

    def cancel_order(self, symbol, order_id):
        if order_id in self._book:
            self._book[order_id].status = OrderStatus.CANCELED
        return self._book.get(order_id)

    def get_open_orders(self, symbol=None):
        return [o for o in self._book.values() if o.status is OrderStatus.NEW]

    def get_order(self, symbol, order_id):
        return self._book[order_id]

    def get_position(self, symbol):
        return self._positions.get(
            symbol, Position(symbol=symbol, qty=0.0, entry_price=0.0,
                              unrealized_pnl=0.0, liquidation_price=0.0),
        )

    def get_account(self):
        return self._account

    def get_symbol_meta(self, symbol):
        return self._meta

    # ---- test hooks ----

    def fill(self, order_id: str, fill_price: float, fill_qty: float) -> None:
        o = self._book[order_id]
        o.status = OrderStatus.FILLED
        o.fill_price = fill_price
        o.fill_qty = fill_qty

    def set_position(self, symbol: str, qty: float, entry_price: float = 0.0) -> None:
        self._positions[symbol] = Position(
            symbol=symbol, qty=qty, entry_price=entry_price,
            unrealized_pnl=0.0, liquidation_price=0.0,
        )


def _make_validated(symbol="BTCUSDT", direction=Direction.LONG):
    zone = Zone(kind=ZoneKind.DEMAND, top=78329.30, bottom=77670.5,
                timeframe=TimeFrame.H4, created_at=datetime(2026, 5, 17),
                status=ZoneStatus.FRESH, origin_candle_ts=datetime(2026, 5, 17),
                anchor=ZoneAnchor.BODY, age_bars=5)
    poi = POIRef(kind=POIKind.ZONE, ref=zone, htf_aligned=True, score_hint=1.0)
    setup = Setup(
        direction=direction, entry=78329.30, sl=77435.0,
        tp=[79670.0, 80671.0, 82110.0], tp_weights=[0.5, 0.3, 0.2],
        poi=poi, confirmation=None, bias_context=Bias.BULLISH,
        confluence_score=0.80, rr=1.5,
        created_at=datetime(2026, 5, 17, 3, 15),
        confluence_factor_count=4,
    )
    return ValidatedSetup(setup=setup, position_size=0.002, risk_amount=2.0,
                          guard_log=["confluence", "regime", "deviation", "no_sl", "min_rr"])


def _build_stack(tmp_path):
    cfg = SMCConfig()
    cfg.execution_enabled = True
    cfg.execution_testnet = True
    cfg.execution_risk_per_trade_dollar = 2.0
    cfg.execution_leverage = 10
    cfg.execution_order_timeout_minutes = 60

    audit = AuditLog(log_dir=str(tmp_path / "audit"), engine_sha="x", testnet=True)
    ks = KillSwitch(
        consecutive_loss_threshold=3, daily_loss_threshold=5.0, equity_minimum=15.0,
        state_path=tmp_path / "ks.json", audit_log=audit,
    )
    tracker = PositionTracker()
    foc = FakeOrderClient()
    om = OrderManager(
        order_client=foc, position_tracker=tracker,
        audit_log=audit, kill_switch=ks, config=cfg,
    )
    return cfg, foc, om, tracker, audit, ks


# ============================================================
# Happy path: signal → place → fill → TP_HIT → audit
# ============================================================


def test_full_happy_path_tp_hit(tmp_path):
    cfg, foc, om, tracker, audit, ks = _build_stack(tmp_path)
    vs = _make_validated()
    at_bar = datetime(2026, 5, 17, 3, 15)

    # 1) Place
    om.process_setup(vs, symbol="BTCUSDT", at_bar=at_bar)
    pending = tracker.pending()
    assert len(pending) == 1
    entry_order_id = pending[0].order_id

    # 2) Simulate fill
    foc.fill(entry_order_id, fill_price=78327.50, fill_qty=0.002)
    foc.set_position("BTCUSDT", qty=0.002, entry_price=78327.50)
    om.tick_fill_polling()

    actives = tracker.active()
    assert len(actives) == 1
    p = actives[0]
    assert p.fill_price == 78327.50
    assert p.sl_order_id is not None
    assert p.tp_order_id is not None

    # 3) Simulate TP hit
    foc.fill(p.tp_order_id, fill_price=79670.0, fill_qty=0.002)
    foc.set_position("BTCUSDT", qty=0.0)  # Position closed
    om.tick_fill_polling()

    closed = tracker.closed_or_aborted()
    assert len(closed) == 1
    c = closed[0]
    assert c.state is PositionState.CLOSED_WIN
    assert c.exit_price == 79670.0
    # PnL: (79670 - 78327.50) * 0.002 = 2.685
    assert c.pnl_dollar == pytest.approx(2.685, rel=1e-3)

    # 4) Audit trail
    audit_file = list((tmp_path / "audit").glob("trades-*.jsonl"))[0]
    events = [json.loads(l)["event"] for l in audit_file.read_text().splitlines() if l.strip()]
    assert "ORDER_PLACED" in events
    assert "ORDER_FILLED" in events
    assert "TP_HIT" in events

    # 5) Kill switch — win → consecutive_losses reset to 0
    assert ks._state.consecutive_losses == 0  # noqa: SLF001


def test_full_path_sl_hit_increments_consecutive_losses(tmp_path):
    cfg, foc, om, tracker, audit, ks = _build_stack(tmp_path)
    vs = _make_validated()
    om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 17, 3, 15))
    pid = tracker.pending()[0].order_id

    foc.fill(pid, fill_price=78327.50, fill_qty=0.002)
    foc.set_position("BTCUSDT", qty=0.002, entry_price=78327.50)
    om.tick_fill_polling()

    p = tracker.active()[0]
    # SL hit (price dropped to SL)
    foc.fill(p.sl_order_id, fill_price=77435.0, fill_qty=0.002)
    foc.set_position("BTCUSDT", qty=0.0)
    om.tick_fill_polling()

    closed = tracker.closed_or_aborted()[0]
    assert closed.state is PositionState.CLOSED_LOSS
    assert closed.pnl_dollar < 0
    assert ks._state.consecutive_losses == 1  # noqa: SLF001


# ============================================================
# Timeout path
# ============================================================


def test_timeout_path(tmp_path):
    cfg, foc, om, tracker, audit, ks = _build_stack(tmp_path)
    vs = _make_validated()
    at_bar = datetime(2026, 5, 17, 3, 15)
    om.process_setup(vs, symbol="BTCUSDT", at_bar=at_bar)
    pid = tracker.pending()[0].order_id

    # 61 dakika sonra timeout watcher
    om.tick_timeout_watcher(now=at_bar + timedelta(minutes=61))

    closed = tracker.closed_or_aborted()
    assert closed[0].state is PositionState.ABORTED
    assert closed[0].abort_reason == "TIMEOUT"
    # Cancel call yapıldı
    assert foc._book[pid].status is OrderStatus.CANCELED


# ============================================================
# Restart recovery
# ============================================================


def test_restart_recovery_loads_persisted_state(tmp_path):
    cfg, foc, om, tracker, audit, ks = _build_stack(tmp_path)
    vs = _make_validated()
    om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 17, 3, 15))

    # Save state
    state_path = tmp_path / "state.json"
    tracker.save_state(state_path)
    # Verify file exists
    assert state_path.exists()

    # New tracker — load
    tracker2 = PositionTracker()
    tracker2.load_state(state_path)
    pendings = tracker2.pending()
    assert len(pendings) == 1
    assert pendings[0].symbol == "BTCUSDT"


def test_kill_switch_persists_across_restart(tmp_path):
    """Kill switch tetiklendikten sonra restart → hâlâ tetiklenmiÅ."""
    cfg, foc, om, tracker, audit, ks = _build_stack(tmp_path)
    # 3 consecutive loss
    from smc_engine.execution.kill_switch import TradeResult
    acc = foc.get_account()
    ks.check_after_trade(TradeResult("a", -1.0), acc)
    ks.check_after_trade(TradeResult("b", -1.0), acc)
    ks.check_after_trade(TradeResult("c", -1.0), acc)
    assert ks.is_triggered() is True

    # Yeni instance — state dosyasını okuduğunda hâlâ triggered
    ks2 = KillSwitch(
        consecutive_loss_threshold=3, daily_loss_threshold=5.0, equity_minimum=15.0,
        state_path=tmp_path / "ks.json", audit_log=audit,
    )
    assert ks2.is_triggered() is True


# ============================================================
# Kill switch skip
# ============================================================


def test_kill_switch_triggered_skips_subsequent_setups(tmp_path):
    cfg, foc, om, tracker, audit, ks = _build_stack(tmp_path)
    # Trigger kill switch externally
    ks.trigger_external(reason="TEST", details=[])

    vs = _make_validated()
    from smc_engine.execution.order_manager import ProcessResult
    result = om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 17, 3, 15))
    assert result is ProcessResult.SKIPPED_KILL_SWITCH
    assert foc.placed == []  # no order placed


# ============================================================
# Multi-tick sequence (3 setups in a row)
# ============================================================


def test_multiple_setups_processed_sequentially(tmp_path):
    """3 ardışık setup → 3 PENDING. max_concurrent guard (İş 1 2026-05-19)
    default 1 olduğu için bu test no-cap mode'u zorlar; niyeti
    `process_setup` çağrı sırasının doğru çalışmasını doğrulamak,
    cap davranışını değil."""
    cfg, foc, om, tracker, audit, ks = _build_stack(tmp_path)
    cfg.execution_max_concurrent_positions = 0  # cap disabled
    for i in range(3):
        vs = _make_validated()
        om.process_setup(
            vs, symbol="BTCUSDT",
            at_bar=datetime(2026, 5, 17, 3, 15 + i*15),
        )
    pendings = tracker.pending()
    assert len(pendings) == 3
