"""TDD test'leri — smc_engine/detectors/structure_detector.py (Plan task 1.1).

CHoCH (karakter degisimi / trend donusu) ve BOS (yapi kirilimi / trend devami).
Kirilim **kapanisla** teyit edilir (wick yetmez). confirm_candle_ts timestamp bazli.
"""

from __future__ import annotations

import pandas as pd
import pytest

from smc_engine.config import SMCConfig
from smc_engine.detectors.structure_detector import detect
from smc_engine.types import Direction, StructureBreak, StructureKind


def _df(rows, start="2026-01-01", freq="h"):
    idx = pd.date_range(start=start, periods=len(rows), freq=freq)
    return pd.DataFrame(rows, index=idx)[["open", "high", "low", "close", "volume"]]


def _candle(o, h, l, c, v=1000.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


@pytest.fixture
def config():
    return SMCConfig()


# ============================================================
# Cikti sozlesmesi
# ============================================================


def test_returns_list_of_structurebreaks(fixture_trending_bullish, config):
    breaks = detect(fixture_trending_bullish, config)
    assert isinstance(breaks, list)
    assert all(isinstance(b, StructureBreak) for b in breaks)


def test_accepts_kwargs(fixture_trending_bullish, config):
    # imza uyumu: orchestrator **kwargs gecirebilir
    breaks = detect(fixture_trending_bullish, config, some_context=123)
    assert isinstance(breaks, list)


# ============================================================
# BOS — trend devami (fixture_trending_bullish)
#   swing high @ idx 11 (113), HL @ idx 16 -> LONG break = BOS
# ============================================================


def test_bos_trend_continuation(fixture_trending_bullish, config):
    df = fixture_trending_bullish
    breaks = detect(df, config)
    longs = [b for b in breaks if b.direction == Direction.LONG]
    assert len(longs) == 1
    b = longs[0]
    assert b.kind == StructureKind.BOS
    assert b.broken_swing_price == 113.0
    # ilk kapanisla teyit eden mum idx 21 (close 114 > 113)
    assert b.confirm_candle_ts == df.index[21]


# ============================================================
# Bullish CHoCH (fixture_choch_bullish)
#   idx 23 SHORT BOS (dusen yapi devami), idx 28 LONG CHoCH (donus)
# ============================================================


def test_bullish_choch_after_downtrend(fixture_choch_bullish, config):
    df = fixture_choch_bullish
    breaks = detect(df, config)

    shorts = [b for b in breaks if b.direction == Direction.SHORT]
    longs = [b for b in breaks if b.direction == Direction.LONG]

    assert len(shorts) == 1
    assert shorts[0].kind == StructureKind.BOS
    assert shorts[0].broken_swing_price == 100.0
    assert shorts[0].confirm_candle_ts == df.index[23]

    assert len(longs) == 1
    assert longs[0].kind == StructureKind.CHoCH
    assert longs[0].direction == Direction.LONG
    assert longs[0].broken_swing_price == 120.0
    assert longs[0].confirm_candle_ts == df.index[28]


# ============================================================
# Bearish CHoCH (fixture_choch_bearish)
#   yukselen yapi -> son HL kirilimi -> SHORT CHoCH
# ============================================================


def test_bearish_choch_after_uptrend(fixture_choch_bearish, config):
    df = fixture_choch_bearish
    breaks = detect(df, config)

    shorts = [b for b in breaks if b.direction == Direction.SHORT]
    assert len(shorts) == 1
    b = shorts[0]
    assert b.kind == StructureKind.CHoCH
    assert b.direction == Direction.SHORT
    assert b.broken_swing_price == 110.0
    assert b.confirm_candle_ts == df.index[28]


# ============================================================
# Kapanis teyidi — wick yetmez
# ============================================================


def test_wick_through_swing_does_not_confirm(config):
    """Bir mum swing high'i WICK ile asar ama altinda KAPATIR -> kirilim YOK.
    Sonraki mum kapanisla asar -> o mum teyit eder.
    """
    rows = [
        _candle(100, 102, 99, 101),   # 0
        _candle(101, 103, 100, 102),  # 1
        _candle(102, 104, 101, 103),  # 2
        _candle(103, 105, 102, 104),  # 3
        _candle(104, 106, 103, 105),  # 4
        _candle(105, 120, 104, 118),  # 5  swing HIGH 120
        _candle(118, 119, 113, 114),  # 6
        _candle(114, 115, 109, 110),  # 7
        _candle(110, 111, 105, 106),  # 8
        _candle(106, 107, 101, 102),  # 9
        _candle(102, 103, 99, 100),   # 10  swing LOW 99
        _candle(100, 104, 99, 103),   # 11
        _candle(103, 108, 102, 107),  # 12
        _candle(107, 112, 106, 111),  # 13
        _candle(111, 116, 110, 115),  # 14
        _candle(115, 122, 114, 119),  # 15  WICK: high 122 > 120 ama close 119 < 120 -> teyit YOK
        _candle(119, 121, 118, 121),  # 16  CLOSE 121 > 120 -> teyit
        _candle(121, 124, 120, 123),  # 17
        _candle(123, 126, 122, 125),  # 18
        _candle(125, 128, 124, 127),  # 19
        _candle(127, 130, 126, 129),  # 20
    ]
    df = _df(rows)
    breaks = detect(df, config)
    longs = [b for b in breaks if b.direction == Direction.LONG]
    assert len(longs) == 1
    b = longs[0]
    assert b.broken_swing_price == 120.0
    # idx 15 wick ile asar ama teyit etmez; idx 16 kapanisla teyit eder
    assert b.confirm_candle_ts == df.index[16]
    assert b.confirm_candle_ts != df.index[15]


# ============================================================
# Edge case: duz piyasa -> swing yok -> kirilim yok
# ============================================================


def test_flat_market_no_breaks(config):
    rows = [_candle(100, 101, 99, 100) for _ in range(20)]
    df = _df(rows)
    breaks = detect(df, config)
    assert breaks == []


# ============================================================
# confirm_candle_ts her zaman DataFrame index'inde ve datetime
# ============================================================


def test_confirm_ts_in_index(fixture_choch_bullish, config):
    df = fixture_choch_bullish
    breaks = detect(df, config)
    assert len(breaks) >= 1
    for b in breaks:
        assert b.confirm_candle_ts in df.index


# ============================================================
# KR-2 — Swing teyit-offset'i: swing kendi mum zamanindan
# `lookback` bar SONRA bilinebilir. Structure detektoru bu
# teyit barindan ONCE swing'i aktif saymamali.
# ============================================================

def test_swing_not_active_before_confirm_bar_stale_selection(config):
    """KR-2: structure_detector full-slice'ta find_swings cagiriyor; bu yuzden
    swing'in TEYIT BARINDAN ONCE 'aktif' sayilmasi tehlikesi var. Bu test
    'eski-swing yanlis seciliyor' sızıntısını yakalar.

    Senaryo: iki swing high
      - swing@idx=6  (price=110), teyit barı idx 10 (sag-lookback=4)
      - swing@idx=12 (price=120), teyit barı idx 16

    Test bari idx 13: close=115 (> 110, < 120). Bar'in high=119 (< 120,
    swing@12'nin sag-lookback validity'sini bozmaz).

    Gercek-zamanli (real-time): idx 13'te yalniz swing@6 BILINEBILIR (sag
    bar 7..10 kapandi); swing@12 henuz teyit edilmedi (sag bar 13..16 lazim).
    Yani idx 13'te kapanis 115 > 110 -> swing@6 (110) kirilimi LONG.

    Bug (KR-2): tum df verili -> find_swings swing@12'yi de gorur ->
    active_high secimi swing@12 (price=120). Close 115 < 120 -> KIRILIM YOK.
    Bu olduğunda asagidaki assert FAIL ederek bug'i aciklar.
    """
    rows = []
    for i in range(6):
        rows.append(_candle(100, 105, 99, 102))  # 0..5
    rows.append(_candle(102, 110, 101, 104))     # 6 swing high (110)
    rows.append(_candle(104, 108, 103, 105))     # 7
    rows.append(_candle(105, 109, 104, 106))     # 8
    rows.append(_candle(106, 109, 105, 107))     # 9
    rows.append(_candle(107, 109, 106, 108))     # 10 (swing@6 confirmed)
    rows.append(_candle(108, 115, 107, 109))     # 11
    rows.append(_candle(109, 120, 108, 110))     # 12 swing high (120)
    rows.append(_candle(110, 119, 108, 115))     # 13 TEST: close=115>110
    rows.append(_candle(115, 118, 113, 116))     # 14
    rows.append(_candle(116, 117, 114, 116))     # 15
    rows.append(_candle(116, 118, 114, 117))     # 16 (swing@12 confirmed)
    df = _df(rows)

    breaks = detect(df, config)
    # Real-time davranisi: idx 13'te swing@6 (110) LONG kirilim.
    long_breaks = [b for b in breaks if b.direction == Direction.LONG]
    matched_110 = [b for b in long_breaks if b.broken_swing_price == 110.0]
    assert len(matched_110) >= 1, (
        "KR-2: real-time davranis -> idx 13'te swing@6 (110) "
        f"kirilimi olmali; bulunan breaks={breaks}"
    )
    # Kirilim idx 13 veya daha ERKEN bir bar olmali (idx 13'te zaten 115>110).
    assert matched_110[0].confirm_candle_ts <= df.index[13].to_pydatetime(), (
        f"KR-2: swing@6 kirilimi gec teyit edildi: {matched_110[0].confirm_candle_ts}"
    )
    # YANLIS davranis: idx 13'te swing@12 (120) aktif saymak -> bu durumda
    # broken=120 olan bir LONG break beklenir; LONG break broken=120 olmamali
    # (swing@12 henuz teyit edilmemis idi).
    matched_120_at_13 = [
        b for b in long_breaks
        if b.broken_swing_price == 120.0
        and b.confirm_candle_ts == df.index[13].to_pydatetime()
    ]
    assert len(matched_120_at_13) == 0, (
        "KR-2: swing@12 teyit edilmeden ONCE kirilim adayi olarak kullanildi"
    )


def test_swing_active_only_after_confirm_offset(config):
    """Daha sade test: tek swing high, kendi sag-lookback penceresinde swing
    aktif sayilmamali. Burada ayni df icindeki 'sonraki bar' analizinde
    swing'in TEYIT BARINDAN ONCE sıçramamasini test ediyoruz.

    Swing@idx=4 (high=110), lookback=4 -> teyit barı idx=8.
    Tek olası kirilim aday bar idx=9 (close>110 olmasi icin bar'in high>110
    olmali, ki bu zaten swing'in sag-lookback validity'sini bozmaz -- idx 5..8
    arasinda olmadigi icin).
    """
    cfg = SMCConfig()
    rows = []
    for i in range(4):
        rows.append(_candle(100, 105, 99, 102))  # 0..3
    rows.append(_candle(102, 110, 101, 104))     # 4 swing high
    rows.append(_candle(104, 108, 103, 105))     # 5
    rows.append(_candle(105, 109, 104, 106))     # 6
    rows.append(_candle(106, 109, 105, 107))     # 7
    rows.append(_candle(107, 109, 106, 108))     # 8 (swing@4 confirmed)
    rows.append(_candle(108, 115, 107, 113))     # 9 close=113>110 -> kirilim
    rows.append(_candle(113, 116, 112, 114))     # 10
    df = _df(rows)

    breaks = detect(df, cfg)
    long_breaks = [b for b in breaks if b.direction == Direction.LONG and b.broken_swing_price == 110.0]
    assert len(long_breaks) >= 1, f"swing@4 kirilimi gormedi: {breaks}"
    # Kirilim idx 9 olmali; idx 4..8 araliginda OLMAMALI.
    confirm_ts = long_breaks[0].confirm_candle_ts
    assert confirm_ts >= df.index[9].to_pydatetime(), (
        f"KR-2: swing kirilimi teyit barindan ONCE isaretlendi: {confirm_ts}"
    )
