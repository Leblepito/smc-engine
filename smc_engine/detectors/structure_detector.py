"""Market structure detektoru — CHoCH / BOS — Spec §5 (detektor tablosu).

- **BOS** (Break of Structure): trend yonunde swing kirilimi (devam).
- **CHoCH** (Change of Character): trend tersine swing kirilimi (donus).

Kirilim **kapanisla** teyit edilir — wick swing'i gecse bile kapanis gecmezse
kirilim yok. Tum zaman referanslari ``datetime`` (DataFrame ``DatetimeIndex``).

Saf fonksiyon: ``detect(ohlcv, config, **kwargs) -> list[StructureBreak]``.
``_swing_utils.find_swings`` ile swing'leri bulur.
"""

from __future__ import annotations

import pandas as pd

from smc_engine.detectors._swing_utils import find_swings
from smc_engine.types import (
    Bias,
    Direction,
    StructureBreak,
    StructureKind,
    SwingKind,
    SwingPoint,
)


def _prior_trend_from_swings(
    swing_highs: list[SwingPoint], swing_lows: list[SwingPoint]
) -> Bias:
    """NEUTRAL trend'de ilk kirilimda once gelen trendi swing yapisindan oku.

    - Son iki swing low yukseliyorsa (HL) -> onceki trend BULLISH.
    - Aksi halde son iki swing high alcaliyorsa (LH) -> onceki trend BEARISH.
    - Karar verilemezse NEUTRAL.

    HL sinyali LH'ye onceliklidir (yukselen dipler trendin daha guclu kaniti).
    """
    lows_ascending = (
        len(swing_lows) >= 2 and swing_lows[-1].price > swing_lows[-2].price
    )
    highs_descending = (
        len(swing_highs) >= 2 and swing_highs[-1].price < swing_highs[-2].price
    )
    if lows_ascending:
        return Bias.BULLISH
    if highs_descending:
        return Bias.BEARISH
    return Bias.NEUTRAL


