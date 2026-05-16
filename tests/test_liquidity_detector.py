"""TDD test'leri — smc_engine/detectors/liquidity_detector.py (Plan task 1.5).

old/equal high-low (equal_level_tolerance), swing high/low, deviation
(bolge ustu kapat -> kaybet), SFP (ikili dip/tepe + likidite temizligi).
significance: HIGH|LOW, reclaimed bayragi.
known_levels OPSIYONEL parametre — verilmezse sadece swing-bazli calisir.
"""

from __future__ import annotations

import pandas as pd
import pytest

from smc_engine.config import SMCConfig
from smc_engine.detectors.liquidity_detector import detect
from smc_engine.types import (
    Direction,
    LiquidityEvent,
    LiquidityKind,
    Significance,
)


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


def test_returns_list_of_events(fixture_sweep, config):
    events = detect(fixture_sweep, config)
    assert isinstance(events, list)
    assert all(isinstance(e, LiquidityEvent) for e in events)


def test_accepts_kwargs(fixture_sweep, config):
    events = detect(fixture_sweep, config, some_context=1)
    assert isinstance(events, list)


# ============================================================
# known_levels OPSIYONEL — verilmeden de calisir (Spec §5.1)
# ============================================================


def test_works_without_known_levels(fixture_sweep, config):
    """known_levels verilmezse detektor sadece swing-bazli calisir, crash yok."""
    events = detect(fixture_sweep, config)
    assert isinstance(events, list)


def test_works_without_known_levels_explicit_none(fixture_sweep, config):
    events = detect(fixture_sweep, config, known_levels=None)
    assert isinstance(events, list)


def test_known_levels_improves_detection(fixture_sweep, config):
    """known_levels verilince sweep tespiti iyilesir (>= swing-bazli sayisi)."""
    base = detect(fixture_sweep, config)
    # equal high 130 bilinen seviye olarak gecirilir
    enriched = detect(fixture_sweep, config, known_levels=[130.0])
    assert len(enriched) >= len(base)


# ============================================================
# Bilinen sweep — fixture_sweep
# equal high idx3 (130.0), idx7 (130.05); idx9 sweep (high 131, close 127)
# ============================================================


def test_detects_known_sweep(fixture_sweep, config):
    df = fixture_sweep
    events = detect(df, config)
    sweeps = [e for e in events if e.kind == LiquidityKind.SWEEP]
    assert len(sweeps) >= 1
    s = sweeps[0]
    assert s.direction == Direction.SHORT  # yukari likidite alindi
    assert s.reclaimed is False
    assert s.candle_ts == df.index[9]


def test_sweep_significance_high_for_equal_levels(fixture_sweep, config):
    """Equal high (coklu temas) -> sweep significance = HIGH."""
    events = detect(fixture_sweep, config, known_levels=[130.0])
    sweeps = [e for e in events if e.kind == LiquidityKind.SWEEP]
    assert any(s.significance == Significance.HIGH for s in sweeps)


def test_event_ts_is_timestamp(fixture_sweep, config):
    df = fixture_sweep
    events = detect(df, config)
    for e in events:
        assert e.candle_ts in df.index


# ============================================================
# Deviation — bolge ustunde kapat, sonra geri kaybet
# ============================================================


def test_detects_deviation(config):
    """Deviation: fiyat bilinen seviyenin USTUNDE kapatip sonra ALTINA doner."""
    rows = [
        _candle(100, 102, 99, 101),   # 0
        _candle(101, 103, 100, 102),  # 1
        _candle(102, 104, 101, 103),  # 2
        _candle(103, 110, 102, 108),  # 3  seviye 105 USTUNDE kapatir (close 108)
        _candle(108, 109, 103, 104),  # 4  geri ALTINA doner (close 104 < 105)
        _candle(104, 105, 100, 101),  # 5
        _candle(101, 102, 98, 99),    # 6
    ]
    df = _df(rows)
    events = detect(df, config, known_levels=[105.0])
    devs = [e for e in events if e.kind == LiquidityKind.DEVIATION]
    assert len(devs) >= 1
    d = devs[0]
    assert d.direction == Direction.SHORT  # ustte kapatip kaybetti -> short


# ============================================================
# SFP — ikili dip/tepe + likidite temizligi
# ============================================================


def test_detects_sfp(config):
    """SFP: onceki swing low'u wick ile asar (likidite temizler) ama USTUNDE kapatir."""
    rows = [
        _candle(110, 112, 108, 109),  # 0
        _candle(109, 111, 106, 107),  # 1
        _candle(107, 109, 104, 105),  # 2
        _candle(105, 107, 102, 103),  # 3
        _candle(103, 105, 100, 101),  # 4
        _candle(101, 103, 98, 99),    # 5  swing LOW (~98)
        _candle(99, 102, 97, 101),    # 6
        _candle(101, 104, 100, 103),  # 7
        _candle(103, 106, 102, 105),  # 8
        _candle(105, 108, 104, 107),  # 9
        _candle(107, 110, 106, 109),  # 10
        _candle(109, 111, 95, 108),   # 11 SFP: low 95 < swing low 98 ama close 108 USTUNDE
        _candle(108, 113, 107, 112),  # 12
        _candle(112, 116, 111, 115),  # 13
        _candle(115, 119, 114, 118),  # 14
        _candle(118, 122, 117, 121),  # 15
    ]
    df = _df(rows)
    events = detect(df, config)
    sfps = [e for e in events if e.kind == LiquidityKind.SFP]
    assert len(sfps) >= 1
    s = sfps[0]
    assert s.direction == Direction.LONG  # asagi likidite temizlendi, yukari donus
    assert s.reclaimed is True
    assert s.candle_ts == df.index[11]


# ============================================================
# Edge case: yetersiz veri
# ============================================================


def test_insufficient_data_empty(config):
    rows = [_candle(100, 101, 99, 100) for _ in range(3)]
    df = _df(rows)
    assert detect(df, config) == []
