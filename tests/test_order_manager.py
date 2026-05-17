"""OrderManager testleri — sinyal → emir pipeline (Spec §4.2, §6).

FakeOrderClient ile end-to-end mock. GerÃ§ek Binance YOK.
"""

from __future__ import annotations

from datetime import datetime
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
from smc_engine.execution.order_manager import OrderManager, ProcessResult
from smc_engine.execution.position_sizing import OrderSizeBelowMinimum
from smc_engine.execution.position_tracker import PositionTracker
from smc_engine.integrations.binance.order_client import BinanceOrderError
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
# FakeOrderClient
# ============================================================


class FakeOrderClient:
    """Deterministic ExecutionAdapter for tests."""

    def __init__(self) -> None:
        self.placed: list[OrderRequest] = []
        self.cancelled: list[tuple[str, str]] = []
        self._order_id_seq = 1000
        self._order_book: dict[str, OrderResponse] = {}
        self._positions: dict[str, Position] = {}
        self._account = Account(equity=25.0, available_margin=20.0, used_margin=5.0)
        self._symbol_meta = SymbolMeta(
            symbol="BTCUSDT", tick_size=0.1, lot_size=0.001,
            min_qty=0.001, price_precision=1, qty_precision=3,
        )
        # Test hooks
        self.place_order_side_effect: Optional[Exception] = None
        self._fail_count = 0

    def place_order(self, request: OrderRequest) -> OrderResponse:
        self.placed.append(request)
        if self.place_order_side_effect is not None:
            self._fail_count += 1
            raise self.place_order_side_effect
        self._order_id_seq += 1
        oid = str(self._order_id_seq)
        resp = OrderResponse(
            order_id=oid, symbol=request.symbol, side=request.side,
            type=request.type, qty=request.qty, price=request.price,
            status=OrderStatus.NEW, fill_qty=0.0, fill_price=0.0,
            created_at=datetime.utcnow(),
        )
        self._order_book[oid] = resp
        return resp

    def cancel_order(self, symbol: str, order_id: str) -> OrderResponse:
        self.cancelled.append((symbol, order_id))
        if order_id in self._order_book:
            self._order_book[order_id].status = OrderStatus.CANCELED
        return self._order_book.get(order_id)

    def get_open_orders(self, symbol: Optional[str] = None) -> list[OrderResponse]:
        return [o for o in self._order_book.values() if o.status is OrderStatus.NEW]

    def get_order(self, symbol: str, order_id: str) -> OrderResponse:
        return self._order_book[order_id]

    def get_position(self, symbol: str) -> Position:
        return self._positions.get(symbol, Position(symbol=symbol, qty=0.0,
                                                     entry_price=0.0, unrealized_pnl=0.0,
                                                     liquidation_price=0.0))

    def get_account(self) -> Account:
        return self._account

    def get_symbol_meta(self, symbol: str) -> SymbolMeta:
        return self._symbol_meta

    # ---- test helpers ----

    def simulate_fill(self, order_id: str, fill_price: float, fill_qty: float) -> None:
        o = self._order_book[order_id]
        o.status = OrderStatus.FILLED
        o.fill_price = fill_price
        o.fill_qty = fill_qty


# ============================================================
# Helpers
# ============================================================


def _make_setup(symbol="BTCUSDT", direction=Direction.LONG, entry=78329.30,
                sl=77435.0, tp=79670.0, conf=0.80, factors=4):
    zone = Zone(kind=ZoneKind.DEMAND, top=78329.30, bottom=77670.5,
                timeframe=TimeFrame.H4, created_at=datetime(2026, 5, 17, 0),
                status=ZoneStatus.FRESH, origin_candle_ts=datetime(2026, 5, 17, 0),
                anchor=ZoneAnchor.BODY, age_bars=5)
    poi = POIRef(kind=POIKind.ZONE, ref=zone, htf_aligned=True, score_hint=1.0)
    return Setup(
        direction=direction, entry=entry, sl=sl,
        tp=[tp, tp + 1000, tp + 2000], tp_weights=[0.5, 0.3, 0.2],
        poi=poi, confirmation=None, bias_context=Bias.BULLISH,
        confluence_score=conf, rr=1.5,
        created_at=datetime(2026, 5, 17, 3, 15, 0),
        confluence_factor_count=factors,
    )


def _make_validated(symbol="BTCUSDT", **kw):
    setup = _make_setup(symbol=symbol, **kw)
    return ValidatedSetup(
        setup=setup, position_size=0.002, risk_amount=2.0,
        guard_log=["confluence", "regime", "deviation", "no_sl", "min_rr"],
    )


def _build_manager(tmp_path, client=None, kill_switch_triggered=False):
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
    if kill_switch_triggered:
        ks.trigger_external(reason="TEST_PRE_TRIGGER", details=[])

    tracker = PositionTracker()
    foc = client or FakeOrderClient()

    om = OrderManager(
        order_client=foc, position_tracker=tracker,
        audit_log=audit, kill_switch=ks, config=cfg,
        symbol_to_btc="BTCUSDT",  # placeholder; per-symbol meta lookup
    )
    return om, foc, tracker, audit, ks


# ============================================================
# process_setup happy path
# ============================================================


