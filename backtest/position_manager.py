"""Faz 5.1 — Pozisyon yonetimi.

Tek pozisyon modeli. Acma (``open_position``) ve bar-bazli guncelleme
(``update``). TP merdiveni kademeli kapanis (``tp_weights``); TP1 vurulunca
``current_sl`` -> breakeven (entry). Bar OHLC ile SL/TP kontrolu; bar-ici
SL+TP cakismasinda **SL once** (en kotu senaryo). Spread + commission +
slippage maliyetleri uygulanir.

İmza:
    open_position(validated_setup, fill_price, fill_ts, config) -> Position
    update(position, bar, config) -> tuple[Position | None, list[Trade]]

``update`` donusu: (kalan pozisyon | None, bu barda kapanan dilim(ler)).
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from smc_engine.types import Direction, Position, Trade, ValidatedSetup

# Exit reason etiketleri TP indeksine gore.
# Setup builder yalniz 3 TP uretir (Spec §6); defensive: >=3 idx `TPN` fallback'a
# duser (asagi bkz). Cogul-TP'ye gecilirse bu sozluk + setup_builder birlikte
# guncellenir.
_TP_REASON = {0: "TP1", 1: "TP2", 2: "TP3"}


# ============================================================
# Maliyet yardimcilari
# ============================================================


def _apply_entry_costs(direction: Direction, raw_price: float, config) -> float:
    """Giris fill fiyatina spread + slippage uygula (her zaman traderin aleyhine).

    LONG: fiyat yukari iter. SHORT: fiyat asagi iter.
    """
    spread = getattr(config, "spread", 0.0)
    slip = getattr(config, "slippage_pct", 0.0)
    adverse = raw_price * slip + spread / 2.0
    if direction == Direction.LONG:
        return raw_price + adverse
    return raw_price - adverse


def _apply_exit_costs(direction: Direction, raw_price: float, config) -> float:
    """Cikis fiyatina spread + slippage uygula (traderin aleyhine).

    LONG cikis (satis): fiyat asagi iter. SHORT cikis (alis): yukari iter.
    """
    spread = getattr(config, "spread", 0.0)
    slip = getattr(config, "slippage_pct", 0.0)
    adverse = raw_price * slip + spread / 2.0
    if direction == Direction.LONG:
        return raw_price - adverse
    return raw_price + adverse


def _commission(price: float, size: float, config) -> float:
    """Notional bazli komisyon (tek taraf)."""
    return abs(price * size) * getattr(config, "commission_pct", 0.0)


# ============================================================
# Pozisyon acma
# ============================================================


def open_position(
    validated_setup: ValidatedSetup,
    fill_price: float,
    fill_ts,
    config,
) -> Position:
    """``ValidatedSetup`` -> acik ``Position``.

    ``fill_price`` ham fill fiyati (harness fill modeli verir); spread +
    slippage burada uygulanir. ``position_size`` ValidatedSetup'tan gelir.
    """
    setup = validated_setup.setup
    direction = setup.direction
    entry = _apply_entry_costs(direction, fill_price, config)
    size = validated_setup.position_size
    return Position(
        direction=direction,
        entry=entry,
        entry_ts=fill_ts,
        original_sl=setup.sl,
        current_sl=setup.sl,
        tp=list(setup.tp),
        tp_weights=list(setup.tp_weights),
        total_size=size,
        remaining_size=size,
        tp_hits=[],
        validated_setup=validated_setup,
    )


# ============================================================
# Bar-bazli guncelleme
# ============================================================


def _sl_hit(direction: Direction, sl: float, bar: pd.Series) -> bool:
    if direction == Direction.LONG:
        return bar["low"] <= sl
    return bar["high"] >= sl


def _tp_hit(direction: Direction, tp: float, bar: pd.Series) -> bool:
    if direction == Direction.LONG:
        return bar["high"] >= tp
    return bar["low"] <= tp


def _r_unit(position: Position) -> float:
    """1R = giris ile orijinal SL arasi mesafe (pozitif)."""
    return abs(position.entry - position.original_sl)


def _make_trade(
    position: Position,
    exit_raw: float,
    exit_ts,
    exit_reason: str,
    size: float,
    config,
) -> Trade:
    """Bir dilim icin ``Trade`` kaydi uret — cikis maliyetleri dahil."""
    direction = position.direction
    exit_price = _apply_exit_costs(direction, exit_raw, config)
    if direction == Direction.LONG:
        gross = (exit_price - position.entry) * size
    else:
        gross = (position.entry - exit_price) * size
    # Komisyon: giris + cikis (bu dilim icin).
    comm = _commission(position.entry, size, config) + _commission(
        exit_price, size, config
    )
    pnl = gross - comm
    r_unit = _r_unit(position)
    # R-multiple: dilimin pnl'i / (1R * dilim size). Sifir bolme korumasi.
    denom = r_unit * size
    r_multiple = pnl / denom if denom > 0 else 0.0
    setup = position.validated_setup.setup
    return Trade(
        direction=direction,
        entry=position.entry,
        entry_ts=position.entry_ts,
        exit_price=exit_price,
        exit_ts=exit_ts,
        exit_reason=exit_reason,
        pnl=pnl,
        r_multiple=r_multiple,
        size=size,
        confluence_score=setup.confluence_score,
        confluence_factor_count=setup.confluence_factor_count,
    )


def update(
    position: Position,
    bar: pd.Series,
    config,
) -> tuple[Optional[Position], list[Trade]]:
    """Pozisyonu BU barin OHLC'siyle guncelle.

    Donus: ``(kalan_pozisyon | None, bu_barda_kapanan_dilimler)``.

    Kurallar:
      - bar-ici SL + TP cakismasi -> **SL once** (en kotu senaryo): tum kalan
        pozisyon SL'de kapanir.
      - SL yoksa, vurulan her TP icin dilim kapanir (``tp_weights``).
      - TP1 (indeks 0) ilk kez vurulunca ``current_sl`` -> entry (breakeven).
      - Tum dilimler kapaninca pozisyon None doner.
    """
    bar_ts = bar.name
    direction = position.direction
    trades: list[Trade] = []

    # --- 1. SL kontrolu (breakeven dahil current_sl) — ONCE ---
    if _sl_hit(direction, position.current_sl, bar):
        # Breakeven SL mi yoksa orijinal SL mi?
        is_breakeven = (
            len(position.tp_hits) > 0
            and abs(position.current_sl - position.entry) < 1e-12
        )
        reason = "BREAKEVEN" if is_breakeven else "SL"
        trade = _make_trade(
            position, position.current_sl, bar_ts, reason,
            position.remaining_size, config,
        )
        trades.append(trade)
        return None, trades

    # --- 2. TP kontrolu — SL vurulmadi ---
    for idx, tp_price in enumerate(position.tp):
        if idx in position.tp_hits:
            continue
        if not _tp_hit(direction, tp_price, bar):
            continue
        # Bu TP dilimini kapat.
        slice_size = position.total_size * position.tp_weights[idx]
        # Son aktif dilimde kalan rounding artigini da kapat.
        # (sirayla ilerledigimiz icin: eger bu son hit-edilebilir TP ise
        #  remaining_size'i tuket.)
        is_last = idx == len(position.tp) - 1
        if is_last:
            slice_size = position.remaining_size
        slice_size = min(slice_size, position.remaining_size)
        reason = _TP_REASON.get(idx, f"TP{idx + 1}")
        trade = _make_trade(
            position, tp_price, bar_ts, reason, slice_size, config
        )
        trades.append(trade)
        position.remaining_size -= slice_size
        position.tp_hits.append(idx)
        # TP1 -> breakeven.
        if idx == 0:
            position.current_sl = position.entry

    # --- 3. Pozisyon tamamen kapandi mi? ---
    if position.remaining_size <= 1e-12 or len(position.tp_hits) == len(
        position.tp
    ):
        return None, trades

    return position, trades
