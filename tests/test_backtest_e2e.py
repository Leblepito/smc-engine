"""Faz 6.2 — Uctan uca backtest dogrulama.

Gercek BTC OHLCV (data/btc/*.parquet) uzerinde sinirli pencere ile:
  - Determinizm: iki harness.run() cagrisi birebir ayni BacktestResult
    (trades + equity_curve + metrics).
  - Train %70 / test %30 zaman bazli bolunme (shuffle yok, look-ahead yok).
  - Look-ahead yok: split timestamp bazli; test penceresi train'den sonra.

Karar C: karlilik iddiasi YOK. Kriter = cokussuz + deterministik + look-ahead'siz.

Not (harness performans siniri): Faz 5 harness'i her M15 barinda orchestrator'i
tum M15 dilimi uzerinde calistirir (O(n^2) egilim). Bu yuzden E2E testi 13 aylik
HTF baglamini korur ama M15 replay'i SINIRLI bir pencereye (~250 bar) keser ve
``m15_lookback`` ile per-cagri maliyeti baglar. Bkz. README "Bilinen Sinirlar".
"""

from __future__ import annotations

import os

import pandas as pd
import pytest

from smc_engine.config import SMCConfig
from smc_engine.types import BacktestResult, TimeFrame
from backtest.harness import run

_BTC_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "btc")

# Sinirli ama gercek-veri pencere boyutlari (harness perf siniri — yukari bak).
_M15_WINDOW = 250
_M15_OFFSET = 6000          # ilk barlar yerine "ortadan" bir pencere
_M15_LOOKBACK = 140


def _btc_available() -> bool:
    return all(
        os.path.exists(os.path.join(_BTC_DIR, f"BTCUSDT_{tf}.parquet"))
        for tf in ("D1", "H4", "H1", "M15")
    )


pytestmark = pytest.mark.skipif(
    not _btc_available(),
    reason="data/btc/*.parquet yok — examples/run_btc.py ile uret",
)


def _load_dataset(m15_slice):
    """13 aylik HTF (D1/H4/H8) + sinirli M15 penceresi -> ohlcv_by_tf."""
    from data.fetch import load_parquet
    from data.resample import resample_ohlcv

    d1 = load_parquet(os.path.join(_BTC_DIR, "BTCUSDT_D1.parquet"))
    h4 = load_parquet(os.path.join(_BTC_DIR, "BTCUSDT_H4.parquet"))
    h1 = load_parquet(os.path.join(_BTC_DIR, "BTCUSDT_H1.parquet"))
    h8 = resample_ohlcv(h1, "H8")
    return {
        TimeFrame.D1: d1,
        TimeFrame.H4: h4,
        TimeFrame.H8: h8,
        TimeFrame.M15: m15_slice,
    }


def _m15_full():
    from data.fetch import load_parquet
    return load_parquet(os.path.join(_BTC_DIR, "BTCUSDT_M15.parquet"))


def test_e2e_runs_without_crash():
    """Gercek veri penceresi uzerinde harness cokmeden calisir, metrik basar."""
    m15 = _m15_full().iloc[_M15_OFFSET:_M15_OFFSET + _M15_WINDOW]
    ds = _load_dataset(m15)
    res = run(ds, SMCConfig(), initial_equity=10_000.0, m15_lookback=_M15_LOOKBACK)
    assert isinstance(res, BacktestResult)
    assert len(res.equity_curve) == _M15_WINDOW
    assert "trade_count" in res.metrics
    assert "sharpe" in res.metrics


def test_e2e_deterministic_identical_result():
    """Iki harness.run() cagrisi birebir ayni BacktestResult uretir."""
    m15 = _m15_full().iloc[_M15_OFFSET:_M15_OFFSET + _M15_WINDOW]
    ds = _load_dataset(m15)
    r1 = run(ds, SMCConfig(), initial_equity=10_000.0, m15_lookback=_M15_LOOKBACK)
    r2 = run(ds, SMCConfig(), initial_equity=10_000.0, m15_lookback=_M15_LOOKBACK)

    # equity_curve birebir.
    pd.testing.assert_series_equal(r1.equity_curve, r2.equity_curve)
    # trade sayisi + her trade alani birebir.
    assert len(r1.trades) == len(r2.trades)
    for t1, t2 in zip(r1.trades, r2.trades):
        assert t1.direction == t2.direction
        assert t1.entry == t2.entry
        assert t1.entry_ts == t2.entry_ts
        assert t1.exit_price == t2.exit_price
        assert t1.exit_ts == t2.exit_ts
        assert t1.exit_reason == t2.exit_reason
        assert t1.pnl == t2.pnl
        assert t1.r_multiple == t2.r_multiple
    # metrics birebir.
    assert r1.metrics == r2.metrics


def test_e2e_train_test_split_time_based():
    """Train %70 / test %30 zaman bazli bolunme — look-ahead yok.

    Split bir timestamp; train penceresi tamamen test'ten ONCE biter.
    Iki ayri harness.run() (train slice, test slice) — birbirine sizmaz.
    """
    m15 = _m15_full().iloc[_M15_OFFSET:_M15_OFFSET + _M15_WINDOW]
    split = int(len(m15) * 0.70)
    train_m15 = m15.iloc[:split]
    test_m15 = m15.iloc[split:]

    # Zaman bazli, shuffle yok: train son barı < test ilk barı.
    assert train_m15.index[-1] < test_m15.index[0]

    r_train = run(_load_dataset(train_m15), SMCConfig(),
                  initial_equity=10_000.0, m15_lookback=_M15_LOOKBACK)
    r_test = run(_load_dataset(test_m15), SMCConfig(),
                 initial_equity=10_000.0, m15_lookback=_M15_LOOKBACK)

    assert len(r_train.equity_curve) == split
    assert len(r_test.equity_curve) == len(m15) - split
    # Her iki periyot da metrik uretir (trade olsa da olmasa da).
    assert "trade_count" in r_train.metrics
    assert "trade_count" in r_test.metrics
    # Train equity curve'u test penceresine sizmaz: index ayrik.
    assert r_train.equity_curve.index.max() < r_test.equity_curve.index.min()


def test_e2e_no_lookahead_window_independence():
    """Ayni M15 penceresi, daha uzun bir M15 serisinin ilk parcasi olarak
    verildiginde de ayni sonuc — sonraki barlar sizmaz.

    harness M15'i ``at_bar``'a kadar dilimledigi icin, [off:off+W] ile
    [off:off+W+50] serisinin ilk W bari ozdes BacktestResult uretmeli.
    """
    full = _m15_full()
    short = full.iloc[_M15_OFFSET:_M15_OFFSET + _M15_WINDOW]
    longer = full.iloc[_M15_OFFSET:_M15_OFFSET + _M15_WINDOW + 50]

    r_short = run(_load_dataset(short), SMCConfig(),
                  initial_equity=10_000.0, m15_lookback=_M15_LOOKBACK)
    r_long = run(_load_dataset(longer), SMCConfig(),
                 initial_equity=10_000.0, m15_lookback=_M15_LOOKBACK)

    # r_long'un ilk W bari r_short ile ozdes (look-ahead yok).
    pd.testing.assert_series_equal(
        r_short.equity_curve,
        r_long.equity_curve.iloc[:_M15_WINDOW],
    )
