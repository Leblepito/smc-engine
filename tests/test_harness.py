"""Faz 5.2 + 5.3 — backtest/harness.py testleri.

Bar-replay dongusu (spec §8), look-ahead yok (t kapanisinda setup -> t+1 fill),
tek pozisyon kurali, account_state guncellemesi, HTF cache, fill modelleri
(next_open / limit_retest).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from smc_engine.config import SMCConfig
from smc_engine.types import BacktestResult, TimeFrame
from backtest.harness import run


# ---- sentetik veri ----------------------------------------------------

def _candle(o, h, l, c, v=1000.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def _df(rows, start, freq):
    idx = pd.date_range(start=start, periods=len(rows), freq=freq)
    return pd.DataFrame(rows, index=idx)[["open", "high", "low", "close", "volume"]]


def _dataset():
    """Smoke benzeri minimal D1+H4+M15 set."""
    d1_pattern = [6, 6, 6, -4, -4, 7, 7, 7, 7, -3, -3, 8, 8, 8, 8]
    d1_rows = []
    price = 100.0
    for d in d1_pattern:
        o = price
        c = price + d
        d1_rows.append(_candle(o, max(o, c) + 1, min(o, c) - 1, c))
        price = c
    d1 = _df(d1_rows, "2026-01-01", "D")

    h4_rows = []
    base = 100.0
    for i in range(60):
        o = base + (i % 10) * 1.5 - (i // 10)
        c = o + (1.2 if i % 2 == 0 else -1.0)
        h4_rows.append(_candle(o, max(o, c) + 0.8, min(o, c) - 0.8, c))
    h4 = _df(h4_rows, "2026-01-01", "4h")

    m15_rows = []
    for i in range(480):
        o = 100.0 + i * 0.1
        m15_rows.append(_candle(o, o + 0.3, o - 0.2, o + 0.08))
    m15 = _df(m15_rows, "2026-01-01", "15min")

    return {TimeFrame.D1: d1, TimeFrame.H4: h4, TimeFrame.M15: m15}


# ---- temel kontrat ----------------------------------------------------

def test_run_returns_backtest_result():
    cfg = SMCConfig()
    res = run(_dataset(), cfg, initial_equity=10_000.0)
    assert isinstance(res, BacktestResult)
    assert isinstance(res.trades, list)
    assert isinstance(res.equity_curve, pd.Series)
    assert isinstance(res.metrics, dict)


def test_equity_curve_indexed_by_m15():
    cfg = SMCConfig()
    res = run(_dataset(), cfg, initial_equity=10_000.0)
    m15 = _dataset()[TimeFrame.M15]
    # equity curve bar-basina mark-to-market — M15 timestamp index'i.
    assert len(res.equity_curve) > 0
    assert res.equity_curve.index.isin(m15.index).all()


def test_determinism_same_input_same_result():
    cfg = SMCConfig()  # next_open varsayilan -> deterministik
    r1 = run(_dataset(), cfg, initial_equity=10_000.0)
    r2 = run(_dataset(), cfg, initial_equity=10_000.0)
    assert len(r1.trades) == len(r2.trades)
    pd.testing.assert_series_equal(r1.equity_curve, r2.equity_curve)
    for ta, tb in zip(r1.trades, r2.trades):
        assert ta.entry == tb.entry
        assert ta.exit_price == tb.exit_price
        assert ta.pnl == tb.pnl


def test_no_lookahead_setup_fills_next_bar():
    """Bir setup uretildiginde fill, uretildigi bardan SONRAKI barda olur."""
    cfg = SMCConfig()
    res = run(_dataset(), cfg, initial_equity=10_000.0)
    # Eger trade varsa: entry_ts daima ilk M15 barindan sonra olmali
    # (ilk barda setup uretilse bile fill t+1).
    m15 = _dataset()[TimeFrame.M15]
    first_ts = m15.index[0]
    for t in res.trades:
        assert pd.Timestamp(t.entry_ts) > first_ts


def test_single_position_rule():
    """Ayni anda en fazla bir acik pozisyon — trade entry/exit araliklari
    cakismaz."""
    cfg = SMCConfig()
    res = run(_dataset(), cfg, initial_equity=10_000.0)
    # trade'leri entry_ts'e gore sirala; her trade'in entry'si bir oncekinin
    # exit'inden sonra (veya ayni — kademeli TP ayni pozisyon).
    by_entry = sorted(res.trades, key=lambda t: t.entry_ts)
    # Ayni pozisyona ait dilimler ayni entry_ts'i paylasir; farkli
    # pozisyonlar arasinda overlap olmamali.
    positions = {}
    for t in by_entry:
        positions.setdefault(t.entry_ts, []).append(t)
    keys = sorted(positions.keys())
    for i in range(len(keys) - 1):
        cur_exits = [t.exit_ts for t in positions[keys[i]]]
        next_entry = keys[i + 1]
        assert max(cur_exits) <= next_entry


def test_htf_cache_used_and_deterministic():
    """HTF cache verildiginde sonuc cache'siz ile ozdes (sadece hizlandirma)."""
    cfg = SMCConfig()
    r_nocache = run(_dataset(), cfg, initial_equity=10_000.0, use_cache=False)
    r_cache = run(_dataset(), cfg, initial_equity=10_000.0, use_cache=True)
    assert len(r_nocache.trades) == len(r_cache.trades)
    pd.testing.assert_series_equal(r_nocache.equity_curve, r_cache.equity_curve)


def test_account_state_equity_tracks_curve():
    cfg = SMCConfig()
    res = run(_dataset(), cfg, initial_equity=10_000.0)
    # equity curve ilk degeri initial_equity civari (ilk barda pozisyon yok).
    assert res.equity_curve.iloc[0] == pytest.approx(10_000.0, abs=1.0)


def test_metrics_dict_populated():
    cfg = SMCConfig()
    res = run(_dataset(), cfg, initial_equity=10_000.0)
    # metrics harness sonunda doldurulur (metrics.py delegasyonu).
    assert "trade_count" in res.metrics


# ---- fill modelleri ---------------------------------------------------

def test_fill_models_next_open_default():
    cfg = SMCConfig()
    assert cfg.fill_model == "next_open"
    res = run(_dataset(), cfg, initial_equity=10_000.0)
    assert isinstance(res, BacktestResult)


def test_fill_models_limit_retest_runs():
    cfg = SMCConfig(fill_model="limit_retest", limit_retest_bars=5)
    res = run(_dataset(), cfg, initial_equity=10_000.0)
    assert isinstance(res, BacktestResult)
    # limit_retest path-dependent ama crash etmemeli; her trade entry_ts
    # gecerli M15 timestamp olmali.
    m15 = _dataset()[TimeFrame.M15]
    for t in res.trades:
        assert pd.Timestamp(t.entry_ts) in m15.index


def test_fill_models_produce_valid_results():
    """next_open ve limit_retest ikisi de gecerli BacktestResult uretir."""
    for fm in ("next_open", "limit_retest"):
        cfg = SMCConfig(fill_model=fm)
        res = run(_dataset(), cfg, initial_equity=10_000.0)
        assert isinstance(res, BacktestResult)
        assert len(res.equity_curve) > 0
