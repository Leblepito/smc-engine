"""TDD test'leri — smc_engine/detectors/_swing_utils.py (Plan task 1.0).

4-mum swing kuralı: bir swing high, ``lookback`` mum öncesi ve sonrasındaki
tüm mumların high'larından kesin büyük olan mumdur (swing low simetrik).
Tüm zaman referansları timestamp bazlı (candle_idx YOK).
"""

from __future__ import annotations

import pandas as pd
import pytest

from smc_engine.detectors._swing_utils import find_swings
from smc_engine.types import SwingKind, SwingPoint


def _df(rows, start="2026-01-01", freq="h"):
    idx = pd.date_range(start=start, periods=len(rows), freq=freq)
    df = pd.DataFrame(rows, index=idx)
    return df[["open", "high", "low", "close", "volume"]]


def _candle(o, h, l, c, v=1000.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


# ============================================================
# Çıktı sözleşmesi
# ============================================================


def test_returns_list_of_swingpoints(fixture_trending_bullish):
    swings = find_swings(fixture_trending_bullish, lookback=4)
    assert isinstance(swings, list)
    assert all(isinstance(s, SwingPoint) for s in swings)
    for s in swings:
        assert s.timestamp in fixture_trending_bullish.index


def test_swings_sorted_by_timestamp(fixture_trending_bullish):
    swings = find_swings(fixture_trending_bullish, lookback=4)
    ts = [s.timestamp for s in swings]
    assert ts == sorted(ts)


# ============================================================
# Bilinen bullish dizi — fixture_trending_bullish docstring'i
#   swing low @ idx 5 (~90), swing high @ idx 11 (~113),
#   swing low @ idx 16 (~100)
# ============================================================


def test_detects_known_swings_in_trending_bullish(fixture_trending_bullish):
    df = fixture_trending_bullish
    swings = find_swings(df, lookback=4)

    lows = [s for s in swings if s.kind == SwingKind.LOW]
    highs = [s for s in swings if s.kind == SwingKind.HIGH]

    assert len(lows) >= 2
    assert len(highs) >= 1

    low_ts = {s.timestamp for s in lows}
    high_ts = {s.timestamp for s in highs}

    assert df.index[5] in low_ts
    assert df.index[11] in high_ts
    assert df.index[16] in low_ts


def test_swing_price_matches_candle_extreme(fixture_trending_bullish):
    df = fixture_trending_bullish
    swings = find_swings(df, lookback=4)
    for s in swings:
        candle = df.loc[s.timestamp]
        if s.kind == SwingKind.HIGH:
            assert s.price == candle["high"]
        else:
            assert s.price == candle["low"]


# ============================================================
# Bilinen bearish dizi — düşen yapı
# ============================================================


def test_detects_swing_high_in_bearish_sequence():
    rows = [
        _candle(100, 101, 99, 100),   # 0
        _candle(100, 103, 99, 102),   # 1
        _candle(102, 105, 101, 104),  # 2
        _candle(104, 107, 103, 106),  # 3
        _candle(106, 109, 105, 108),  # 4
        _candle(108, 115, 107, 110),  # 5  swing HIGH (115)
        _candle(110, 111, 105, 106),  # 6
        _candle(106, 107, 101, 102),  # 7
        _candle(102, 103, 97, 98),    # 8
        _candle(98, 99, 93, 94),      # 9
        _candle(94, 95, 89, 90),      # 10
    ]
    df = _df(rows)
    swings = find_swings(df, lookback=4)
    highs = [s for s in swings if s.kind == SwingKind.HIGH]
    assert any(s.timestamp == df.index[5] and s.price == 115 for s in highs)


# ============================================================
# Edge case: düz piyasa — swing yok
# ============================================================


def test_flat_market_no_swings():
    rows = [_candle(100, 101, 99, 100) for _ in range(15)]
    df = _df(rows)
    swings = find_swings(df, lookback=4)
    assert swings == []


# ============================================================
# Edge case: tek-mum spike
# ============================================================


def test_single_candle_spike_is_swing():
    rows = [_candle(100, 101, 99, 100) for _ in range(4)]
    rows.append(_candle(100, 120, 99, 101))  # idx 4: spike high
    rows += [_candle(100, 101, 99, 100) for _ in range(4)]
    df = _df(rows)
    swings = find_swings(df, lookback=4)
    highs = [s for s in swings if s.kind == SwingKind.HIGH]
    assert any(s.timestamp == df.index[4] and s.price == 120 for s in highs)


def test_single_candle_low_spike_is_swing():
    rows = [_candle(100, 101, 99, 100) for _ in range(4)]
    rows.append(_candle(100, 101, 80, 99))  # idx 4: spike low
    rows += [_candle(100, 101, 99, 100) for _ in range(4)]
    df = _df(rows)
    swings = find_swings(df, lookback=4)
    lows = [s for s in swings if s.kind == SwingKind.LOW]
    assert any(s.timestamp == df.index[4] and s.price == 80 for s in lows)


# ============================================================
# Edge case: çok kısa DataFrame
# ============================================================


def test_too_short_dataframe_returns_empty():
    rows = [_candle(100, 101, 99, 100) for _ in range(5)]
    df = _df(rows)
    swings = find_swings(df, lookback=4)
    assert swings == []


def test_lookback_parameter_respected():
    rows = [
        _candle(100, 101, 99, 100),   # 0
        _candle(100, 102, 99, 101),   # 1
        _candle(101, 110, 100, 105),  # 2  lokal tepe
        _candle(105, 106, 99, 100),   # 3
        _candle(100, 104, 99, 101),   # 4
        _candle(101, 112, 100, 108),  # 5  daha büyük tepe
        _candle(108, 109, 99, 100),   # 6
        _candle(100, 102, 99, 101),   # 7
        _candle(101, 103, 99, 100),   # 8
    ]
    df = _df(rows)
    s2 = find_swings(df, lookback=2)
    highs2 = {s.timestamp for s in s2 if s.kind == SwingKind.HIGH}
    assert df.index[2] in highs2
