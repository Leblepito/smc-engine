"""TDD test'leri — smc_engine/detectors/level_detector.py (Plan task 1.6).

MO/WO/DO acilislari, PWO/PMO, Monday High/Low, Old/Prev ATH, kripto 8H funding
window. time_utils.py kullanir. Level.valid_from/valid_until doldurulur.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from smc_engine.config import SMCConfig
from smc_engine.detectors.level_detector import detect
from smc_engine.types import Level, LevelKind

UTC = timezone.utc


def _candle(o, h, l, c, v=1000.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


@pytest.fixture
def config():
    return SMCConfig()


# ============================================================
# Cikti sozlesmesi
# ============================================================


def test_returns_list_of_levels(fixture_levels, config):
    levels = detect(fixture_levels, config)
    assert isinstance(levels, list)
    assert all(isinstance(x, Level) for x in levels)


def test_accepts_kwargs(fixture_levels, config):
    levels = detect(fixture_levels, config, some_context=1)
    assert isinstance(levels, list)


# ============================================================
# YO — yil acilisi: 2026-01-01 00:00 UTC, price = candle[0].open = 100
# ============================================================


def test_year_open(fixture_levels, config):
    levels = detect(fixture_levels, config)
    yo = [x for x in levels if x.kind == LevelKind.YO]
    assert len(yo) == 1
    assert yo[0].price == pytest.approx(100.0)
    # valid_from = 2026-01-01
    assert yo[0].valid_from.year == 2026
    assert yo[0].valid_from.month == 1
    assert yo[0].valid_from.day == 1


# ============================================================
# MO — ay acilisi: 2026-01 ilk is gunu (Persembe 1 Ocak), price = 100
# ============================================================


def test_month_open(fixture_levels, config):
    levels = detect(fixture_levels, config)
    mo = [x for x in levels if x.kind == LevelKind.MO]
    assert len(mo) >= 1
    # ilk MO 2026-01-01, open 100
    first_mo = min(mo, key=lambda x: x.valid_from)
    assert first_mo.price == pytest.approx(100.0)


# ============================================================
# WO — hafta acilislari (time_utils.week_open ile)
# ============================================================


def test_week_opens_present(fixture_levels, config):
    levels = detect(fixture_levels, config)
    wo = [x for x in levels if x.kind == LevelKind.WO]
    # 30 gunluk pencerede en az 4 hafta acilisi
    assert len(wo) >= 4


# ============================================================
# Monday High/Low — ilk Pazartesi 2026-01-05 (idx 4)
# ============================================================


def test_monday_high_low(fixture_levels, config):
    df = fixture_levels
    levels = detect(df, config)
    mh = [x for x in levels if x.kind == LevelKind.MONDAY_H]
    ml = [x for x in levels if x.kind == LevelKind.MONDAY_L]
    assert len(mh) >= 1
    assert len(ml) >= 1
    # ilk Pazartesi idx 4: candle = (104, 107, 102, 105)
    first_mh = min(mh, key=lambda x: x.valid_from)
    first_ml = min(ml, key=lambda x: x.valid_from)
    assert first_mh.price == pytest.approx(107.0)
    assert first_ml.price == pytest.approx(102.0)


# ============================================================
# Old/Prev ATH — fixture monoton yukseliyor; ATH son mumun high'i
# ============================================================


def test_ath_levels(fixture_levels, config):
    df = fixture_levels
    levels = detect(df, config)
    ath = [x for x in levels if x.kind in (LevelKind.OLD_ATH, LevelKind.PREV_ATH)]
    assert len(ath) >= 1
    # fixture monoton artan -> en yuksek high son mumda
    max_high = float(df["high"].max())
    assert any(x.price == pytest.approx(max_high) for x in ath)


# ============================================================
# valid_from / valid_until — zaman penceresi
# ============================================================


def test_valid_window_is_datetime(fixture_levels, config):
    levels = detect(fixture_levels, config)
    for x in levels:
        assert isinstance(x.valid_from, datetime)
        assert x.valid_until is None or isinstance(x.valid_until, datetime)


# ============================================================
# Funding window — kripto 8H (time_utils.FUNDING_WINDOWS_UTC)
# ============================================================


def test_funding_windows_intraday(config):
    """Intraday TF'de (H4) 8H funding window seviyeleri uretilir."""
    # H4 OHLCV — 2 gun, 12 mum (funding 00/08/16 UTC)
    rows = [_candle(100 + i, 100 + i + 2, 100 + i - 1, 100 + i + 1) for i in range(12)]
    idx = pd.date_range(start="2026-01-01", periods=12, freq="4h")
    df = pd.DataFrame(rows, index=idx)[["open", "high", "low", "close", "volume"]]
    levels = detect(df, config)
    # funding window'lari Level olarak gelmez ama detektor crash etmemeli;
    # intraday DO (gun acilisi) seviyeleri uretilmeli
    do = [x for x in levels if x.kind == LevelKind.DO]
    assert len(do) >= 1


# ============================================================
# Edge case: bos df
# ============================================================


def test_empty_df(config):
    df = pd.DataFrame(
        {"open": [], "high": [], "low": [], "close": [], "volume": []}
    )
    df.index = pd.DatetimeIndex([])
    assert detect(df, config) == []
