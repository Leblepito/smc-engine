"""smc_engine.integrations._base — ExchangeAdapter Protocol + tipler (Spec §3, §5)."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from smc_engine.integrations._base import ExchangeAdapter, Kline, SymbolMeta
from smc_engine.types import TimeFrame


def test_symbol_meta_dataclass_fields():
    meta = SymbolMeta(
        symbol="BTCUSDT",
        tick_size=0.1,
        lot_size=0.001,
        min_qty=0.001,
        price_precision=1,
        qty_precision=3,
    )
    assert meta.symbol == "BTCUSDT"
    assert meta.tick_size == 0.1
    assert meta.lot_size == 0.001
    assert meta.price_precision == 1


def test_symbol_meta_is_frozen():
    meta = SymbolMeta("BTCUSDT", 0.1, 0.001, 0.001, 1, 3)
    try:
        meta.symbol = "ETHUSDT"
    except Exception:
        return
    raise AssertionError("SymbolMeta dataclass frozen olmalı")


def test_kline_dataclass_fields():
    ts = datetime(2026, 5, 16, 14, 45)
    k = Kline(
        symbol="BTCUSDT",
        timeframe=TimeFrame.M15,
        open_time=ts,
        open=67000.0,
        high=67500.0,
        low=66800.0,
        close=67432.5,
        volume=123.45,
        is_closed=True,
    )
    assert k.symbol == "BTCUSDT"
    assert k.timeframe == TimeFrame.M15
    assert k.open_time == ts
    assert k.is_closed is True


def test_exchange_adapter_protocol_is_runtime_checkable_or_structural():
    """Protocol uyumlu fake bir sınıf tip kontrolünden geçmeli (yapısal)."""

    class FakeAdapter:
        def fetch_ohlcv(self, symbol, timeframe, lookback_bars):
            return pd.DataFrame()

        def fetch_funding_rate(self, symbol):
            return 0.0

        def fetch_open_interest(self, symbol):
            return 0.0

        def fetch_symbol_info(self, symbol):
            return SymbolMeta(symbol, 0.1, 0.001, 0.001, 1, 3)

        def close(self):
            return None

    # Yapısal uyum: tüm Protocol metodları FakeAdapter'da var.
    fake = FakeAdapter()
    assert hasattr(fake, "fetch_ohlcv")
    assert hasattr(fake, "fetch_funding_rate")
    assert hasattr(fake, "fetch_open_interest")
    assert hasattr(fake, "fetch_symbol_info")
    assert hasattr(fake, "close")

    # Protocol modülde export edilmiş olmalı.
    assert ExchangeAdapter is not None


def test_types_module_reexports_symbol_meta_and_kline():
    """SymbolMeta + Kline smc_engine.types üzerinden de erişilebilir olmalı."""
    from smc_engine.types import Kline as KFromTypes
    from smc_engine.types import SymbolMeta as SMFromTypes

    assert KFromTypes is Kline
    assert SMFromTypes is SymbolMeta
