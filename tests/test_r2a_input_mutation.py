"""R2a — Ö-1 harness ohlcv_by_tf girdi dict mutasyon korumasi (TDD)."""
from __future__ import annotations
import pandas as pd
import pytest
from smc_engine.config import SMCConfig
from smc_engine.orchestrator import analyze
from smc_engine.types import TimeFrame


def _candle(o, h, l, c, v=1000.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def _df(rows, start, freq):
    idx = pd.date_range(start=start, periods=len(rows), freq=freq)
    return pd.DataFrame(rows, index=idx)[["open","high","low","close","volume"]]


def _dataset():
    d1_rows = [_candle(100+i, 102+i, 99+i, 101+i) for i in range(40)]
    d1 = _df(d1_rows, "2026-01-01", "D")
    h4_rows = [_candle(100+i*0.1, 100+i*0.1+1, 100+i*0.1-1, 100+i*0.1+0.5) for i in range(80)]
    h4 = _df(h4_rows, "2026-01-01", "4h")
    m15_rows = [_candle(100+i*0.05, 100+i*0.05+0.5, 100+i*0.05-0.5, 100+i*0.05+0.2) for i in range(96)]
    m15 = _df(m15_rows, "2026-01-01", "15min")
    return {TimeFrame.D1: d1, TimeFrame.H4: h4, TimeFrame.M15: m15}


def test_analyze_does_not_mutate_input_dict():
    """Ö-1: analyze() input ohlcv_by_tf dict'ini IN-PLACE degistirmemeli.

    Cagiran ayni dict'i tekrar kullanabilmeli; mutasyon harness/walk_forward
    icin sessiz hata kaynagi (latent).
    """
    cfg = SMCConfig()
    data = _dataset()
    # Snapshot: anahtar listesi + her TF DataFrame'in tipi/uzunlugu.
    before_keys = set(data.keys())
    before_lengths = {k: len(v) for k, v in data.items()}
    before_index_first = {k: v.index[0] for k, v in data.items()}
    before_index_last = {k: v.index[-1] for k, v in data.items()}
    # analyze cagirisi
    analyze(data, cfg)
    # dict anahtarlari aynı kalmali
    assert set(data.keys()) == before_keys
    # Her TF df ayni uzunluk + ayni baslangic/son timestamp
    for k in data:
        assert len(data[k]) == before_lengths[k]
        assert data[k].index[0] == before_index_first[k]
        assert data[k].index[-1] == before_index_last[k]


def test_analyze_input_dict_safe_for_repeat_calls():
    """Ö-1: Ayni dict ile arka arkaya analyze() cagirisi -> her seferinde
    AYNI sonuc (sessiz mutasyon olsa farklı olur)."""
    cfg = SMCConfig()
    data = _dataset()
    p1 = analyze(data, cfg)
    p2 = analyze(data, cfg)
    assert p1.htf_bias == p2.htf_bias
    assert p1.at_timestamp == p2.at_timestamp
    assert len(p1.active_pois) == len(p2.active_pois)



def test_analyze_does_not_mutate_dataframes_in_place():
    """Ö-1: analyze() df verilerini de mutate etmemeli (cell, index, columns)."""
    cfg = SMCConfig()
    data = _dataset()
    snapshots = {k: v.copy(deep=True) for k, v in data.items()}
    analyze(data, cfg)
    for k in data:
        # tum hucreler ayni olmali (deep copy ile compare)
        pd.testing.assert_frame_equal(data[k], snapshots[k])


def test_harness_does_not_mutate_input_dict():
    """Ö-1: harness.run() de input dict'i mutate etmemeli."""
    from backtest.harness import run
    cfg = SMCConfig()
    data = _dataset()
    before_keys = set(data.keys())
    snapshots = {k: v.copy(deep=True) for k, v in data.items()}
    run(data, cfg, initial_equity=10_000.0)
    assert set(data.keys()) == before_keys
    for k in data:
        pd.testing.assert_frame_equal(data[k], snapshots[k])
