"""data/resample.py testleri -- mum sayisi + OHLC kurallari."""

import pandas as pd
import pytest

from data.resample import resample_ohlcv


def _h1_df(n_hours):
    """n_hours saatlik H1 OHLCV -- her mum deterministik."""
    idx = pd.date_range("2026-01-01", periods=n_hours, freq="h")
    rows = []
    for i in range(n_hours):
        o = 100.0 + i
        h = o + 5
        l = o - 3
        c = o + 1
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 10.0})
    return pd.DataFrame(rows, index=idx)


def test_h1_to_h8_candle_count():
    # 24 saat H1 -> 3 adet H8 mum
    df = _h1_df(24)
    out = resample_ohlcv(df, "H8")
    assert len(out) == 3
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]


def test_h1_to_h4_candle_count():
    # 24 saat H1 -> 6 adet H4 mum
    df = _h1_df(24)
    out = resample_ohlcv(df, "H4")
    assert len(out) == 6


def test_ohlc_aggregation_rules():
    # ilk 8 saatlik H1 dilimi -> 1 H8 mum
    df = _h1_df(8)
    out = resample_ohlcv(df, "H8")
    assert len(out) == 1
    bar = out.iloc[0]
    # open = ilk H1 mumun open'i = 100.0
    assert bar["open"] == df["open"].iloc[0] == 100.0
    # close = son H1 mumun close'u = (100+7)+1 = 108.0
    assert bar["close"] == df["close"].iloc[-1] == 108.0
    # high = tum dilimin max'i
    assert bar["high"] == df["high"].max()
    # low = tum dilimin min'i
    assert bar["low"] == df["low"].min()
    # volume = toplam
    assert bar["volume"] == df["volume"].sum() == 80.0


def test_resample_preserves_datetimeindex():
    df = _h1_df(16)
    out = resample_ohlcv(df, "H8")
    assert isinstance(out.index, pd.DatetimeIndex)
    # H8 mumlari 8 saatlik aralikli
    assert (out.index[1] - out.index[0]) == pd.Timedelta(hours=8)


def test_resample_partial_last_bucket():
    # 20 saat -> H8: 8 + 8 + 4 -> 3 mum (son mum kismi ama dusulmez)
    df = _h1_df(20)
    out = resample_ohlcv(df, "H8")
    assert len(out) == 3
    # son mumun close'u son H1 close'u
    assert out["close"].iloc[-1] == df["close"].iloc[-1]


def test_resample_invalid_target_raises():
    df = _h1_df(8)
    with pytest.raises(ValueError):
        resample_ohlcv(df, "H7")


def test_resample_missing_column_raises():
    df = _h1_df(8).drop(columns=["volume"])
    with pytest.raises(ValueError):
        resample_ohlcv(df, "H8")


def test_resample_non_datetime_index_raises():
    df = _h1_df(8).reset_index(drop=True)
    with pytest.raises(TypeError):
        resample_ohlcv(df, "H8")
