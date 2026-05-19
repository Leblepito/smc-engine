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
        # Bug D testleri: belirli OrderType için fail injection (örn. SADECE
        # STOP_MARKET fail etsin, LIMIT entry başarılı geçsin)
        self.fail_on_type: Optional[OrderType] = None
        self.fail_on_type_exception: Optional[Exception] = None

    def place_order(self, request: OrderRequest) -> OrderResponse:
        self.placed.append(request)
        if (self.fail_on_type is not None
                and request.type is self.fail_on_type
                and self.fail_on_type_exception is not None):
            raise self.fail_on_type_exception
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

    def get_mark_price(self, symbol: str) -> float:
        """Bug E-B: process_setup pre-place mark guard için.

        Test'ler self._mark_price üzerinden inject eder; default 78329.30
        (Setup default entry'siyle eşit → "edge" durumu).
        """
        return getattr(self, "_mark_price", 78329.30)

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
# Bug A (2026-05-18): position_mode wiring (Hedge vs One-way)
# ============================================================


def test_process_setup_one_way_mode_omits_position_side(tmp_path):
    """Default ONE_WAY: OrderRequest.position_side=None."""
    om, foc, _, _, _ = _build_manager(tmp_path)
    om.config.execution_position_mode = "ONE_WAY"
    vs = _make_validated(direction=Direction.LONG)
    om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 17, 3, 15))
    assert foc.placed[0].position_side is None


def test_process_setup_hedge_mode_long_sets_position_side_LONG(tmp_path):
    """HEDGE + LONG → entry order positionSide='LONG'."""
    om, foc, _, _, _ = _build_manager(tmp_path)
    om.config.execution_position_mode = "HEDGE"
    vs = _make_validated(direction=Direction.LONG)
    om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 17, 3, 15))
    assert foc.placed[0].position_side == "LONG"


def test_process_setup_hedge_mode_short_sets_position_side_SHORT(tmp_path):
    """HEDGE + SHORT → entry order positionSide='SHORT'."""
    om, foc, _, _, _ = _build_manager(tmp_path)
    om.config.execution_position_mode = "HEDGE"
    vs = _make_validated(direction=Direction.SHORT)
    om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 17, 3, 15))
    assert foc.placed[0].position_side == "SHORT"


def test_on_fill_hedge_mode_sl_tp_orders_use_position_side(tmp_path):
    """HEDGE + LONG fill → SL ve TP order'larında da positionSide='LONG'.

    Kritik: SL/TP exit order'ları side='SELL' alır ama positionSide hala
    'LONG' olmalı (aynı LONG pozisyonu kapatıyor). Yoksa -4061 burada da olur.
    """
    om, foc, tracker, _, _ = _build_manager(tmp_path)
    om.config.execution_position_mode = "HEDGE"
    vs = _make_validated(direction=Direction.LONG)
    at_bar = datetime(2026, 5, 17, 3, 15)
    om.process_setup(vs, symbol="BTCUSDT", at_bar=at_bar)
    # Entry order'ı fill et
    pending = tracker.pending()[0]
    foc.simulate_fill(pending.order_id, fill_price=78327.5, fill_qty=pending.qty)
    om.tick_fill_polling()
    # Üç order olmalı: entry + SL + TP. Hepsi positionSide='LONG'.
    assert len(foc.placed) == 3
    for req in foc.placed:
        assert req.position_side == "LONG", f"order type={req.type} side={req.side} positionSide={req.position_side}"


# ============================================================
# Bug D (2026-05-18): Atomic SL/TP placement + rollback
# Sebep: Production incident — SL place fail → main order PENDING kalıyor,
# polling sonsuz retry yapıyor, fiyat seviyeye gelince SL'siz position açılıyor.
# Beklenen: fail → main cancel + emergency close + tracker ABORTED + audit
# ROLLBACK; bir daha asla retry edilmez.
# ============================================================


def test_process_setup_does_not_place_sl_before_fill(tmp_path):
    """process_setup sadece LIMIT entry koyar; SL ve TP fill GELENE KADAR BEKLENMELI."""
    om, foc, tracker, _, _ = _build_manager(tmp_path)
    vs = _make_validated()
    om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 17, 3, 15))
    # Yalnızca 1 order (LIMIT entry) — SL/TP placement YOK.
    assert len(foc.placed) == 1
    assert foc.placed[0].type is OrderType.LIMIT
    # Tracker'da PENDING; ACTIVE'e geçmedi.
    assert len(tracker.pending()) == 1
    assert len(tracker.active()) == 0


