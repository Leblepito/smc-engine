"""Borsa adapter sözleşmesi — Spec §3 (Sub-proje #2).

``ExchangeAdapter`` Protocol: bir borsayı somut bir sınıf bu Protocol'ü
implement eder (Binance, MT5, Oanda...). ``LiveRunner`` yalnız bu Protocol'a
karşı kodlanır → adapter değişimi tek satır.

``SymbolMeta`` ve ``Kline`` adapter'ların ürettiği iki ortak veri tipi
(``smc_engine.types`` üzerinden de re-export edilir).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd

from smc_engine.types import Kline, SymbolMeta, TimeFrame

__all__ = ["ExchangeAdapter", "Kline", "SymbolMeta"]


@runtime_checkable
class ExchangeAdapter(Protocol):
    """Borsa adapter sözleşmesi (Spec §3).

    Tüm metodlar senkron; live runner thread-safe değil (APScheduler tek
    iş parçacığı). WS subscribe v2'ye ertelendi (Spec §2 "Dahil değil");
    Protocol'da yer almıyor.
    """

    def fetch_ohlcv(
        self, symbol: str, timeframe: TimeFrame, lookback_bars: int
    ) -> pd.DataFrame:
        """DatetimeIndex'li OHLCV DataFrame. Forming bar DAHİL EDİLMEZ."""
        ...

    def fetch_funding_rate(self, symbol: str) -> float:
        """En güncel funding rate (futures)."""
        ...

    def fetch_open_interest(self, symbol: str) -> float:
        """En güncel open interest (futures)."""
        ...

    def fetch_symbol_info(self, symbol: str) -> SymbolMeta:
        """Tick/lot/precision metadata; cache'lenebilir."""
        ...

    def close(self) -> None:
        """Bağlantıları kapat (HTTP session, vs.)."""
        ...
