"""TDD test'leri — smc_engine/detectors/imbalance_detector.py (Plan task 1.4).

FVG (3-mum boslugu) / liquidity void / inefficiency. filled=False,
fill_ratio=0.0 olusum aninda. Min boslugu filtresi = config.fvg_min_gap_atr
(ATR dahili hesaplanir). direction (bullish/bearish FVG).
"""

from __future__ import annotations

import pandas as pd
import pytest

from smc_engine.config import SMCConfig
from smc_engine.detectors.imbalance_detector import detect
from smc_engine.types import Direction, Imbalance, ImbalanceKind


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


def test_returns_list_of_imbalances(fixture_known_fvg, config):
    imbs = detect(fixture_known_fvg, config)
    assert isinstance(imbs, list)
    assert all(isinstance(i, Imbalance) for i in imbs)


def test_accepts_kwargs(fixture_known_fvg, config):
    imbs = detect(fixture_known_fvg, config, some_context=1)
    assert isinstance(imbs, list)


# ============================================================
# Bilinen bullish FVG — fixture_known_fvg
# idx 1-2-3: candle[1].high=103, candle[3].low=108 -> bosluk (103, 108)
# ============================================================


def test_detects_known_bullish_fvg(fixture_known_fvg, config):
    imbs = detect(fixture_known_fvg, config)
    fvgs = [i for i in imbs if i.kind == ImbalanceKind.FVG]
    assert len(fvgs) >= 1
    # bullish FVG: bottom = candle[1].high = 103, top = candle[3].low = 108
    f = [x for x in fvgs if x.direction == Direction.LONG][0]
    assert f.bottom == pytest.approx(103.0)
    assert f.top == pytest.approx(108.0)


def test_fvg_unfilled_at_creation(fixture_known_fvg, config):
    imbs = detect(fixture_known_fvg, config)
    for i in imbs:
        assert i.filled is False
        assert i.fill_ratio == 0.0


def test_fvg_created_at_is_timestamp(fixture_known_fvg, config):
    df = fixture_known_fvg
    f = detect(df, config)[0]
    assert f.created_at in df.index
    # FVG orta mum idx 2'de teyit edilir (3-mum dizisi 1-2-3 kapanir)
    assert f.created_at == df.index[2]


# ============================================================
# Bearish FVG
# ============================================================


def test_detects_bearish_fvg(config):
    """Dusus FVG: candle[1].low ile candle[3].high arasinda bosluk."""
    rows = [
        _candle(120, 121, 116, 117),  # 0  low 116 (<= candle[2].high; (0,1,2) FVG yok)
        _candle(119, 120, 117, 118),  # 1  FVG ust sinir: low 117
        _candle(115, 116, 108, 109),  # 2  orta mum (buyuk dusus)
        _candle(109, 112, 107, 110),  # 3  FVG alt sinir: high 112
        _candle(110, 111, 106, 107),  # 4
        _candle(107, 108, 103, 104),  # 5
    ]
    df = _df(rows)
    imbs = detect(df, config)
    bear = [i for i in imbs if i.direction == Direction.SHORT]
    assert len(bear) >= 1
    f = bear[0]
    # bearish FVG: top = candle[1].low = 117, bottom = candle[3].high = 112
    assert f.top == pytest.approx(117.0)
    assert f.bottom == pytest.approx(112.0)


# ============================================================
# Min boslugu filtresi — fvg_min_gap_atr
# ============================================================


def test_tiny_gap_filtered_out(config):
    """Cok kucuk FVG (< fvg_min_gap_atr x ATR) gurultu -> elenir."""
    # tum mumlar dar; uclu dizide minik bir bosluk olusur ama esigi gecmez
    rows = [
        _candle(100.0, 100.5, 99.5, 100.0),   # 0
        _candle(100.0, 100.6, 99.6, 100.1),   # 1  high 100.6
        _candle(100.2, 101.0, 100.1, 100.8),  # 2  orta
        _candle(100.8, 101.3, 100.65, 101.0), # 3  low 100.65 -> bosluk 0.05
        _candle(101.0, 101.5, 100.7, 101.2),  # 4
        _candle(101.2, 101.7, 100.9, 101.4),  # 5
    ]
    df = _df(rows)
    imbs = detect(df, config)
    # 0.05'lik bosluk ATR (~1.0) x 0.3 = 0.3 esiginin altinda -> FVG yok
    fvgs = [i for i in imbs if i.kind == ImbalanceKind.FVG]
    assert fvgs == []


def test_large_gap_kept(fixture_known_fvg, config):
    """Genis bosluk (>= fvg_min_gap_atr x ATR) -> tutulur."""
    imbs = detect(fixture_known_fvg, config)
    assert len(imbs) >= 1


# ============================================================
# Edge case: yetersiz veri
# ============================================================


def test_insufficient_data_empty(config):
    rows = [_candle(100, 101, 99, 100) for _ in range(2)]
    df = _df(rows)
    assert detect(df, config) == []
