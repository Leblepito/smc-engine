"""Fixture saglik testleri -- her fixture'in sekli ve docstring'de iddia edilen
bilinen degerleri dogrular. Detektor mantigi degil, sadece fixture butunlugu.
"""

import pandas as pd

REQUIRED_COLS = ["open", "high", "low", "close", "volume"]


def _check_ohlcv(df):
    assert isinstance(df.index, pd.DatetimeIndex)
    assert list(df.columns) == REQUIRED_COLS
    assert len(df) > 0
    # high >= low her mumda
    assert (df["high"] >= df["low"]).all()
    # high >= open/close, low <= open/close
    assert (df["high"] >= df[["open", "close"]].max(axis=1)).all()
    assert (df["low"] <= df[["open", "close"]].min(axis=1)).all()


def test_trending_bullish_shape(fixture_trending_bullish):
    df = fixture_trending_bullish
    _check_ohlcv(df)
    assert len(df) == 26
    # son kapanis ilk acilistan belirgin yuksek (yukselen yapi)
    assert df["close"].iloc[-1] > df["open"].iloc[0]


def test_range_bound_known_values(fixture_range_bound):
    df = fixture_range_bound
    _check_ohlcv(df)
    # RH ~ 120, RL ~ 80
    assert df["high"].max() == 120
    assert df["low"].min() == 80


def test_known_ob_breakout(fixture_known_ob):
    df = fixture_known_ob
    _check_ohlcv(df)
    # idx 5 OB mumu bearish (close < open)
    assert df["close"].iloc[5] < df["open"].iloc[5]
    # idx 6 istekli bullish breakout
    assert df["close"].iloc[6] > df["open"].iloc[6]
    body6 = df["close"].iloc[6] - df["open"].iloc[6]
    range5 = df["high"].iloc[5] - df["low"].iloc[5]
    assert body6 >= 1.5 * range5


def test_known_fvg_gap(fixture_known_fvg):
    df = fixture_known_fvg
    _check_ohlcv(df)
    # candle[1].high < candle[3].low -> bullish FVG boslugu
    assert df["high"].iloc[1] < df["low"].iloc[3]
    assert df["high"].iloc[1] == 103
    assert df["low"].iloc[3] == 108


def test_sweep_equal_highs(fixture_sweep):
    df = fixture_sweep
    _check_ohlcv(df)
    # idx 3 ve idx 7 equal high (~130, fark < %0.1)
    h3, h7 = df["high"].iloc[3], df["high"].iloc[7]
    assert abs(h3 - h7) / h3 < 0.001
    # idx 9 sweep: high > 130 ama close < 130
    assert df["high"].iloc[9] > 130
    assert df["close"].iloc[9] < 130


def test_levels_calendar(fixture_levels):
    df = fixture_levels
    _check_ohlcv(df)
    assert len(df) == 30
    assert str(df.index[0].date()) == "2026-01-01"
    assert df["open"].iloc[0] == 100.0


def test_multi_tf_alignment(fixture_multi_tf):
    tfs = fixture_multi_tf
    assert set(tfs) == {"D1", "H4", "M15"}
    for df in tfs.values():
        _check_ohlcv(df)
    assert len(tfs["D1"]) == 5
    assert len(tfs["H4"]) == 30
    assert len(tfs["M15"]) == 480
    # hepsi ayni anda baslar
    assert tfs["D1"].index[0] == tfs["H4"].index[0] == tfs["M15"].index[0]