def test_process_setup_places_order_happy_path(tmp_path):
    om, foc, tracker, audit, ks = _build_manager(tmp_path)
    vs = _make_validated()
    result = om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 17, 3, 15))
    assert result is ProcessResult.PLACED
    assert len(foc.placed) == 1
    req = foc.placed[0]
    assert req.symbol == "BTCUSDT"
    assert req.side is OrderSide.BUY
    assert req.type is OrderType.LIMIT
    # Tracker'a kayıt
    pendings = tracker.pending()
    assert len(pendings) == 1


def test_process_setup_short_uses_sell_side(tmp_path):
    om, foc, _, _, _ = _build_manager(tmp_path)
    vs = _make_validated(direction=Direction.SHORT)
    om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 17, 3, 15))
    assert foc.placed[0].side is OrderSide.SELL


def test_process_setup_emits_audit(tmp_path):
    om, foc, _, audit, _ = _build_manager(tmp_path)
    vs = _make_validated()
    om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 17, 3, 15))
    log_files = list((tmp_path / "audit").glob("trades-*.jsonl"))
    content = log_files[0].read_text()
    assert "ORDER_PLACED" in content


# ============================================================
# process_setup skipped paths
# ============================================================


def test_process_setup_skipped_when_kill_switch_triggered(tmp_path):
    om, foc, _, audit, _ = _build_manager(tmp_path, kill_switch_triggered=True)
    vs = _make_validated()
    result = om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 17, 3, 15))
    assert result is ProcessResult.SKIPPED_KILL_SWITCH
    assert foc.placed == []
    log_files = list((tmp_path / "audit").glob("trades-*.jsonl"))
    content = log_files[0].read_text()
    assert "SETUP_SKIPPED_KILL_SWITCH" in content


def test_process_setup_size_below_minimum_skips(tmp_path):
    """Çok küçük risk → OrderSizeBelowMinimum → SKIPPED + audit."""
    om, foc, _, _, _ = _build_manager(tmp_path)
    om.config.execution_risk_per_trade_dollar = 0.001  # tiny
    vs = _make_validated()
    result = om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 17, 3, 15))
    assert result is ProcessResult.SIZING_FAILED
    assert foc.placed == []


def test_process_setup_binance_reject_audits(tmp_path):
    om, foc, _, _, _ = _build_manager(tmp_path)
    foc.place_order_side_effect = BinanceOrderError(
        code=-2010, message="REJECTED", retryable=False,
    )
    vs = _make_validated()
    result = om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 17, 3, 15))
    assert result is ProcessResult.REJECTED
    log_files = list((tmp_path / "audit").glob("trades-*.jsonl"))
    content = log_files[0].read_text()
    assert "ORDER_REJECTED" in content


def test_process_setup_kill_switch_signal_triggers_external(tmp_path):
    """-1013 PRICE_FILTER → kill_switch_signal=True → trigger external."""
    om, foc, _, _, ks = _build_manager(tmp_path)
    foc.place_order_side_effect = BinanceOrderError(
        code=-1013, message="PRICE_FILTER", retryable=False, kill_switch_signal=True,
    )
    vs = _make_validated()
    om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 17, 3, 15))
    assert ks.is_triggered() is True


# ============================================================
# Timeout watcher
# ============================================================


def test_timeout_watcher_cancels_expired_pending(tmp_path):
    om, foc, tracker, audit, _ = _build_manager(tmp_path)
    vs = _make_validated()
    at_bar = datetime(2026, 5, 17, 3, 15)
    om.process_setup(vs, symbol="BTCUSDT", at_bar=at_bar)

    # Simulate 61 dakika sonra (timeout = at_bar + 60 dk)
    om.tick_timeout_watcher(now=datetime(2026, 5, 17, 4, 16))
    assert len(foc.cancelled) == 1
    aborted = tracker.closed_or_aborted()
    assert len(aborted) == 1
    assert aborted[0].abort_reason == "TIMEOUT"


def test_timeout_watcher_skips_unexpired_pending(tmp_path):
    om, foc, tracker, _, _ = _build_manager(tmp_path)
    vs = _make_validated()
    at_bar = datetime(2026, 5, 17, 3, 15)
    om.process_setup(vs, symbol="BTCUSDT", at_bar=at_bar)
    # 30 dakika sonra — henüz timeout deÄil
    om.tick_timeout_watcher(now=datetime(2026, 5, 17, 3, 45))
    assert foc.cancelled == []
    assert len(tracker.pending()) == 1


# ============================================================
# Fill polling
# ============================================================


def test_fill_polling_transitions_pending_to_active_with_sl_tp(tmp_path):
    om, foc, tracker, _, _ = _build_manager(tmp_path)
    vs = _make_validated()
    at_bar = datetime(2026, 5, 17, 3, 15)
    om.process_setup(vs, symbol="BTCUSDT", at_bar=at_bar)

    pending = tracker.pending()[0]
    foc.simulate_fill(pending.order_id, fill_price=78327.50, fill_qty=0.002)

    om.tick_fill_polling()
    actives = tracker.active()
    assert len(actives) == 1
    p = actives[0]
    assert p.fill_price == 78327.50
    # SL + TP order'lar konuldu (3 place toplam: original + SL + TP)
    assert len(foc.placed) == 3
    assert any(r.type is OrderType.STOP_MARKET for r in foc.placed)
    sl_orders = [r for r in foc.placed if r.type is OrderType.STOP_MARKET]
    assert len(sl_orders) == 1
    assert sl_orders[0].side is OrderSide.SELL  # LONG entry → SELL SL
