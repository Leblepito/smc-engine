"""Binance sembol normalize + metadata extraction (Spec §5).

``futures_exchange_info`` raw payload'undan ``SymbolMeta`` üretir.
"""

from __future__ import annotations

from typing import Optional

from smc_engine.types import SymbolMeta


def normalize_symbol(symbol: str) -> str:
    """USDT-M perpetual sembol normalize (örn 'btc-usdt' → 'BTCUSDT')."""
    return symbol.replace("-", "").replace("/", "").upper()


def extract_symbol_meta(exchange_info: dict, symbol: str) -> Optional[SymbolMeta]:
    """``futures_exchange_info`` dict'inden tek sembolün ``SymbolMeta``'sını çıkar.

    Bulunamazsa ``None``.
    """
    sym = normalize_symbol(symbol)
    for entry in exchange_info.get("symbols", []):
        if entry.get("symbol", "").upper() != sym:
            continue
        tick_size = 0.0
        lot_size = 0.0
        min_qty = 0.0
        min_notional = 0.0
        for flt in entry.get("filters", []):
            ftype = flt.get("filterType")
            if ftype == "PRICE_FILTER":
                tick_size = float(flt.get("tickSize", "0"))
            elif ftype == "LOT_SIZE":
                lot_size = float(flt.get("stepSize", "0"))
                min_qty = float(flt.get("minQty", "0"))
            elif ftype in ("MIN_NOTIONAL", "NOTIONAL"):
                # Binance USDT-M: çoğunlukla "notional" key; eski API'lerde
                # "minNotional". İkisini de dene; ilki bulunan kullanılır.
                raw = flt.get("notional", flt.get("minNotional", "0"))
                min_notional = float(raw)
        return SymbolMeta(
            symbol=sym,
            tick_size=tick_size,
            lot_size=lot_size,
            min_qty=min_qty,
            price_precision=int(entry.get("pricePrecision", 0)),
            qty_precision=int(entry.get("quantityPrecision", 0)),
            min_notional=min_notional,
        )
    return None
