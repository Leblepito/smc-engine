"""Pytest fixture'lari -- her detektor icin sentetik OHLCV DataFrame'leri.

Gercek uretici fonksiyonlar tests/fixtures/synthetic.py icinde; burada sadece
pytest fixture sarmalayicilari. Her fixture'in beklenen ciktisi ilgili
synthetic.make_* fonksiyonunun docstring'inde belgelenmistir.
"""

from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

# tests/fixtures dizinini import path'ine ekle
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures"))

import synthetic  # noqa: E402


@pytest.fixture
def fixture_trending_bullish() -> pd.DataFrame:
    """LL -> HL -> HH yukselen yapi (structure_detector)."""
    return synthetic.make_trending_bullish()


@pytest.fixture
def fixture_range_bound() -> pd.DataFrame:
    """Bilinen RH/RL/EQ yatay range (range_detector)."""
    return synthetic.make_range_bound()


@pytest.fixture
def fixture_known_ob() -> pd.DataFrame:
    """Pump/dump + istekli breakout order block (zone_detector)."""
    return synthetic.make_known_ob()


@pytest.fixture
def fixture_known_fvg() -> pd.DataFrame:
    """3-mum FVG boslugu (imbalance_detector)."""
    return synthetic.make_known_fvg()


@pytest.fixture
def fixture_sweep() -> pd.DataFrame:
    """Equal high + sweep/reclaim (liquidity_detector)."""
    return synthetic.make_sweep()


@pytest.fixture
def fixture_levels() -> pd.DataFrame:
    """Hafta/ay acilisi bilinen tarihler, D1 (level_detector)."""
    return synthetic.make_levels()


@pytest.fixture
def fixture_multi_tf() -> dict:
    """D1 + H4 + M15 hizalanmis setler (orchestrator)."""
    return synthetic.make_multi_tf()


@pytest.fixture
def fixture_choch_bullish() -> pd.DataFrame:
    """Dusen yapi + son LH kirilimi -> bullish CHoCH (structure_detector)."""
    return synthetic.make_choch_bullish()


@pytest.fixture
def fixture_choch_bearish() -> pd.DataFrame:
    """Yukselen yapi + son HL kirilimi -> bearish CHoCH (structure_detector)."""
    return synthetic.make_choch_bearish()