def detect(ohlcv: pd.DataFrame, config, **kwargs) -> list[StructureBreak]:
    """Kapanis-teyitli CHoCH / BOS kirilimlarini tespit et.

    Algoritma (kronolojik tarama):
      1. ``find_swings`` ile tum swing'ler (config.swing_lookback).
      2. Her mum icin: o ana kadar olusmus ve henuz kirilmamis en guncel
         swing high / swing low.
      3. Mum **kapanisi** swing high'i asarsa -> LONG kirilim; swing low'u
         asarsa -> SHORT kirilim.
      4. Kirilim turu:
         - trend kirilim yonunde -> BOS
         - trend kirilim yonune ters -> CHoCH (ve trend doner)
         - trend NEUTRAL -> swing yapisindan onceki trend okunur
           (``_prior_trend_from_swings``); kirilim o trende ters ise CHoCH,
           ayni yonde / belirsizse BOS.
      5. Kirilan swing bir daha tetiklenmez (ilk teyit mumu kaydedilir).

    Args:
        ohlcv: ``open/high/low/close`` kolonlu, ``DatetimeIndex``'li df.
        config: ``swing_lookback`` ozelligi olan config nesnesi.
        **kwargs: orchestrator opsiyonel context (Faz 1A'da kullanilmaz).

    Returns:
        Timestamp'e gore artan sirali ``StructureBreak`` listesi.
    """
    lookback = getattr(config, "swing_lookback", 4)
    swings = find_swings(ohlcv, lookback=lookback)
    if not swings:
        return []

    index = ohlcv.index
    closes = ohlcv["close"].to_numpy()

    breaks: list[StructureBreak] = []
    trend = Bias.NEUTRAL

    # Henuz kirilmamis swing'leri takip etmek icin pointer'lar.
    swing_highs = [s for s in swings if s.kind == SwingKind.HIGH]
    swing_lows = [s for s in swings if s.kind == SwingKind.LOW]

    broken_high_ts: set = set()
    broken_low_ts: set = set()
    # Yapinin ilerlemesini takip et: kirilan swing'ten DAHA ESKI swing'ler
    # tekrar tetiklenmez — yalnizca kirilan swing'ten SONRA olusan swing
    # bir sonraki aktif swing olabilir.
    last_broken_high_ts = None
    last_broken_low_ts = None

    # KR-2: Swing'in real-time bilinebilirligi onun TEYIT BARI (sag-lookback
    # dolduktan sonra) ile sinirli. ``swing.confirm_timestamp <= ts`` koşulu
    # 'bu swing su an gercek-zamanli olarak bilinebilir' anlamina gelir.
    # Geri uyumluluk: confirm_timestamp None ise eski semantige (timestamp
    # bazli filtre) düşeriz -- diğer cagiranlar (range_detector) etkilenmez.
    def _is_known(s, cur_ts):
        ct = s.confirm_timestamp
        if ct is None:
            return s.timestamp < cur_ts
        return ct <= cur_ts

    # Ö-11: Two-pointer optimizasyonu. swing_highs/swing_lows zaten zaman
    # sirali; her bar icin "şu ana kadar bilinen swing'ler" prefix'idir.
    # Pointer'lar her bar ileri tasinir (asla geri); toplam maliyet O(n+s).
    known_high_n = 0  # swing_highs[:known_high_n] = bu bara kadar bilinen
    known_low_n = 0

    for i in range(len(ohlcv)):
        ts = index[i]
        close = float(closes[i])

        # Pointer'lari ilerlet: yeni teyit edilmis swing'ler dahil et.
        while known_high_n < len(swing_highs) and _is_known(swing_highs[known_high_n], ts):
            known_high_n += 1
        while known_low_n < len(swing_lows) and _is_known(swing_lows[known_low_n], ts):
            known_low_n += 1

        # O ana kadar olusmus VE TEYIT EDILMIS, henuz kirilmamis, son kirilandan
        # SONRA olusan en guncel swing high. Bilinen prefix'i SONDAN tara —
        # ilk gecerli swing (kirik degil + last_broken sonrasi) "en guncel".
        active_high = None
        for k in range(known_high_n - 1, -1, -1):
            s = swing_highs[k]
            if s.timestamp in broken_high_ts:
                continue
            if last_broken_high_ts is not None and s.timestamp <= last_broken_high_ts:
                # kirilan high'in oncesi: bunu ve oncesini atla (sirali).
                break
            active_high = s
            break
        active_low = None
        for k in range(known_low_n - 1, -1, -1):
            s = swing_lows[k]
            if s.timestamp in broken_low_ts:
                continue
            if last_broken_low_ts is not None and s.timestamp <= last_broken_low_ts:
                break
            active_low = s
            break

        # Trend okunurken kullanilacak: o ana kadar TEYIT EDILMIS swing listeleri.
        # Slice O(1) ile (bilinen prefix).
        prior_highs = swing_highs[:known_high_n]
        prior_lows = swing_lows[:known_low_n]

        # --- LONG kirilim: kapanis swing high'i asar ---
        if active_high is not None and close > active_high.price:
            if trend == Bias.BEARISH:
                kind = StructureKind.CHoCH
            elif trend == Bias.BULLISH:
                kind = StructureKind.BOS
            else:
                prior = _prior_trend_from_swings(prior_highs, prior_lows)
                kind = (
                    StructureKind.CHoCH
                    if prior == Bias.BEARISH
                    else StructureKind.BOS
                )
            breaks.append(
                StructureBreak(
                    kind=kind,
                    direction=Direction.LONG,
                    broken_swing_price=active_high.price,
                    confirm_candle_ts=ts.to_pydatetime(),
                    timeframe=getattr(config, "timeframe", None),
                )
            )
            broken_high_ts.add(active_high.timestamp)
            last_broken_high_ts = active_high.timestamp
            trend = Bias.BULLISH

        # --- SHORT kirilim: kapanis swing low'u asar ---
        if active_low is not None and close < active_low.price:
            if trend == Bias.BULLISH:
                kind = StructureKind.CHoCH
            elif trend == Bias.BEARISH:
                kind = StructureKind.BOS
            else:
                prior = _prior_trend_from_swings(prior_highs, prior_lows)
                kind = (
                    StructureKind.CHoCH
                    if prior == Bias.BULLISH
                    else StructureKind.BOS
                )
            breaks.append(
                StructureBreak(
                    kind=kind,
                    direction=Direction.SHORT,
                    broken_swing_price=active_low.price,
                    confirm_candle_ts=ts.to_pydatetime(),
                    timeframe=getattr(config, "timeframe", None),
                )
            )
            broken_low_ts.add(active_low.timestamp)
            last_broken_low_ts = active_low.timestamp
            trend = Bias.BEARISH

    return breaks