def test_on_fill_places_sl_and_tp_atomically_on_success(tmp_path):
    """Fill geldiğinde SL+TP başarıyla atomik konur → tracker ACTIVE."""
    om, foc, tracker, _, _ = _build_manager(tmp_path)
    vs = _make_validated()
    om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 17, 3, 15))
    pending = tracker.pending()[0]
    foc.simulate_fill(pending.order_id, fill_price=78327.5, fill_qty=pending.qty)
    om.tick_fill_polling()
    # Entry + SL + TP = 3 order
    assert len(foc.placed) == 3
    assert len(tracker.active()) == 1
    assert len(tracker.pending()) == 0


def test_on_fill_sl_failure_rolls_back_main_order(tmp_path):
    """SL place fail → main fill'i emergency close + tracker ABORTED + audit ROLLBACK.

    Bu Bug D'nin core senaryosu. Önceki davranış: SL fail → return → main
    pozisyon SL'siz açık kalır.
    """
    om, foc, tracker, audit, _ = _build_manager(tmp_path)
    vs = _make_validated()
    om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 17, 3, 15))
    pending = tracker.pending()[0]
    foc.simulate_fill(pending.order_id, fill_price=78327.5, fill_qty=pending.qty)

    # SL placement fail injection (-1111 Precision is over the maximum)
    foc.fail_on_type = OrderType.STOP_MARKET
    foc.fail_on_type_exception = BinanceOrderError(
        code=-1111, message="Precision is over the maximum.", retryable=False,
    )

    om.tick_fill_polling()

    # 1) Tracker ABORTED (NOT PENDING, NOT ACTIVE — finalized)
    assert len(tracker.pending()) == 0, "PENDING kalmamalı (polling sonsuz retry yapardı)"
    assert len(tracker.active()) == 0, "SL/TP eksikse ACTIVE'e geçmemeli"
    aborted = tracker.closed_or_aborted()
    assert len(aborted) == 1
    assert aborted[0].abort_reason and "SL" in aborted[0].abort_reason

    # 2) Emergency close: SL fail sonrası market exit denenmiş — opposite side MARKET
    #    LIMIT BUY entry → emergency SELL MARKET
    market_exits = [r for r in foc.placed if r.type is OrderType.MARKET]
    assert len(market_exits) == 1, "emergency MARKET close emri eksik"
    assert market_exits[0].side is OrderSide.SELL

    # 3) Audit ROLLBACK
    log_files = list((tmp_path / "audit").glob("trades-*.jsonl"))
    content = log_files[0].read_text()
    assert "ORDER_ROLLBACK" in content


def test_on_fill_tp_failure_rolls_back_after_sl_placed(tmp_path):
    """SL başarılı, TP fail → SL cancel + emergency close + tracker ABORTED."""
    om, foc, tracker, audit, _ = _build_manager(tmp_path)
    vs = _make_validated()
    om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 17, 3, 15))
    pending = tracker.pending()[0]
    foc.simulate_fill(pending.order_id, fill_price=78327.5, fill_qty=pending.qty)

    # TP fail (LIMIT exit). NOT: SL de LIMIT type değil STOP_MARKET — fail_on_type
    # LIMIT olursa hem entry hem TP fail eder. O yüzden TP placement'i ayrı işaretle.
    # Strateji: SL başarılı (STOP_MARKET geçer), sonra TP (LIMIT) için fail set et.
    original_place = foc.place_order
    call_state = {"n": 0}

    def fail_third(req):
        call_state["n"] += 1
        # 1=entry (already placed), 2=SL (STOP_MARKET success), 3=TP (LIMIT) fail
        # Aslında entry zaten placed listede; bu çağrı _on_fill içindeki SL+TP.
        # 1. çağrı = SL, 2. çağrı = TP → 2. çağrıda fail.
        if call_state["n"] == 2:
            raise BinanceOrderError(code=-2010, message="REJECTED", retryable=False)
        return original_place(req)

    foc.place_order = fail_third  # type: ignore[assignment]

    om.tick_fill_polling()

    # ABORTED — TP fail → SL cancel + main emergency close + ABORTED
    aborted = tracker.closed_or_aborted()
    assert len(aborted) == 1
    assert aborted[0].abort_reason and "TP" in aborted[0].abort_reason

    # Audit ROLLBACK
    log_files = list((tmp_path / "audit").glob("trades-*.jsonl"))
    content = log_files[0].read_text()
    assert "ORDER_ROLLBACK" in content


