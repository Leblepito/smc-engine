"""Sentetik OHLCV DataFrame ureticileri -- her detektor icin bilinen senaryo.

Her fonksiyon pd.DatetimeIndex'li, open/high/low/close/volume kolonlu
bir DataFrame doner. Beklenen detektor ciktisi her fonksiyonun docstring'inde.
conftest.py bunlari pytest fixture'i olarak sarar.
"""

from __future__ import annotations

import pandas as pd


def _df(rows, start, freq):
    """rows: open/high/low/close/volume dict listesi -> DatetimeIndex'li DataFrame."""
    idx = pd.date_range(start=start, periods=len(rows), freq=freq)
    df = pd.DataFrame(rows, index=idx)
    return df[["open", "high", "low", "close", "volume"]]


def _candle(o, h, l, c, v=1000.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


# ============================================================
# 1. fixture_trending_bullish -- LL -> HL -> HH (structure)
# ============================================================


def make_trending_bullish():
    """Yukselen yapi: dusuk dip (LL), daha yuksek dip (HL), onceki tepeyi kiran HH.

    Beklenen cikti (structure_detector, swing_lookback=4):
      - Swing low @ ~idx 5  (ilk LL bolgesi dibi, price ~ 90)
      - Swing high @ ~idx 11 (ilk tepe, price ~ 113)
      - Swing low @ ~idx 16 (HL -- daha yuksek dip, price ~ 99)
      - Bullish BOS: idx ~22 mumu onceki swing high'i (113) KAPANISLA asar.
      - Yon: bullish, trend devami -> kind=BOS.
    """
    rows = [
        _candle(100, 102, 99, 101),   # 0
        _candle(101, 103, 98, 99),    # 1
        _candle(99, 100, 95, 96),     # 2
        _candle(96, 97, 92, 93),      # 3
        _candle(93, 94, 90, 91),      # 4
        _candle(91, 92, 89, 90),      # 5  swing LOW (~90)
        _candle(90, 95, 90, 94),      # 6
        _candle(94, 99, 93, 98),      # 7
        _candle(98, 104, 97, 103),    # 8
        _candle(103, 109, 102, 108),  # 9
        _candle(108, 112, 107, 111),  # 10
        _candle(111, 113, 110, 112),  # 11 swing HIGH (~113)
        _candle(112, 112, 108, 109),  # 12  (high 112 < swing high 113)
        _candle(109, 110, 104, 105),  # 13
        _candle(105, 106, 101, 102),  # 14
        _candle(102, 103, 100, 101),  # 15
        _candle(101, 102, 99, 101),   # 16 swing LOW / HL (~99, kesin swing)
        _candle(101, 105, 100, 104),  # 17
        _candle(104, 108, 103, 107),  # 18
        _candle(107, 111, 106, 110),  # 19
        _candle(110, 113, 109, 112),  # 20
        _candle(112, 115, 111, 114),  # 21
        _candle(114, 118, 113, 117),  # 22 BOS: close 117 > prev swing high 113
        _candle(117, 120, 116, 119),  # 23
        _candle(119, 122, 118, 121),  # 24
        _candle(121, 123, 120, 122),  # 25
    ]
    return _df(rows, "2026-01-01", "h")


# ============================================================
# 2. fixture_range_bound -- bilinen RH/RL/EQ (range)
# ============================================================


def make_range_bound():
    """Yatay range: fiyat 120 (RH) ve 80 (RL) arasinda salinir.

    Beklenen cikti (range_detector, swing_lookback=4):
      - find_swings ile 2 swing HIGH ~120 (idx 11, 23) ve 2 swing LOW ~80
        (idx 5, 17) -- coklu-swing dogrulama saglanir.
      - Range.high  = 120
      - Range.low   = 80
      - Range.equilibrium = 100  ((120+80)/2)
      - premium_zone = (100, 120), discount_zone = (80, 100)
    """
    rows = [
        _candle(100, 103, 98, 99),    # 0  filler
        _candle(99, 102, 96, 97),     # 1
        _candle(97, 100, 94, 95),     # 2
        _candle(95, 98, 92, 93),      # 3
        _candle(93, 96, 90, 91),      # 4
        _candle(91, 93, 80, 84),      # 5  swing LOW 80 (#1)
        _candle(84, 90, 83, 89),      # 6
        _candle(89, 95, 88, 94),      # 7
        _candle(94, 100, 93, 99),     # 8
        _candle(99, 105, 98, 104),    # 9
        _candle(104, 110, 103, 109),  # 10
        _candle(109, 120, 108, 116),  # 11 swing HIGH 120 (#1)
        _candle(116, 118, 111, 113),  # 12
        _candle(113, 115, 107, 109),  # 13
        _candle(109, 111, 103, 105),  # 14
        _candle(105, 107, 99, 101),   # 15
        _candle(101, 103, 95, 97),    # 16
        _candle(97, 99, 80, 85),      # 17 swing LOW 80 (#2)
        _candle(85, 91, 84, 90),      # 18
        _candle(90, 96, 89, 95),      # 19
        _candle(95, 101, 94, 100),    # 20
        _candle(100, 106, 99, 105),   # 21
        _candle(105, 111, 104, 110),  # 22
        _candle(110, 120, 109, 115),  # 23 swing HIGH 120 (#2)
        _candle(115, 117, 110, 112),  # 24
        _candle(112, 114, 107, 109),  # 25
        _candle(109, 111, 104, 106),  # 26
        _candle(106, 108, 101, 103),  # 27
        _candle(103, 105, 99, 101),   # 28
    ]
    return _df(rows, "2026-01-01", "h")


# ============================================================
# 3. fixture_known_ob -- pump/dump + istekli breakout (zone)
# ============================================================


def make_known_ob():
    """Bullish Order Block: pes pese dusus mumlari + OB mumu + istekli breakout.

    Beklenen cikti (zone_detector, ob_breakout_threshold=1.5):
      - DEMAND zone, OB mumu idx 5: top ~ 96 (open), bottom ~ 92 (low).
      - idx 6 mumu istekli breakout: body (98->108=10) onceki mumun
        range'inin (96-92=4) >=1.5 kati -> tetiklenir.
      - Zone.status = FRESH, Zone.age_bars = 0.
    """
    rows = [
        _candle(110, 111, 108, 109),  # 0
        _candle(109, 110, 106, 107),  # 1
        _candle(107, 108, 104, 105),  # 2
        _candle(105, 106, 102, 103),  # 3
        _candle(103, 104, 100, 101),  # 4
        _candle(96, 97, 92, 93),      # 5  OB mumu (son bearish mum)
        _candle(98, 109, 97, 108),    # 6  istekli bullish breakout
        _candle(108, 114, 107, 113),  # 7
        _candle(113, 118, 112, 117),  # 8
        _candle(117, 121, 116, 120),  # 9
        _candle(120, 123, 119, 122),  # 10
        _candle(122, 125, 121, 124),  # 11
    ]
    return _df(rows, "2026-01-01", "h")


# ============================================================
# 4. fixture_known_fvg -- 3-mum FVG (imbalance)
# ============================================================


def make_known_fvg():
    """Bullish FVG: 3 mumluk dizide 1. mumun high'i ile 3. mumun low'u arasinda bosluk.

    Beklenen cikti (imbalance_detector):
      - 1 adet bullish FVG, idx 1-3 (orta mum idx 2):
        bottom = candle[1].high = 103, top = candle[3].low = 108.
      - filled = False, fill_ratio = 0.0, direction = LONG.
      - Bosluk = 108 - 103 = 5 (fvg_min_gap_atr filtresini gececek kadar genis).
    """
    rows = [
        _candle(100, 105, 99, 104),    # 0  high 105 (>= candle[2].low; (0,1,2) FVG yok)
        _candle(101, 103, 100, 102),   # 1  FVG alt sinir: high 103
        _candle(105, 112, 104, 110),   # 2  orta mum (impulse) -- FVG burada teyit
        _candle(110, 115, 108, 113),   # 3  FVG ust sinir: low 108
        _candle(113, 116, 111, 114),   # 4
        _candle(114, 117, 112, 115),   # 5
        _candle(115, 118, 113, 116),   # 6
    ]
    return _df(rows, "2026-01-01", "h")


# ============================================================
# 5. fixture_sweep -- equal high + sweep/reclaim (liquidity)
# ============================================================


def make_sweep():
    """Equal high'lar + likidite sweep: iki mum ~ayni tepeye (130) ulasir,
    sonra bir mum bu tepeyi wick ile asip ALTINA kapatir (sweep).

    Beklenen cikti (liquidity_detector, equal_level_tolerance=0.001):
      - Equal high tespiti: idx 3 (high 130.0) ve idx 7 (high 130.05) -- fark < %0.1.
      - SWEEP event @ idx 9: high 131 > equal high 130 ama close 127 < 130.
      - direction = SHORT (yukari likidite alindi), reclaimed = False.
      - significance = HIGH (equal high coklu temas).
    """
    rows = [
        _candle(120, 122, 119, 121),     # 0
        _candle(121, 125, 120, 124),     # 1
        _candle(124, 128, 123, 127),     # 2
        _candle(127, 130, 126, 129),     # 3  equal high #1 (130.0)
        _candle(129, 130, 125, 126),     # 4
        _candle(126, 127, 122, 123),     # 5
        _candle(123, 126, 122, 125),     # 6
        _candle(125, 130.05, 124, 129),  # 7  equal high #2 (130.05 ~ 130)
        _candle(129, 130, 127, 128),     # 8
        _candle(128, 131, 127, 127),     # 9  SWEEP: high 131 > 130, close 127 < 130
        _candle(127, 128, 122, 123),     # 10
        _candle(123, 124, 119, 120),     # 11
    ]
    return _df(rows, "2026-01-01", "h")


# ============================================================
# 6. fixture_levels -- hafta/ay acilisi bilinen tarihler (level)
# ============================================================


def make_levels():
    """Bilinen takvim tarihleri iceren D1 OHLCV -- level_detector icin.

    Dizi 2026-01-01 (Persembe, yil acilisi) baslar, 30 gun D1 mum.
    Beklenen cikti (level_detector + time_utils):
      - YO (Year Open) = 2026-01-01 00:00 UTC, price = candle[0].open = 100.
      - Aylik acilis MO = 2026-01-01 (ilk is gunu Persembe).
      - Hafta acilislari week_open ile hesaplanir (Pazar 21:00 UTC).
      - Monday H/L: 2026-01-05 (ilk Pazartesi) mumunun high/low'u.
    """
    rows = []
    base = 100.0
    for i in range(30):
        o = base + i
        rows.append(_candle(o, o + 3, o - 2, o + 1))
    return _df(rows, "2026-01-01", "D")


# ============================================================
# 7. fixture_multi_tf -- D1 + H4 + M15 hizalanmis (orchestrator)
# ============================================================


def make_multi_tf():
    """Uc hizalanmis TF seti -- orchestrator MTF kaskad testi icin.

    Hepsi 2026-01-01 00:00 UTC'de baslar, ayni 5 gunluk pencereyi kapsar:
      - D1: 5 mum (yukselen -> bullish HTF bias beklenir)
      - H4: 30 mum (5 gun x 6 mum/gun)
      - M15: 480 mum (5 gun x 96 mum/gun)
    Tum TF'lerin kapanis zamanlari hizali.

    Beklenen cikti (orchestrator):
      - htf_bias = BULLISH (D1 dizisi monoton yukseliyor)
      - per_tf dict'inde D1/H4/M15 anahtarlari mevcut
      - MarketPicture.at_timestamp son M15 mumunun timestamp'i
    """
    d1_rows = []
    for i in range(5):
        o = 100.0 + i * 10
        d1_rows.append(_candle(o, o + 8, o - 2, o + 6))
    d1 = _df(d1_rows, "2026-01-01", "D")

    h4_rows = []
    for i in range(30):
        o = 100.0 + i * 1.6
        h4_rows.append(_candle(o, o + 2, o - 1, o + 1.2))
    h4 = _df(h4_rows, "2026-01-01", "4h")

    m15_rows = []
    for i in range(480):
        o = 100.0 + i * 0.1
        m15_rows.append(_candle(o, o + 0.3, o - 0.2, o + 0.08))
    m15 = _df(m15_rows, "2026-01-01", "15min")

    return {"D1": d1, "H4": h4, "M15": m15}


# ============================================================
# 8. fixture_choch_bullish -- dusen yapi + son LH kirilimi (structure)
# ============================================================


def make_choch_bullish():
    """Bullish CHoCH: dusen yapi (LH/LL), sonra son lower-high KAPANISLA asilir.

    Swing dizisi (find_swings, lookback=4):
      - swing HIGH @ idx 5  (130)
      - swing LOW  @ idx 11 (100)
      - swing HIGH @ idx 17 (120, LH -- 120 < 130)
      - swing LOW  @ idx 23 (90,  LL -- 90 < 100)

    Beklenen cikti (structure_detector):
      - idx 23 mumu swing low @11 (100)'u KAPANISLA asar (close 93) -> SHORT BOS
        (dusen yapi devami; NEUTRAL trend, highs descending).
      - idx 28 mumu swing high @17 (120)'yi KAPANISLA asar (close 121) -> LONG CHoCH
        (trend BEARISH iken ters yon kirilimi -> karakter degisimi).
      - idx 28'den onceki idx'lerin wick'leri 120'yi gecmez (sadece kapanis teyidi).
    """
    rows = [
        _candle(120, 122, 118, 119),  # 0
        _candle(119, 121, 116, 117),  # 1
        _candle(117, 119, 114, 115),  # 2
        _candle(115, 117, 112, 113),  # 3
        _candle(113, 115, 111, 112),  # 4
        _candle(112, 130, 111, 128),  # 5  swing HIGH 130
        _candle(128, 129, 124, 125),  # 6
        _candle(125, 126, 121, 122),  # 7
        _candle(122, 123, 118, 119),  # 8
        _candle(119, 120, 115, 116),  # 9
        _candle(116, 117, 113, 114),  # 10
        _candle(114, 116, 100, 103),  # 11 swing LOW 100
        _candle(103, 107, 102, 106),  # 12
        _candle(106, 110, 105, 109),  # 13
        _candle(109, 113, 108, 112),  # 14
        _candle(112, 116, 111, 115),  # 15
        _candle(115, 118, 114, 117),  # 16
        _candle(117, 120, 116, 118),  # 17 swing HIGH 120 (LH)
        _candle(118, 119, 114, 115),  # 18
        _candle(115, 116, 111, 112),  # 19
        _candle(112, 113, 108, 109),  # 20
        _candle(109, 110, 105, 106),  # 21
        _candle(106, 107, 102, 103),  # 22
        _candle(103, 104, 90, 93),    # 23 swing LOW 90 (LL); close 93 < swing low@11 (100) -> SHORT BOS
        _candle(93, 97, 92, 96),      # 24
        _candle(96, 100, 95, 99),     # 25
        _candle(99, 103, 98, 102),    # 26
        _candle(102, 106, 101, 105),  # 27
        _candle(105, 121, 104, 121),  # 28 close 121 > swing high@17 (120) -> LONG CHoCH
        _candle(121, 125, 120, 124),  # 29
        _candle(124, 128, 123, 127),  # 30
        _candle(127, 131, 126, 130),  # 31
        _candle(130, 134, 129, 133),  # 32
    ]
    return _df(rows, "2026-01-01", "h")


# ============================================================
# 9. fixture_choch_bearish -- yukselen yapi + son HL kirilimi (structure)
# ============================================================


def make_choch_bearish():
    """Bearish CHoCH: yukselen yapi (HL/HH), sonra son higher-low KAPANISLA asilir.

    Swing dizisi (find_swings, lookback=4):
      - swing LOW  @ idx 5  (100)
      - swing HIGH @ idx 11 (130)
      - swing LOW  @ idx 17 (110, HL -- 110 > 100)
      - swing HIGH @ idx 23 (127, LH -- 127 < 130)

    Beklenen cikti (structure_detector):
      - idx 28 mumu swing low @17 (110)'u KAPANISLA asagi kirar (close 109)
        -> SHORT CHoCH (NEUTRAL trend, lows ascending -> onceki trend bullish,
        ters yon kirilimi -> karakter degisimi).
      - idx 28 ilk teyit mumu; oncesinde kapanis 110 altina inmez.
    """
    rows = [
        _candle(110, 112, 108, 109),  # 0
        _candle(109, 111, 106, 107),  # 1
        _candle(107, 109, 104, 105),  # 2
        _candle(105, 107, 102, 103),  # 3
        _candle(103, 105, 101, 102),  # 4
        _candle(102, 104, 100, 103),  # 5  swing LOW 100
        _candle(103, 107, 102, 106),  # 6
        _candle(106, 110, 105, 109),  # 7
        _candle(109, 113, 108, 112),  # 8
        _candle(112, 116, 111, 115),  # 9
        _candle(115, 120, 114, 119),  # 10
        _candle(119, 130, 118, 127),  # 11 swing HIGH 130
        _candle(127, 128, 123, 124),  # 12
        _candle(124, 125, 120, 121),  # 13
        _candle(121, 122, 117, 118),  # 14
        _candle(118, 119, 114, 115),  # 15
        _candle(115, 116, 112, 113),  # 16
        _candle(113, 115, 110, 114),  # 17 swing LOW 110 (HL)
        _candle(114, 118, 113, 117),  # 18
        _candle(117, 121, 116, 120),  # 19
        _candle(120, 123, 119, 122),  # 20
        _candle(122, 124, 121, 123),  # 21
        _candle(123, 125, 122, 124),  # 22
        _candle(124, 127, 123, 125),  # 23 swing HIGH 127 (LH)
        _candle(125, 126, 121, 122),  # 24
        _candle(122, 123, 118, 119),  # 25
        _candle(119, 120, 115, 116),  # 26
        _candle(116, 117, 112, 113),  # 27
        _candle(113, 114, 108, 109),  # 28 close 109 < swing low@17 (110) -> SHORT CHoCH
        _candle(109, 110, 105, 106),  # 29
        _candle(106, 107, 102, 103),  # 30
        _candle(103, 104, 99, 100),   # 31
        _candle(100, 101, 96, 97),    # 32
    ]
    return _df(rows, "2026-01-01", "h")
