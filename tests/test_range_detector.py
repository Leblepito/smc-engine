"""TDD test'leri — smc_engine/detectors/range_detector.py (Plan task 1.2).

RH/RL/EQ hesabi; _swing_utils ile coklu-swing dogrulama; EQ=(RH+RL)/2;
premium/discount bolgeleri; formed_at timestamp. Edge case: tek-yonlu trend.
"""

from __future__ import annotations

import pandas as pd
import pytest

from smc_engine.config import SMCConfig
from smc_engine.detectors.range_detector import detect
from smc_engine.types import Range


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


def test_returns_list_of_ranges(fixture_range_bound, config):
    ranges = detect(fixture_range_bound, config)
    assert isinstance(ranges, list)
    assert all(isinstance(r, Range) for r in ranges)


def test_accepts_kwargs(fixture_range_bound, config):
    ranges = detect(fixture_range_bound, config, some_context=1)
    assert isinstance(ranges, list)


# ============================================================
# Bilinen range — fixture_range_bound: RH=120, RL=80, EQ=100
# ============================================================


def test_detects_known_range(fixture_range_bound, config):
    ranges = detect(fixture_range_bound, config)
    assert len(ranges) >= 1
    r = ranges[0]
    assert r.high == 120.0
    assert r.low == 80.0
    assert r.equilibrium == 100.0  # (120 + 80) / 2


def test_premium_discount_zones(fixture_range_bound, config):
    r = detect(fixture_range_bound, config)[0]
    # discount = EQ alti (RL..EQ), premium = EQ ustu (EQ..RH)
    assert r.discount_zone == (80.0, 100.0)
    assert r.premium_zone == (100.0, 120.0)


def test_formed_at_is_timestamp(fixture_range_bound, config):
    df = fixture_range_bound
    r = detect(df, config)[0]
    # formed_at: range'i dogrulayan son swing'in timestamp'i, index'te olmali
    assert r.formed_at in df.index


# ============================================================
# Coklu-swing dogrulama: tek swing high/low range olusturmaz
# ============================================================


def test_single_swing_pair_insufficient(config):
    """Yalnizca 1 swing high + 1 swing low -> range dogrulanmaz (>=2 gerekir)."""
    rows = [
        _candle(100, 102, 99, 101),   # 0
        _candle(101, 103, 100, 102),  # 1
        _candle(102, 104, 101, 103),  # 2
        _candle(103, 105, 102, 104),  # 3
        _candle(104, 106, 103, 105),  # 4
        _candle(105, 120, 104, 110),  # 5  tek swing HIGH
        _candle(110, 112, 90, 95),    # 6
        _candle(95, 97, 85, 88),      # 7
        _candle(88, 90, 82, 85),      # 8
        _candle(85, 87, 81, 83),      # 9
        _candle(83, 85, 70, 74),      # 10 tek swing LOW
        _candle(74, 80, 73, 78),      # 11
        _candle(78, 84, 77, 82),      # 12
        _candle(82, 88, 81, 86),      # 13
        _candle(86, 92, 85, 90),      # 14
    ]
    df = _df(rows)
    ranges = detect(df, config)
    assert ranges == []


# ============================================================
# Edge case: tek-yonlu trend -> range olusmaz
# ============================================================


def test_one_directional_trend_no_range(fixture_trending_bullish, config):
    ranges = detect(fixture_trending_bullish, config)
    assert ranges == []


def test_strict_uptrend_no_range(config):
    rows = [_candle(100 + i, 100 + i + 2, 100 + i - 1, 100 + i + 1) for i in range(25)]
    df = _df(rows)
    ranges = detect(df, config)
    assert ranges == []


# ============================================================
# EQ her zaman high/low ortasi
# ============================================================


def test_equilibrium_is_midpoint(fixture_range_bound, config):
    r = detect(fixture_range_bound, config)[0]
    assert r.equilibrium == (r.high + r.low) / 2
    assert r.discount_zone == (r.low, r.equilibrium)
    assert r.premium_zone == (r.equilibrium, r.high)
