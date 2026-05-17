"""BinanceClient testleri — python-binance wrap, rate-limit/retry/auth (Spec §7, §10).

Tüm REST çağrıları mock'lanır; gerçek API çağrısı YOK.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from smc_engine.integrations.binance.client import BinanceClient


# ---------------- init & auth ----------------


def test_client_reads_keys_from_env(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "ak")
    monkeypatch.setenv("BINANCE_API_SECRET", "sk")
    with patch("smc_engine.integrations.binance.client.Client") as MockClient:
        c = BinanceClient()
        MockClient.assert_called_once()
        args, kwargs = MockClient.call_args
        # python-binance Client(api_key, api_secret, testnet=...)
        assert "ak" in args or kwargs.get("api_key") == "ak"
        assert "sk" in args or kwargs.get("api_secret") == "sk"
        assert c is not None


def test_client_supports_explicit_keys():
    with patch("smc_engine.integrations.binance.client.Client") as MockClient:
        BinanceClient(api_key="xx", api_secret="yy")
        MockClient.assert_called_once()


def test_client_no_keys_runs_in_public_mode(monkeypatch):
    """Keys yoksa public-only mod — Client yine kurulur (public endpoint için yeterli)."""
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    with patch("smc_engine.integrations.binance.client.Client") as MockClient:
        c = BinanceClient()
        assert c is not None
        MockClient.assert_called_once()


def test_client_testnet_flag_passed_through(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "ak")
    monkeypatch.setenv("BINANCE_API_SECRET", "sk")
    with patch("smc_engine.integrations.binance.client.Client") as MockClient:
        BinanceClient(testnet=True)
        args, kwargs = MockClient.call_args
        assert kwargs.get("testnet") is True


# ---------------- futures endpoint sarmalayıcıları ----------------


def test_futures_klines_delegates_to_python_binance():
    with patch("smc_engine.integrations.binance.client.Client") as MockClient:
        mock_client_inst = MockClient.return_value
        mock_client_inst.futures_klines.return_value = [["row"]]
        c = BinanceClient(api_key="a", api_secret="b")
        out = c.futures_klines(symbol="BTCUSDT", interval="15m", limit=100)
        mock_client_inst.futures_klines.assert_called_once_with(
            symbol="BTCUSDT", interval="15m", limit=100
        )
        assert out == [["row"]]


def test_futures_funding_rate_delegates():
    with patch("smc_engine.integrations.binance.client.Client") as MockClient:
        mock_client_inst = MockClient.return_value
        mock_client_inst.futures_funding_rate.return_value = [
            {"symbol": "BTCUSDT", "fundingRate": "0.0001", "fundingTime": 1}
        ]
        c = BinanceClient(api_key="a", api_secret="b")
        out = c.futures_funding_rate(symbol="BTCUSDT", limit=1)
        mock_client_inst.futures_funding_rate.assert_called_once_with(
            symbol="BTCUSDT", limit=1
        )
        assert out[0]["symbol"] == "BTCUSDT"


def test_futures_open_interest_delegates():
    with patch("smc_engine.integrations.binance.client.Client") as MockClient:
        mock_client_inst = MockClient.return_value
        mock_client_inst.futures_open_interest.return_value = {
            "symbol": "BTCUSDT", "openInterest": "12345.6"
        }
        c = BinanceClient(api_key="a", api_secret="b")
        out = c.futures_open_interest(symbol="BTCUSDT")
        mock_client_inst.futures_open_interest.assert_called_once_with(symbol="BTCUSDT")
        assert out["openInterest"] == "12345.6"


def test_futures_exchange_info_delegates():
    with patch("smc_engine.integrations.binance.client.Client") as MockClient:
        mock_client_inst = MockClient.return_value
        mock_client_inst.futures_exchange_info.return_value = {"symbols": []}
        c = BinanceClient(api_key="a", api_secret="b")
        out = c.futures_exchange_info()
        mock_client_inst.futures_exchange_info.assert_called_once_with()
        assert out == {"symbols": []}


# ---------------- retry / error path ----------------


def test_retry_on_transient_5xx_then_succeeds():
    """5xx hatasında 3 deneme; 2. veya 3. deneme başarılıysa sonuç döner."""

    class FakeServerError(Exception):
        pass

    with patch("smc_engine.integrations.binance.client.Client") as MockClient, \
         patch("smc_engine.integrations.binance.client._RETRY_EXC", (FakeServerError,)), \
         patch("smc_engine.integrations.binance.client._RETRY_SLEEP", lambda s: None):
        mock_client_inst = MockClient.return_value
        mock_client_inst.futures_klines.side_effect = [
            FakeServerError("503"),
            FakeServerError("503"),
            [["row"]],
        ]
        c = BinanceClient(api_key="a", api_secret="b")
        out = c.futures_klines(symbol="BTCUSDT", interval="15m", limit=10)
        assert out == [["row"]]
        assert mock_client_inst.futures_klines.call_count == 3


def test_retry_exhausted_raises_last_exception():
    class FakeServerError(Exception):
        pass

    with patch("smc_engine.integrations.binance.client.Client") as MockClient, \
         patch("smc_engine.integrations.binance.client._RETRY_EXC", (FakeServerError,)), \
         patch("smc_engine.integrations.binance.client._RETRY_SLEEP", lambda s: None):
        mock_client_inst = MockClient.return_value
        mock_client_inst.futures_klines.side_effect = FakeServerError("503")
        c = BinanceClient(api_key="a", api_secret="b")
        with pytest.raises(FakeServerError):
            c.futures_klines(symbol="BTCUSDT", interval="15m", limit=10)
        # 3 deneme (initial + 2 retry)
        assert mock_client_inst.futures_klines.call_count == 3


def test_non_retryable_exception_passes_through():
    """4xx (parametre hatası) gibi retry edilmemesi gereken hatalar hemen yükselir."""

    class FakeBadRequest(Exception):
        pass

    with patch("smc_engine.integrations.binance.client.Client") as MockClient:
        mock_client_inst = MockClient.return_value
        mock_client_inst.futures_klines.side_effect = FakeBadRequest("bad symbol")
        c = BinanceClient(api_key="a", api_secret="b")
        with pytest.raises(FakeBadRequest):
            c.futures_klines(symbol="BAD", interval="15m", limit=10)
        # Tek deneme — retry yok
        assert mock_client_inst.futures_klines.call_count == 1


# ---------------- rate-limit tampon ----------------


def test_rate_limit_buffer_default_is_used():
    """``rate_limit_buffer`` parametresi default 0.8 (Spec §6)."""
    with patch("smc_engine.integrations.binance.client.Client"):
        c = BinanceClient(api_key="a", api_secret="b")
        assert c.rate_limit_buffer == 0.8


def test_rate_limit_buffer_override():
    with patch("smc_engine.integrations.binance.client.Client"):
        c = BinanceClient(api_key="a", api_secret="b", rate_limit_buffer=0.5)
        assert c.rate_limit_buffer == 0.5
