"""TDD test'leri — smc_engine/detectors/zone_detector.py (Plan task 1.3).

Efloud Order Block: pump/dump oncesi mum + oncesindeki pes pese ayni renkli
mumlar. Istekli breakout esigi = config.ob_breakout_threshold. Breaker block /
indecision candle. Zone.status = FRESH, Zone.age_bars = 0 (olusum aninda).
Tum zaman referanslari datetime.
"""

from __future__ import annotations

import pandas as pd
import pytest

from smc_engine.config import SMCConfig
from smc_engine.detectors.zone_detector import detect
from smc_engine.types import Zone, ZoneAnchor, ZoneKind, ZoneStatus


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


def test_returns_list_of_zones(fixture_known_ob, config):
    zones = detect(fixture_known_ob, config)
    assert isinstance(zones, list)
    assert all(isinstance(z, Zone) for z in zones)


def test_accepts_kwargs(fixture_known_ob, config):
    zones = detect(fixture_known_ob, config, some_context=1)
    assert isinstance(zones, list)


# ============================================================
# Bilinen bullish OB — fixture_known_ob
# OB mumu idx 5 (96,97,92,93 bearish), idx 6 istekli bullish breakout.
# ============================================================


def test_detects_known_bullish_ob(fixture_known_ob, config):
    zones = detect(fixture_known_ob, config)
    demand = [z for z in zones if z.kind == ZoneKind.DEMAND]
    assert len(demand) >= 1
    z = demand[0]
    # OB mumu idx 5: BODY anchor varsayilan -> top=open 96, bottom=close 93
    assert z.top == pytest.approx(96.0)
    assert z.bottom == pytest.approx(93.0)


def test_ob_origin_is_timestamp(fixture_known_ob, config):
    df = fixture_known_ob
    z = detect(df, config)[0]
    assert z.origin_candle_ts in df.index
    assert z.created_at in df.index
    assert z.origin_candle_ts == df.index[5]


def test_fresh_zone_age_zero(fixture_known_ob, config):
    zones = detect(fixture_known_ob, config)
    for z in zones:
        assert z.status == ZoneStatus.FRESH
        assert z.age_bars == 0


def test_zone_anchor_set(fixture_known_ob, config):
    z = detect(fixture_known_ob, config)[0]
    assert isinstance(z.anchor, ZoneAnchor)


# ============================================================
# Istekli breakout esigi — config.ob_breakout_threshold
# ============================================================


def test_weak_breakout_no_ob(config):
    """Breakout mumu istekli degilse (body < threshold x onceki range) -> OB yok."""
    rows = [
        _candle(110, 111, 108, 109),  # 0
        _candle(109, 110, 106, 107),  # 1
        _candle(107, 108, 104, 105),  # 2
        _candle(105, 106, 102, 103),  # 3
        _candle(103, 104, 100, 101),  # 4
        _candle(96, 97, 92, 93),      # 5  potansiyel OB mumu (range 5)
        _candle(94, 96, 93, 95),      # 6  zayif breakout (body 1 < 1.5*5)
        _candle(95, 97, 94, 96),      # 7
        _candle(96, 98, 95, 97),      # 8
        _candle(97, 99, 96, 98),      # 9
    ]
    df = _df(rows)
    zones = detect(df, config)
    assert all(z.origin_candle_ts != df.index[5] for z in zones)


def test_strong_breakout_triggers_ob(config):
    """Istekli breakout (body >= threshold x onceki range) -> OB tetiklenir."""
    rows = [
        _candle(110, 111, 108, 109),  # 0
        _candle(109, 110, 106, 107),  # 1
        _candle(107, 108, 104, 105),  # 2
        _candle(105, 106, 102, 103),  # 3
        _candle(103, 104, 100, 101),  # 4
        _candle(96, 97, 92, 93),      # 5  OB mumu (range 5)
        _candle(98, 109, 97, 108),    # 6  istekli breakout (body 10 >= 1.5*5)
        _candle(108, 114, 107, 113),  # 7
        _candle(113, 118, 112, 117),  # 8
        _candle(117, 121, 116, 120),  # 9
    ]
    df = _df(rows)
    zones = detect(df, config)
    assert any(z.origin_candle_ts == df.index[5] for z in zones)


# ============================================================
# Bearish OB — bull mumu + istekli bearish breakout
# ============================================================


def test_detects_bearish_ob(config):
    """Bullish mum + istekli dusus breakout -> SUPPLY zone."""
    rows = [
        _candle(90, 92, 89, 91),      # 0
        _candle(91, 94, 90, 93),      # 1
        _candle(93, 96, 92, 95),      # 2
        _candle(95, 98, 94, 97),      # 3
        _candle(97, 100, 96, 99),     # 4
        _candle(104, 108, 103, 107),  # 5  OB mumu (bullish, range 5)
        _candle(102, 103, 92, 93),    # 6  istekli bearish breakout (body 9 >= 1.5*5)
        _candle(93, 94, 88, 89),      # 7
        _candle(89, 90, 84, 85),      # 8
        _candle(85, 86, 80, 81),      # 9
    ]
    df = _df(rows)
    zones = detect(df, config)
    supply = [z for z in zones if z.kind == ZoneKind.SUPPLY]
    assert len(supply) >= 1
    z = supply[0]
    assert z.origin_candle_ts == df.index[5]
    # SUPPLY OB mumu idx 5 (bullish): BODY anchor -> top=close 107, bottom=open 104
    assert z.top == pytest.approx(107.0)
    assert z.bottom == pytest.approx(104.0)


# ============================================================
# Config'lenebilir anchor
# ============================================================


def test_wick_anchor_configurable(fixture_known_ob):
    """zone_anchor='WICK' -> OB mumu wick'lerini (high/low) kullanir."""
    cfg = SMCConfig()
    cfg.zone_anchor = "WICK"
    zones = detect(fixture_known_ob, cfg)
    demand = [z for z in zones if z.kind == ZoneKind.DEMAND]
    assert len(demand) >= 1
    z = demand[0]
    # OB mumu idx 5: WICK anchor -> top=high 97, bottom=low 92
    assert z.top == pytest.approx(97.0)
    assert z.bottom == pytest.approx(92.0)
    assert z.anchor == ZoneAnchor.WICK


# ============================================================
# Edge case: yetersiz veri
# ============================================================


def test_insufficient_data_empty(config):
    rows = [_candle(100, 101, 99, 100) for _ in range(3)]
    df = _df(rows)
    assert detect(df, config) == []
