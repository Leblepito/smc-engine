"""Execution adapter sözleşmesi — Spec §4.1.

``ExecutionAdapter`` Protocol: bir borsa write API'sini somut bir sınıf bu
Protocol'ü implement eder (BinanceOrderClient, MT5OrderClient, ...).
``OrderManager`` yalnız bu Protocol'a karÅı kodlanır → adapter değiÅimi tek satır.

Tüm enum/dataclass'lar saf-Python (ne pydantic ne attrs); frozen=False çünkü
fill geldiğinde ``OrderResponse.fill_qty / fill_price`` güncellenebilir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Protocol, runtime_checkable


# ============================================================
# Enums
# ============================================================


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    STOP_MARKET = "STOP_MARKET"
    STOP_LIMIT = "STOP_LIMIT"


class OrderStatus(Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class TimeInForce(Enum):
    GTC = "GTC"  # Good Till Cancel
    IOC = "IOC"  # Immediate Or Cancel
    FOK = "FOK"  # Fill Or Kill
    # Bug E-A (2026-05-19): Good Till Crossing — Binance Futures post-only.
    # Order mark price'ın "yanlış" tarafındaysa Binance place ETMEZ (taker
    # olmasını engeller). Spot'taki LIMIT_MAKER muadili. Pre-place guard'ı
    # geçen ama hala yanlış tarafta olan setup'lar için defense-in-depth.
    GTX = "GTX"


# ============================================================
# Dataclasses
# ============================================================


@dataclass
class OrderRequest:
    """OrderManager → adapter.place_order parametre paketi."""

    symbol: str
    side: OrderSide
    type: OrderType
    qty: float
    price: Optional[float] = None  # LIMIT için; MARKET için None
    stop_price: Optional[float] = None  # STOP_MARKET için
    time_in_force: TimeInForce = TimeInForce.GTC
    # Hedge mode için "LONG" / "SHORT". One-way mode'da None (Binance default BOTH).
    # Bug A 2026-05-18: testnet hesabı dualSidePosition=True → her order'da gerekli.
    position_side: Optional[str] = None


@dataclass
class OrderResponse:
    """adapter.place_order / get_order çıktısı."""

    order_id: str
    symbol: str
    side: OrderSide
    type: OrderType
    qty: float
    price: Optional[float]
    status: OrderStatus
    fill_qty: float = 0.0
    fill_price: float = 0.0
    created_at: Optional[datetime] = None


@dataclass
class Position:
    """get_position çıktısı (futures USDT-M)."""

    symbol: str
    qty: float  # signed: + LONG, - SHORT, 0 = no position
    entry_price: float
    unrealized_pnl: float
    liquidation_price: float
    margin_type: str = "isolated"


@dataclass
class Account:
    """get_account çıktısı — minimal slice (R-sizing için yeterli)."""

    equity: float
    available_margin: float
    used_margin: float


# ============================================================
# Protocol
# ============================================================


@runtime_checkable
class ExecutionAdapter(Protocol):
    """Borsa execution adapter sözleÅmesi (Spec §4.1)."""

    def place_order(self, request: OrderRequest) -> OrderResponse: ...
    def cancel_order(self, symbol: str, order_id: str) -> OrderResponse: ...
    def get_open_orders(self, symbol: Optional[str] = None) -> list[OrderResponse]: ...
    def get_order(self, symbol: str, order_id: str) -> OrderResponse: ...
    def get_position(self, symbol: str) -> Position: ...
    def get_account(self) -> Account: ...
    # Bug E-B (2026-05-19): pre-place mark price guard için. I-1 code review.
    # Alt-adapter implementasyonları (paper/backtest) bu metodu uygulamalı;
    # eksikse OrderManager runtime'da AttributeError fırlatır.
    def get_mark_price(self, symbol: str) -> float: ...
