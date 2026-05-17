"""BinanceAdapter testleri — mock BinanceClient ile birim testleri (Spec §3, §5)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from smc_engine.integrations._base import ExchangeAdapter, SymbolMeta
from smc_engine.integrations.binance.adapter import BinanceAdapter
from smc_engine.types import TimeFrame


# ---------------- yardımcılar ----------------


def _make_kline_row(open_time_ms: int, o: float, h: float, l: float, c: float, v: float):
    """python-binance futures_klines bir satır → 12 elemanlı liste (Binance format)."""
    close_time_ms = open_time_ms + 15 * 60 * 1000 - 1
    return [
        open_time_ms,    # 0: open time (ms)
        str(o),          # 1: open
        str(h),          # 2: high
        str(l),          # 3: low
        str(c),          # 4: close
        str(v),          # 5: volume
        close_time_ms,   # 6: close time (ms)
        "0",             # 7: quote volume
        0,               # 8: number of trades
        "0",             # 9: taker buy base
        "0",             # 10: taker buy quote
        "0",             # 11: ignore
    ]


# ---------------- fetch_ohlcv ----------------


def test_fetch_ohlcv_returns_dataframe_with_datetime_index():
    mock_client = MagicMock()
    base_ms = int(datetime(2026, 5, 16, 14, 0, tzinfo=timezone.utc).timestamp() * 1000)
    rows = [
        _make_kline_row(base_ms, 100, 110, 95, 105, 50),
        _make_kline_row(base_ms + 15 * 60 * 1000, 105, 115, 100, 110, 60),
        _make_kline_row(base_ms + 30 * 60 * 1000, 110, 120, 105, 115, 70),
    ]
    mock_client.futures_klines.return_value = rows
    adapter = BinanceAdapter(client=mock_client)

    df = adapter.fetch_ohlcv("BTCUSDT", TimeFrame.M15, lookback_bars=3)

    assert isinstance(df, pd.DataFrame)
    assert isinstance(df.index, pd.DatetimeIndex)
    for col in ("open", "high", "low", "close", "volume"):
        assert col in df.columns
    assert len(df) == 3
    assert df["close"].iloc[0] == 105.0
    assert df["volume"].iloc[2] == 70.0


def test_fetch_ohlcv_uses_correct_interval_for_tf():
    mock_client = MagicMock()
    mock_client.futures_klines.return_value = []
    adapter = BinanceAdapter(client=mock_client)

    adapter.fetch_ohlcv("BTCUSDT", TimeFrame.M15, lookback_bars=100)
    args, kwargs = mock_client.futures_klines.call_args
    assert kwargs["interval"] == "15m"
    assert kwargs["limit"] == 100

    adapter.fetch_ohlcv("BTCUSDT", TimeFrame.H4, lookback_bars=50)
    _, kwargs = mock_client.futures_klines.call_args
    assert kwargs["interval"] == "4h"

    adapter.fetch_ohlcv("BTCUSDT", TimeFrame.D1, lookback_bars=10)
    _, kwargs = mock_client.futures_klines.call_args
    assert kwargs["interval"] == "1d"


def test_fetch_ohlcv_excludes_forming_bar():
    """Spec §3 look-ahead garantisi: forming bar (close_time gelecekteyse) skip."""
    mock_client = MagicMock()
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    # 1 kapanmış bar (close geçmiş) + 1 forming (close gelecek)
    closed_open_ms = now_ms - 60 * 60 * 1000  # 1 saat önce
    forming_open_ms = now_ms - 5 * 60 * 1000  # 5 dk önce -> close 10 dk sonra (gelecek)
    rows = [
        _make_kline_row(closed_open_ms, 100, 110, 95, 105, 50),
        _make_kline_row(forming_open_ms, 105, 115, 100, 110, 60),
    ]
    mock_client.futures_klines.return_value = rows
    adapter = BinanceAdapter(client=mock_client)
    df = adapter.fetch_ohlcv("BTCUSDT", TimeFrame.M15, lookback_bars=2)
    assert len(df) == 1  # forming bar atıldı
    assert df["close"].iloc[0] == 105.0


# ---------------- funding rate / open interest ----------------


def test_fetch_funding_rate_returns_float():
    mock_client = MagicMock()
    mock_client.futures_funding_rate.return_value = [
        {"symbol": "BTCUSDT", "fundingRate": "0.0001", "fundingTime": 1}
    ]
    adapter = BinanceAdapter(client=mock_client)
    rate = adapter.fetch_funding_rate("BTCUSDT")
    assert isinstance(rate, float)
    assert rate == pytest.approx(0.0001)


def test_fetch_funding_rate_empty_returns_zero():
    mock_client = MagicMock()
    mock_client.futures_funding_rate.return_value = []
    adapter = BinanceAdapter(client=mock_client)
    assert adapter.fetch_funding_rate("BTCUSDT") == 0.0


def test_fetch_open_interest_returns_float():
    mock_client = MagicMock()
    mock_client.futures_open_interest.return_value = {
        "symbol": "BTCUSDT", "openInterest": "12345.6"
    }
    adapter = BinanceAdapter(client=mock_client)
    oi = adapter.fetch_open_interest("BTCUSDT")
    assert isinstance(oi, float)
    assert oi == pytest.approx(12345.6)


# ---------------- symbol info ----------------


def test_fetch_symbol_info_returns_symbol_meta():
    mock_client = MagicMock()
    mock_client.futures_exchange_info.return_value = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "pricePrecision": 1,
                "quantityPrecision": 3,
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                ],
            },
            {"symbol": "OTHER", "pricePrecision": 4, "quantityPrecision": 2, "filters": []},
        ]
    }
    adapter = BinanceAdapter(client=mock_client)
    meta = adapter.fetch_symbol_info("BTCUSDT")
    assert isinstance(meta, SymbolMeta)
    assert meta.symbol == "BTCUSDT"
    assert meta.tick_size == pytest.approx(0.10)
    assert meta.lot_size == pytest.approx(0.001)
    assert meta.min_qty == pytest.approx(0.001)
    assert meta.price_precision == 1
    assert meta.qty_precision == 3


def test_fetch_symbol_info_caches_exchange_info():
    """exchange_info iki sembol için iki kez çağrılmamalı (cache)."""
    mock_client = MagicMock()
    mock_client.futures_exchange_info.return_value = {
        "symbols": [
            {
                "symbol": "BTCUSDT", "pricePrecision": 1, "quantityPrecision": 3,
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                ],
            },
            {
                "symbol": "ETHUSDT", "pricePrecision": 2, "quantityPrecision": 3,
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                ],
            },
        ]
    }
    adapter = BinanceAdapter(client=mock_client)
    adapter.fetch_symbol_info("BTCUSDT")
    adapter.fetch_symbol_info("ETHUSDT")
    adapter.fetch_symbol_info("BTCUSDT")
    # Cache hits: futures_exchange_info yalnızca 1 kez çağrılmış olmalı
    assert mock_client.futures_exchange_info.call_count == 1


def test_fetch_symbol_info_unknown_raises():
    mock_client = MagicMock()
    mock_client.futures_exchange_info.return_value = {"symbols": []}
    adapter = BinanceAdapter(client=mock_client)
    with pytest.raises(ValueError):
        adapter.fetch_symbol_info("UNKNOWN")


# ---------------- Protocol uyumu ----------------


def test_binance_adapter_satisfies_exchange_adapter_protocol():
    mock_client = MagicMock()
    adapter = BinanceAdapter(client=mock_client)
    assert isinstance(adapter, ExchangeAdapter)


def test_adapter_close_delegates_to_client():
    mock_client = MagicMock()
    adapter = BinanceAdapter(client=mock_client)
    adapter.close()
    mock_client.close.assert_called_once()
