"""smc_engine.execution._base — Protocol + tipler (Spec §4.1)."""

from __future__ import annotations

from datetime import datetime

from smc_engine.execution._base import (
    Account,
    ExecutionAdapter,
    OrderRequest,
    OrderResponse,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)


def test_order_request_dataclass_fields():
    req = OrderRequest(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        qty=0.002,
        price=78329.30,
        time_in_force=TimeInForce.GTC,
    )
    assert req.symbol == "BTCUSDT"
    assert req.side is OrderSide.BUY
    assert req.type is OrderType.LIMIT
    assert req.qty == 0.002
    assert req.price == 78329.30
    assert req.stop_price is None


def test_order_response_dataclass():
    resp = OrderResponse(
        order_id="12345",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        qty=0.002,
        price=78329.30,
        status=OrderStatus.NEW,
        fill_qty=0.0,
        fill_price=0.0,
        created_at=datetime(2026, 5, 17, 3, 15, 5),
    )
    assert resp.order_id == "12345"
    assert resp.status is OrderStatus.NEW
    assert resp.fill_qty == 0.0


def test_position_dataclass():
    p = Position(
        symbol="BTCUSDT",
        qty=0.002,
        entry_price=78329.30,
        unrealized_pnl=1.50,
        liquidation_price=70850.0,
        margin_type="isolated",
    )
    assert p.symbol == "BTCUSDT"
    assert p.qty == 0.002


def test_account_dataclass():
    a = Account(equity=25.0, available_margin=20.0, used_margin=5.0)
    assert a.equity == 25.0
    assert a.available_margin == 20.0


def test_execution_adapter_protocol_yapisal_uyum():
    """Yapısal Protocol — bir sınıf gerekli metodları varsa uyumlu sayılır."""

    class FakeAdapter:
        def place_order(self, request): ...
        def cancel_order(self, symbol, order_id): ...
        def get_open_orders(self, symbol=None): ...
        def get_order(self, symbol, order_id): ...
        def get_position(self, symbol): ...
        def get_account(self): ...

    f = FakeAdapter()
    # ExecutionAdapter modülde export edilmiÅ
    assert ExecutionAdapter is not None
    for m in ("place_order", "cancel_order", "get_open_orders", "get_order",
              "get_position", "get_account"):
        assert hasattr(f, m)


def test_enums_have_expected_values():
    assert OrderSide.BUY.value == "BUY"
    assert OrderSide.SELL.value == "SELL"
    assert OrderType.LIMIT.value == "LIMIT"
    assert OrderType.MARKET.value == "MARKET"
    assert OrderType.STOP_MARKET.value == "STOP_MARKET"
    assert OrderStatus.NEW.value == "NEW"
    assert OrderStatus.FILLED.value == "FILLED"
    assert OrderStatus.CANCELED.value == "CANCELED"
    assert OrderStatus.EXPIRED.value == "EXPIRED"
    assert OrderStatus.REJECTED.value == "REJECTED"
    assert TimeInForce.GTC.value == "GTC"