# ============================================================
# Bug E-B (2026-05-19): Pre-place mark price guard
# LIMIT entry'nin anlık fill durumu → SL/TP -2021 reject + rollback. Guard
# process_setup öncesi mark_price'ı sorgular; entry seviyesini geçmişse
# SETUP_SKIPPED_PRICE_PASSED audit + skip (order place yok).
# ============================================================


def test_process_setup_long_skips_when_mark_below_entry(tmp_path):
    """LONG: mark < entry → fiyat geçmiş → SKIPPED_PRICE_PASSED, order place YOK."""
    om, foc, _, audit, _ = _build_manager(tmp_path)
    om.config.execution_pre_place_mark_guard = True
    foc._mark_price = 78000.0  # entry 78329.30 → mark 78000 (LONG için passed)
    vs = _make_validated(direction=Direction.LONG)
    result = om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 19, 3, 15))
    assert result is ProcessResult.SKIPPED_PRICE_PASSED
    assert foc.placed == []
    log_files = list((tmp_path / "audit").glob("trades-*.jsonl"))
    content = log_files[0].read_text()
    assert "SETUP_SKIPPED_PRICE_PASSED" in content


def test_process_setup_short_skips_when_mark_above_entry(tmp_path):
    """SHORT: mark > entry → fiyat geçmiş → SKIPPED_PRICE_PASSED."""
    om, foc, _, _, _ = _build_manager(tmp_path)
    om.config.execution_pre_place_mark_guard = True
    foc._mark_price = 78600.0  # entry 78329.30 → mark 78600 (SHORT için passed)
    vs = _make_validated(direction=Direction.SHORT)
    result = om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 19, 3, 15))
    assert result is ProcessResult.SKIPPED_PRICE_PASSED
    assert foc.placed == []


def test_process_setup_long_proceeds_when_mark_above_entry(tmp_path):
    """LONG: mark > entry → henüz ulaşmadı → normal place."""
    om, foc, _, _, _ = _build_manager(tmp_path)
    om.config.execution_pre_place_mark_guard = True
    foc._mark_price = 78600.0  # entry 78329.30 → mark 78600 (LONG için ulaşmadı)
    vs = _make_validated(direction=Direction.LONG)
    result = om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 19, 3, 15))
    assert result is ProcessResult.PLACED
    assert len(foc.placed) == 1


def test_process_setup_short_proceeds_when_mark_below_entry(tmp_path):
    """SHORT: mark < entry → henüz ulaşmadı → normal place."""
    om, foc, _, _, _ = _build_manager(tmp_path)
    om.config.execution_pre_place_mark_guard = True
    foc._mark_price = 78000.0
    vs = _make_validated(direction=Direction.SHORT)
    result = om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 19, 3, 15))
    assert result is ProcessResult.PLACED


def test_process_setup_long_at_exact_entry_proceeds(tmp_path):
    """LONG: mark == entry → sınırda kabul (just-touched edge); place et."""
    om, foc, _, _, _ = _build_manager(tmp_path)
    om.config.execution_pre_place_mark_guard = True
    foc._mark_price = 78329.30  # tam entry
    vs = _make_validated(direction=Direction.LONG)
    result = om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 19, 3, 15))
    assert result is ProcessResult.PLACED


def test_process_setup_guard_disabled_proceeds_unconditionally(tmp_path):
    """Guard kapalıyken mark price'a bakılmaz."""
    om, foc, _, _, _ = _build_manager(tmp_path)
    om.config.execution_pre_place_mark_guard = False
    foc._mark_price = 78000.0  # LONG için "passed" durumu
    vs = _make_validated(direction=Direction.LONG)
    result = om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 19, 3, 15))
    # Guard kapalı, normal flow — PLACED. (Önce position_sizing geçer.)
    assert result is ProcessResult.PLACED


# ============================================================
# Bug E-A (2026-05-19): post-only LIMIT entry (TimeInForce.GTX)
# Pre-place guard'ı geçen ama hala yanlış tarafta olan setup'lar
# Binance tarafında place ETMEDEN reddedilmeli (atomic rollback'ten
# daha ucuz — order yok, emergency close yok).
# ============================================================


