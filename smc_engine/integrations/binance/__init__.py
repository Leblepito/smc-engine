"""Binance USDT-M futures adapter (Sub-proje #2).

Sürüm v1: REST + APScheduler tetiklemesi. WS kline stream v2'ye ertelendi.
Emir gönderme YOK — log-only mod; gerçek emir akışı sub-proje #5.
"""

from smc_engine.integrations.binance.adapter import BinanceAdapter
from smc_engine.integrations.binance.client import BinanceClient

__all__ = ["BinanceAdapter", "BinanceClient"]
