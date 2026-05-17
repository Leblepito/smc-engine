"""BinanceOrderClient testleri — python-binance write API wrap (Spec §4.1, §12.1).

TÃ¼m REST çağrıları mock'lanır. GerÃ§ek Binance'e GIDILMEZ.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from smc_engine.execution._base import (
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from smc_engine.integrations.binance.order_client import (
    BinanceOrderClient,
    BinanceOrderError,
)


# =========================================================
# X1.1 — Constructor + URL switching + mainnet guard hook
# =========================================================


def test_constructor_testnet_url_switching():
    """testnet=True → testnet base URL kullanılır."""
    with patch("smc_engine.integrations.binance.order_client.Client") as MockClient:
        BinanceOrderClient(api_key="a", api_secret="b", testnet=True)
        # python-binance Client(testnet=True) → futures_testnet base URL otomatik
        args, kwargs = MockClient.call_args
        assert kwargs.get("testnet") is True


def test_constructor_mainnet_without_config_raises():
    """testnet=False ise config zorunlu (MainnetGuard layer 2 için)."""
    with patch("smc_engine.integrations.binance.order_client.Client"):
        with pytest.raises(RuntimeError, match="[Mm]ainnet"):
            BinanceOrderClient(api_key="a", api_secret="b", testnet=False)


def test_constructor_mainnet_guard_rejects_raises():
    """testnet=False ise MainnetGuard.is_approved(config) Åart; False dÃ¶nerse RuntimeError."""
    from smc_engine.config import SMCConfig
    cfg = SMCConfig()
    with patch("smc_engine.integrations.binance.order_client.MainnetGuard") as MockGuard, \
         patch("smc_engine.integrations.binance.order_client.Client"):
        MockGuard.is_approved.return_value = False
        with pytest.raises(RuntimeError, match="[Mm]ainnet"):
            BinanceOrderClient(api_key="a", api_secret="b", testnet=False, config=cfg)


def test_constructor_mainnet_with_guard_approved_ok():
    from smc_engine.config import SMCConfig
    cfg = SMCConfig()
    with patch("smc_engine.integrations.binance.order_client.MainnetGuard") as MockGuard, \
         patch("smc_engine.integrations.binance.order_client.Client") as MockClient:
        MockGuard.is_approved.return_value = True
        client = BinanceOrderClient(api_key="a", api_secret="b", testnet=False, config=cfg)
        assert client is not None
        args, kwargs = MockClient.call_args
        assert kwargs.get("testnet") is False


def test_rate_limit_buffer_default_and_override():
    with patch("smc_engine.integrations.binance.order_client.Client"):
        c1 = BinanceOrderClient(api_key="a", api_secret="b", testnet=True)
        assert c1.rate_limit_buffer == 0.8
        c2 = BinanceOrderClient(api_key="a", api_secret="b", testnet=True, rate_limit_buffer=0.5)
        assert c2.rate_limit_buffer == 0.5


# =========================================================
# X1.2 — Write endpoints (place / cancel / get_open_orders / get_order)
# =========================================================


def _make_client():
    """Helper: testnet client with mocked python-binance.Client."""
    patcher = patch("smc_engine.integrations.binance.order_client.Client")
    mock_cls = patcher.start()
    mock_inst = mock_cls.return_value
    c = BinanceOrderClient(api_key="a", api_secret="b", testnet=True)
    return c, mock_inst, patcher


def test_place_order_limit_buy():
    c, mock, patcher = _make_client()
    try:
        mock.futures_create_order.return_value = {
            "orderId": 12345, "symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT",
            "origQty": "0.002", "price": "78329.30", "status": "NEW",
            "executedQty": "0", "avgPrice": "0",
        }
        req = OrderRequest(
            symbol="BTCUSDT", side=OrderSide.BUY, type=OrderType.LIMIT,
            qty=0.002, price=78329.30, time_in_force=TimeInForce.GTC,
        )
        resp = c.place_order(req)
        assert resp.order_id == "12345"
        assert resp.status is OrderStatus.NEW
        assert resp.qty == 0.002
        # python-binance çağrı parametre kontrolü
        kwargs = mock.futures_create_order.call_args.kwargs
        assert kwargs["symbol"] == "BTCUSDT"
        assert kwargs["side"] == "BUY"
        assert kwargs["type"] == "LIMIT"
        assert kwargs["timeInForce"] == "GTC"
    finally:
        patcher.stop()


def test_place_order_stop_market():
    c, mock, patcher = _make_client()
    try:
        mock.futures_create_order.return_value = {
            "orderId": 99, "symbol": "BTCUSDT", "side": "SELL", "type": "STOP_MARKET",
            "origQty": "0.002", "stopPrice": "77435.50", "status": "NEW",
            "executedQty": "0", "avgPrice": "0",
        }
        req = OrderRequest(
            symbol="BTCUSDT", side=OrderSide.SELL, type=OrderType.STOP_MARKET,
            qty=0.002, stop_price=77435.50,
        )
        resp = c.place_order(req)
        assert resp.order_id == "99"
        kwargs = mock.futures_create_order.call_args.kwargs
        assert kwargs["type"] == "STOP_MARKET"
        assert kwargs["stopPrice"] == 77435.50
    finally:
        patcher.stop()


def test_cancel_order():
    c, mock, patcher = _make_client()
    try:
        mock.futures_cancel_order.return_value = {
            "orderId": 12345, "symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT",
            "origQty": "0.002", "price": "78329.30", "status": "CANCELED",
            "executedQty": "0", "avgPrice": "0",
        }
        resp = c.cancel_order("BTCUSDT", "12345")
        assert resp.status is OrderStatus.CANCELED
        mock.futures_cancel_order.assert_called_once_with(symbol="BTCUSDT", orderId=12345)
    finally:
        patcher.stop()


def test_get_open_orders_all_symbols():
    c, mock, patcher = _make_client()
    try:
        mock.futures_get_open_orders.return_value = [
            {"orderId": 1, "symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT",
             "origQty": "0.002", "price": "78329", "status": "NEW",
             "executedQty": "0", "avgPrice": "0"},
            {"orderId": 2, "symbol": "ETHUSDT", "side": "SELL", "type": "LIMIT",
             "origQty": "0.1", "price": "2900", "status": "NEW",
             "executedQty": "0", "avgPrice": "0"},
        ]
        orders = c.get_open_orders()
        assert len(orders) == 2
        assert orders[0].symbol == "BTCUSDT"
        mock.futures_get_open_orders.assert_called_once_with()
    finally:
        patcher.stop()


def test_get_open_orders_single_symbol():
    c, mock, patcher = _make_client()
    try:
        mock.futures_get_open_orders.return_value = []
        c.get_open_orders(symbol="BTCUSDT")
        mock.futures_get_open_orders.assert_called_once_with(symbol="BTCUSDT")
    finally:
        patcher.stop()


def test_get_order_status_filled():
    c, mock, patcher = _make_client()
    try:
        mock.futures_get_order.return_value = {
            "orderId": 12345, "symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT",
            "origQty": "0.002", "price": "78329", "status": "FILLED",
            "executedQty": "0.002", "avgPrice": "78327.50",
        }
        resp = c.get_order("BTCUSDT", "12345")
        assert resp.status is OrderStatus.FILLED
        assert resp.fill_qty == 0.002
        assert resp.fill_price == 78327.50
    finally:
        patcher.stop()


# =========================================================
# X1.3 — Read endpoints (get_position / get_account)
# =========================================================


def test_get_position_long():
    c, mock, patcher = _make_client()
    try:
        mock.futures_position_information.return_value = [
            {"symbol": "BTCUSDT", "positionAmt": "0.002", "entryPrice": "78327.50",
             "unRealizedProfit": "1.50", "liquidationPrice": "70850.0",
             "marginType": "isolated"},
        ]
        p = c.get_position("BTCUSDT")
        assert p.symbol == "BTCUSDT"
        assert p.qty == 0.002
        assert p.entry_price == 78327.50
        assert p.unrealized_pnl == 1.50
    finally:
        patcher.stop()


def test_get_position_none():
    c, mock, patcher = _make_client()
    try:
        mock.futures_position_information.return_value = [
            {"symbol": "BTCUSDT", "positionAmt": "0", "entryPrice": "0",
             "unRealizedProfit": "0", "liquidationPrice": "0",
             "marginType": "isolated"},
        ]
        p = c.get_position("BTCUSDT")
        assert p.qty == 0.0
    finally:
        patcher.stop()


def test_get_account():
    c, mock, patcher = _make_client()
    try:
        mock.futures_account.return_value = {
            "totalWalletBalance": "25.00",
            "availableBalance": "20.00",
            "totalInitialMargin": "5.00",
        }
        a = c.get_account()
        assert a.equity == 25.00
        assert a.available_margin == 20.00
        assert a.used_margin == 5.00
    finally:
        patcher.stop()


# =========================================================
# X1.4 — Leverage + margin mode
# =========================================================


def test_set_leverage():
    c, mock, patcher = _make_client()
    try:
        mock.futures_change_leverage.return_value = {"leverage": 10, "symbol": "BTCUSDT"}
        c.set_leverage("BTCUSDT", 10)
        mock.futures_change_leverage.assert_called_once_with(symbol="BTCUSDT", leverage=10)
    finally:
        patcher.stop()


def test_set_margin_mode_isolated():
    c, mock, patcher = _make_client()
    try:
        mock.futures_change_margin_type.return_value = {"code": 200, "msg": "success"}
        c.set_margin_mode("BTCUSDT", "isolated")
        mock.futures_change_margin_type.assert_called_once_with(
            symbol="BTCUSDT", marginType="ISOLATED",
        )
    finally:
        patcher.stop()


def test_set_margin_mode_idempotent_on_no_need_to_change():
    """Binance "No need to change margin type" hatasÄ± idempotent geÃ§sin (-4046)."""

    class FakeBinanceAPIException(Exception):
        def __init__(self, code, msg):
            self.code = code
            self.message = msg
            super().__init__(msg)

    with patch("smc_engine.integrations.binance.order_client.Client") as mock_cls, \
         patch("smc_engine.integrations.binance.order_client._BINANCE_API_EXC",
               (FakeBinanceAPIException,)):
        mock = mock_cls.return_value
        mock.futures_change_margin_type.side_effect = FakeBinanceAPIException(
            -4046, "No need to change margin type."
        )
        c = BinanceOrderClient(api_key="a", api_secret="b", testnet=True)
        # Hata fÄ±rlatmamalÄ± — zaten doÄru mode'da
        c.set_margin_mode("BTCUSDT", "isolated")


# =========================================================
# X1.5 — Error handling (Spec §12.1)
# =========================================================


class _FakeAPIError(Exception):
    """Mimics binance.exceptions.BinanceAPIException."""
    def __init__(self, code: int, msg: str):
        self.code = code
        self.message = msg
        super().__init__(f"{code} {msg}")


def test_error_mapping_price_filter_kill_switch_signal():
    with patch("smc_engine.integrations.binance.order_client.Client") as mock_cls, \
         patch("smc_engine.integrations.binance.order_client._BINANCE_API_EXC",
               (_FakeAPIError,)):
        mock = mock_cls.return_value
        mock.futures_create_order.side_effect = _FakeAPIError(-1013, "PRICE_FILTER")
        c = BinanceOrderClient(api_key="a", api_secret="b", testnet=True)
        req = OrderRequest(symbol="BTCUSDT", side=OrderSide.BUY, type=OrderType.LIMIT,
                           qty=0.002, price=78329.30)
        with pytest.raises(BinanceOrderError) as exc_info:
            c.place_order(req)
        assert exc_info.value.code == -1013
        assert exc_info.value.retryable is False
        assert exc_info.value.kill_switch_signal is True


def test_error_mapping_new_order_rejected_not_retryable():
    with patch("smc_engine.integrations.binance.order_client.Client") as mock_cls, \
         patch("smc_engine.integrations.binance.order_client._BINANCE_API_EXC",
               (_FakeAPIError,)):
        mock = mock_cls.return_value
        mock.futures_create_order.side_effect = _FakeAPIError(-2010, "REJECTED")
        c = BinanceOrderClient(api_key="a", api_secret="b", testnet=True)
        req = OrderRequest(symbol="BTCUSDT", side=OrderSide.BUY, type=OrderType.LIMIT,
                           qty=0.002, price=78329.30)
        with pytest.raises(BinanceOrderError) as exc_info:
            c.place_order(req)
        assert exc_info.value.code == -2010
        assert exc_info.value.retryable is False


def test_error_mapping_margin_insufficient_kill_switch():
    with patch("smc_engine.integrations.binance.order_client.Client") as mock_cls, \
         patch("smc_engine.integrations.binance.order_client._BINANCE_API_EXC",
               (_FakeAPIError,)):
        mock = mock_cls.return_value
        mock.futures_create_order.side_effect = _FakeAPIError(-2019, "Margin is insufficient")
        c = BinanceOrderClient(api_key="a", api_secret="b", testnet=True)
        req = OrderRequest(symbol="BTCUSDT", side=OrderSide.BUY, type=OrderType.LIMIT,
                           qty=0.002, price=78329.30)
        with pytest.raises(BinanceOrderError) as exc_info:
            c.place_order(req)
        assert exc_info.value.kill_switch_signal is True


def test_error_mapping_cancel_rejected_reconcile_needed():
    with patch("smc_engine.integrations.binance.order_client.Client") as mock_cls, \
         patch("smc_engine.integrations.binance.order_client._BINANCE_API_EXC",
               (_FakeAPIError,)):
        mock = mock_cls.return_value
        mock.futures_cancel_order.side_effect = _FakeAPIError(-2011, "CANCEL_REJECTED")
        c = BinanceOrderClient(api_key="a", api_secret="b", testnet=True)
        with pytest.raises(BinanceOrderError) as exc_info:
            c.cancel_order("BTCUSDT", "12345")
        assert exc_info.value.code == -2011
        assert exc_info.value.reconcile_needed is True


def test_error_mapping_rate_limit_429_retries_then_aborts():
    """429 → exponential backoff (3 attempt), hepsi fail → raise."""
    with patch("smc_engine.integrations.binance.order_client.Client") as mock_cls, \
         patch("smc_engine.integrations.binance.order_client._BINANCE_API_EXC",
               (_FakeAPIError,)), \
         patch("smc_engine.integrations.binance.order_client._RETRY_SLEEP", lambda s: None):
        mock = mock_cls.return_value
        # Bütün denemeler 429 dÃ¶ndÃ¼rsÃ¼n
        mock.futures_create_order.side_effect = _FakeAPIError(-1003, "Too many requests")
        c = BinanceOrderClient(api_key="a", api_secret="b", testnet=True)
        req = OrderRequest(symbol="BTCUSDT", side=OrderSide.BUY, type=OrderType.LIMIT,
                           qty=0.002, price=78329.30)
        with pytest.raises(BinanceOrderError) as exc_info:
            c.place_order(req)
        assert exc_info.value.code == -1003
        # 3 deneme yapıldı
        assert mock.futures_create_order.call_count == 3


def test_error_mapping_rate_limit_retries_then_succeeds():
    """3 denemeden 2'si 429, 3.'sÃ¼ baÅarÄ±lÄ± → response."""
    with patch("smc_engine.integrations.binance.order_client.Client") as mock_cls, \
         patch("smc_engine.integrations.binance.order_client._BINANCE_API_EXC",
               (_FakeAPIError,)), \
         patch("smc_engine.integrations.binance.order_client._RETRY_SLEEP", lambda s: None):
        mock = mock_cls.return_value
        success_response = {"orderId": 1, "symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT",
                            "origQty": "0.002", "price": "78329", "status": "NEW",
                            "executedQty": "0", "avgPrice": "0"}
        mock.futures_create_order.side_effect = [
            _FakeAPIError(-1003, "Too many"),
            _FakeAPIError(-1003, "Too many"),
            success_response,
        ]
        c = BinanceOrderClient(api_key="a", api_secret="b", testnet=True)
        req = OrderRequest(symbol="BTCUSDT", side=OrderSide.BUY, type=OrderType.LIMIT,
                           qty=0.002, price=78329.30)
        resp = c.place_order(req)
        assert resp.order_id == "1"
        assert mock.futures_create_order.call_count == 3


def test_error_mapping_percent_price_4131():
    with patch("smc_engine.integrations.binance.order_client.Client") as mock_cls, \
         patch("smc_engine.integrations.binance.order_client._BINANCE_API_EXC",
               (_FakeAPIError,)):
        mock = mock_cls.return_value
        mock.futures_create_order.side_effect = _FakeAPIError(-4131, "PERCENT_PRICE")
        c = BinanceOrderClient(api_key="a", api_secret="b", testnet=True)
        req = OrderRequest(symbol="BTCUSDT", side=OrderSide.BUY, type=OrderType.LIMIT,
                           qty=0.002, price=78329.30)
        with pytest.raises(BinanceOrderError) as exc_info:
            c.place_order(req)
        assert exc_info.value.code == -4131
        assert exc_info.value.retryable is False
