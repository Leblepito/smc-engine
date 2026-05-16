"""Imbalance detektoru — FVG / Liquidity Void / Inefficiency — Spec §5.

**FVG (Fair Value Gap)**: 3-mum dizisinde 1. ve 3. mumun *fitilleri arasinda*
2. mumun (impulse) hizla gecip kapatmadigi bir bosluk.
- Bullish FVG (LONG): ``candle[i].high < candle[i+2].low`` — bosluk
  ``(candle[i].high, candle[i+2].low)``.
- Bearish FVG (SHORT): ``candle[i].low > candle[i+2].high`` — bosluk
  ``(candle[i+2].high, candle[i].low)``.

**Liquidity Void**: cok genis FVG (>= ``liq_void_gap_atr`` x ATR) — ayni
3-mum kuralina uyar ama daha buyuk; ``kind = LIQ_VOID``.

Min boslugu filtresi: bosluk genisligi ``config.fvg_min_gap_atr`` x ATR'den
kucukse gurultu kabul edilir, elenir. ATR dahili hesaplanir (Wilder true range
ortalamasi, varsayilan 14 pencere; veri yetersizse mevcut tum barlar).

``filled = False`` ve ``fill_ratio = 0.0`` olusum aninda (orchestrator ileride
guncel fiyatla doldurma orani hesaplar). ``created_at`` = 3-mum dizisinin orta
mumunun (impulse) timestamp'i — dizi o bar kapaninca teyit edilir.

Saf fonksiyon: ``detect(ohlcv, config, **kwargs) -> list[Imbalance]``.
"""

from __future__ import annotations

import pandas as pd

from smc_engine.detectors._atr import ATR_DEFAULT_PERIOD
from smc_engine.detectors._atr import atr as _atr
from smc_engine.types import Direction, Imbalance, ImbalanceKind, TimeFrame

_ATR_WINDOW = ATR_DEFAULT_PERIOD
# Liquidity void esigi: FVG'nin "cok genis" sayilmasi icin ATR carpani.
_LIQ_VOID_GAP_ATR = 2.0
# Ö-7: INEFFICIENCY esigi — LIQ_VOID'tan da buyuk, "asiri" bosluk.
# Spec §5 listede INEFFICIENCY tipi var ama eski kod uretmiyordu; yeni kural:
# gap >= _INEFFICIENCY_GAP_ATR x ATR (siralama: INEFFICIENCY > LIQ_VOID > FVG > min).
_INEFFICIENCY_GAP_ATR = 5.0


def _classify(gap: float, liq_void_gap: float, inefficiency_gap: float):
    """Bosluk genisligine gore ImbalanceKind kararı (Ö-7).

    Esikler (cogalarak): FVG (default) -> LIQ_VOID (>= 2x ATR) ->
    INEFFICIENCY (>= 5x ATR, "asiri" bosluk).
    """
    if inefficiency_gap > 0 and gap >= inefficiency_gap:
        return ImbalanceKind.INEFFICIENCY
    if liq_void_gap > 0 and gap >= liq_void_gap:
        return ImbalanceKind.LIQ_VOID
    return ImbalanceKind.FVG


def detect(ohlcv: pd.DataFrame, config, **kwargs) -> list[Imbalance]:
    """3-mum FVG / liquidity void imbalance'lari tespit et.

    Algoritma (kronolojik tarama, her uclu i, i+1, i+2):
      1. Bullish FVG: ``high[i] < low[i+2]`` -> bosluk (high[i], low[i+2]).
      2. Bearish FVG: ``low[i] > high[i+2]`` -> bosluk (high[i+2], low[i]).
      3. Bosluk genisligi < ``fvg_min_gap_atr`` x ATR -> elenir (gurultu).
      4. Bosluk genisligi >= ``_LIQ_VOID_GAP_ATR`` x ATR -> kind=LIQ_VOID,
         aksi halde kind=FVG.
      5. ``filled=False``, ``fill_ratio=0.0``, ``created_at`` = orta mum ts.

    Args:
        ohlcv: ``open/high/low/close`` kolonlu, ``DatetimeIndex``'li df.
        config: ``fvg_min_gap_atr`` ozelligi olan config nesnesi.
        **kwargs: orchestrator opsiyonel context (Faz 1B'de kullanilmaz).

    Returns:
        Timestamp'e gore artan sirali ``Imbalance`` listesi.
    """
    n = len(ohlcv)
    if n < 3:
        return []

    min_gap_atr = getattr(config, "fvg_min_gap_atr", 0.3)
    # U-2: esikler artik config'den okunur (eskiden modul sabitiydi).
    liq_void_atr_mult = getattr(config, "liq_void_gap_atr", _LIQ_VOID_GAP_ATR)
    ineff_atr_mult = getattr(config, "inefficiency_gap_atr", _INEFFICIENCY_GAP_ATR)
    tf = getattr(config, "timeframe", None) or TimeFrame.D1

    highs = ohlcv["high"].to_numpy()
    lows = ohlcv["low"].to_numpy()
    index = ohlcv.index

    atr = _atr(ohlcv)
    # ATR 0 ise (duz veri) min esik 0 olur -> sifirdan buyuk her bosluk gecer.
    min_gap = min_gap_atr * atr
    liq_void_gap = liq_void_atr_mult * atr
    inefficiency_gap = ineff_atr_mult * atr

    imbalances: list[Imbalance] = []

    for i in range(n - 2):
        mid_ts = index[i + 1].to_pydatetime()

        # --- Bullish FVG ---
        if highs[i] < lows[i + 2]:
            bottom = float(highs[i])
            top = float(lows[i + 2])
            gap = top - bottom
            if gap > min_gap:
                kind = _classify(gap, liq_void_gap, inefficiency_gap)
                imbalances.append(
                    Imbalance(
                        kind=kind,
                        top=top,
                        bottom=bottom,
                        direction=Direction.LONG,
                        timeframe=tf,
                        created_at=mid_ts,
                        filled=False,
                        fill_ratio=0.0,
                    )
                )

        # --- Bearish FVG ---
        if lows[i] > highs[i + 2]:
            top = float(lows[i])
            bottom = float(highs[i + 2])
            gap = top - bottom
            if gap > min_gap:
                kind = _classify(gap, liq_void_gap, inefficiency_gap)
                imbalances.append(
                    Imbalance(
                        kind=kind,
                        top=top,
                        bottom=bottom,
                        direction=Direction.SHORT,
                        timeframe=tf,
                        created_at=mid_ts,
                        filled=False,
                        fill_ratio=0.0,
                    )
                )

    imbalances.sort(key=lambda x: x.created_at)
    return imbalances
