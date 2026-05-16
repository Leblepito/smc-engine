"""R2a — Ö-8 ATR tutarlilik testi (TDD)."""
from __future__ import annotations
import pandas as pd
import pytest
from smc_engine.detectors._atr import atr, atr_series, true_range


def _df(rows, start="2026-01-01", freq="h"):
    idx = pd.date_range(start=start, periods=len(rows), freq=freq)
    return pd.DataFrame(rows, index=idx)[["open", "high", "low", "close", "volume"]]


def _c(o, h, l, c, v=1000.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def test_atr_and_atr_series_consistent_at_last_bar():
    """Ö-8: atr(df) ile atr_series(df).iloc[-1] ayni deger olmali.

    Onceki davranis: atr() ilk bari atliyordu (tr_eff = tr.iloc[1:]),
    atr_series() atlamiyordu -> ayni df icin farkli sonuc. Tek kaynak: atr()
    artik atr_series() uzerinden hesaplanmali.
    """
    rows = [_c(100 + i, 100 + i + 2, 100 + i - 1, 100 + i + 1) for i in range(30)]
    df = _df(rows)
    period = 14
    expected = float(atr_series(df, period).iloc[-1])
    got = atr(df, period)
    assert got == pytest.approx(expected), \
        f"atr() {got} != atr_series()[-1] {expected}"


def test_atr_consistent_for_short_data():
    """Kisa veri (period > len) -> atr ve atr_series son deger tutarli."""
    rows = [_c(100 + i, 100 + i + 2, 100 + i - 1, 100 + i + 1) for i in range(5)]
    df = _df(rows)
    expected = float(atr_series(df, 14).iloc[-1])
    got = atr(df, 14)
    assert got == pytest.approx(expected)


def test_atr_two_bars():
    """2 bar -> hesap mumkun ama tek TR."""
    rows = [_c(100, 102, 99, 101), _c(101, 105, 100, 104)]
    df = _df(rows)
    # atr_series son degeri rolling(min_periods=1) ortalamasi.
    expected = float(atr_series(df, 14).iloc[-1])
    got = atr(df, 14)
    assert got == pytest.approx(expected)
