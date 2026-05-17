"""``BinanceAdapter`` — ``ExchangeAdapter`` Protocol'ünün Binance USDT-M somut implementasyonu.

Spec §3: orchestrator/runner ile ``BinanceClient`` arasında orta katman.
``BinanceClient`` ham python-binance çağrılarını yapar; bu adapter çıktıları
SMC engine tiplerine (DataFrame, ``SymbolMeta``, float) çevirir.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from smc_engine.integrations.binance.client import BinanceClient
from smc_engine.integrations.binance.data import (
    funding_payload_to_float,
    klines_to_dataframe,
    open_interest_payload_to_float,
    tf_to_binance_interval,
)
from smc_engine.integrations.binance.symbols import extract_symbol_meta, normalize_symbol
from smc_engine.types import SymbolMeta, TimeFrame


class BinanceAdapter:
    """``ExchangeAdapter`` Protocol implementasyonu (futures USDT-M)."""

    def __init__(self, client: Optional[BinanceClient] = None) -> None:
        self._client = client if client is not None else BinanceClient()
        self._exchange_info_cache: Optional[dict] = None
        self._symbol_meta_cache: dict[str, SymbolMeta] = {}

    # ---------------- OHLCV ----------------

    def fetch_ohlcv(
        self, symbol: str, timeframe: TimeFrame, lookback_bars: int
    ) -> pd.DataFrame:
        sym = normalize_symbol(symbol)
        interval = tf_to_binance_interval(timeframe)
        raw = self._client.futures_klines(symbol=sym, interval=interval, limit=lookback_bars)
        return klines_to_dataframe(raw, include_forming=False)

    # ---------------- Funding rate / OI ----------------

    def fetch_funding_rate(self, symbol: str) -> float:
        sym = normalize_symbol(symbol)
        payload = self._client.futures_funding_rate(symbol=sym, limit=1)
        return funding_payload_to_float(payload)

    def fetch_open_interest(self, symbol: str) -> float:
        sym = normalize_symbol(symbol)
        payload = self._client.futures_open_interest(symbol=sym)
        return open_interest_payload_to_float(payload)

    # ---------------- Symbol metadata ----------------

    def fetch_symbol_info(self, symbol: str) -> SymbolMeta:
        sym = normalize_symbol(symbol)
        if sym in self._symbol_meta_cache:
            return self._symbol_meta_cache[sym]
        if self._exchange_info_cache is None:
            self._exchange_info_cache = self._client.futures_exchange_info()
        meta = extract_symbol_meta(self._exchange_info_cache, sym)
        if meta is None:
            raise ValueError(f"Sembol bulunamadı: {sym}")
        self._symbol_meta_cache[sym] = meta
        return meta

    # ---------------- lifecycle ----------------

    def close(self) -> None:
        self._client.close()