def test_process_setup_post_only_enabled_uses_gtx_on_limit_entry(tmp_path):
    """execution_post_only=True → LIMIT entry GTX (post-only) ile gönderilir."""
    from smc_engine.execution._base import TimeInForce
    om, foc, _, _, _ = _build_manager(tmp_path)
    om.config.execution_post_only = True
    om.config.execution_pre_place_mark_guard = False  # E-B'yi izole et
    vs = _make_validated(direction=Direction.LONG)
    om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 19, 3, 15))
    assert len(foc.placed) == 1
    assert foc.placed[0].time_in_force is TimeInForce.GTX


def test_process_setup_post_only_disabled_uses_gtc(tmp_path):
    """execution_post_only=False → klasik GTC (geri uyumluluk)."""
    from smc_engine.execution._base import TimeInForce
    om, foc, _, _, _ = _build_manager(tmp_path)
    om.config.execution_post_only = False
    om.config.execution_pre_place_mark_guard = False
    vs = _make_validated(direction=Direction.LONG)
    om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 19, 3, 15))
    assert foc.placed[0].time_in_force is TimeInForce.GTC


def test_on_fill_tp_order_uses_gtc_not_gtx(tmp_path):
    """TP exit order'ı GTX OLMAMALI — TP fiyatına ulaşıldığında likidite
    olmayabilir, GTX reject yapar. Exit'ler taker tolerant kalmalı."""
    from smc_engine.execution._base import TimeInForce
    om, foc, tracker, _, _ = _build_manager(tmp_path)
    om.config.execution_post_only = True
    om.config.execution_pre_place_mark_guard = False
    vs = _make_validated(direction=Direction.LONG)
    om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 19, 3, 15))
    pending = tracker.pending()[0]
    foc.simulate_fill(pending.order_id, fill_price=78327.5, fill_qty=pending.qty)
    om.tick_fill_polling()
    # placed[0] entry (GTX), placed[1] SL (STOP_MARKET, TimeInForce irrelevant),
    # placed[2] TP (LIMIT, GTC)
    tp_orders = [r for r in foc.placed if r.type is OrderType.LIMIT and r is not foc.placed[0]]
    assert len(tp_orders) == 1
    assert tp_orders[0].time_in_force is TimeInForce.GTC


def test_process_setup_mark_price_fetch_failure_proceeds_safely(tmp_path):
    """get_mark_price exception → guard fail-safe: order yine place edilir.

    Mark fetch'i hata fırlatırsa guard'ı atla; downstream Binance -2021
    reject zaten atomic rollback flow'una düşer (defense-in-depth A).
    """
    om, foc, _, audit, _ = _build_manager(tmp_path)
    om.config.execution_pre_place_mark_guard = True

    def _boom(symbol):
        raise BinanceOrderError(code=-1, message="network down", retryable=True)
    foc.get_mark_price = _boom  # type: ignore[assignment]
    vs = _make_validated(direction=Direction.LONG)
    result = om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 19, 3, 15))
    # Fail-safe: order place edilmiş
    assert result is ProcessResult.PLACED
    # Audit'te uyarı
    log_files = list((tmp_path / "audit").glob("trades-*.jsonl"))
    content = log_files[0].read_text()
    assert "MARK_PRICE_FETCH_FAILED" in content


def test_aborted_state_not_retried_by_polling(tmp_path):
    """ABORTED state'te polling SL retry YAPMAMALI (önceki davranış sonsuz retry idi)."""
    om, foc, tracker, _, _ = _build_manager(tmp_path)
    vs = _make_validated()
    om.process_setup(vs, symbol="BTCUSDT", at_bar=datetime(2026, 5, 17, 3, 15))
    pending = tracker.pending()[0]
    foc.simulate_fill(pending.order_id, fill_price=78327.5, fill_qty=pending.qty)

    # SL fail → ABORTED beklenir
    foc.fail_on_type = OrderType.STOP_MARKET
    foc.fail_on_type_exception = BinanceOrderError(
        code=-1111, message="Precision is over the maximum.", retryable=False,
    )
    om.tick_fill_polling()
    placed_after_first = len(foc.placed)
    aborted_count = len(tracker.closed_or_aborted())
    assert aborted_count == 1

    # SL injection KALDIRILSA bile bir sonraki polling tick'i ABORTED'a dokunmamalı.
    foc.fail_on_type = None
    om.tick_fill_polling()
    om.tick_fill_polling()
    om.tick_fill_polling()
    # Yeni order GİTMEDİ — retry yok.
    assert len(foc.placed) == placed_after_first, "ABORTED'a yeni SL/TP gönderildi (retry bug)"


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
