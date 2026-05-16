"""Faz 5.4 — backtest/metrics.py testleri.

Bilinen trade listesinden: Sharpe (yillik), Sortino, win rate, profit factor,
max DD (% + sure), R-multiple dagilimi, expectancy, trade sayisi, ort. tutma
suresi, confluence-score kovasi performansi. <30 trade -> uyari bayragi.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from smc_engine.config import SMCConfig
from smc_engine.types import Direction, Trade
from backtest.metrics import compute


def _trade(r_multiple, pnl=None, entry_ts=None, exit_ts=None,
           direction=Direction.LONG, score=0.6, fcount=3, size=1.0,
           entry=100.0, exit_price=None):
    entry_ts = entry_ts or datetime(2026, 1, 1)
    exit_ts = exit_ts or (entry_ts + timedelta(hours=4))
    if pnl is None:
        pnl = r_multiple * 100.0
    if exit_price is None:
        exit_price = entry + r_multiple
    return Trade(
        direction=direction, entry=entry, entry_ts=entry_ts,
        exit_price=exit_price, exit_ts=exit_ts,
        exit_reason="TP1" if r_multiple > 0 else "SL",
        pnl=pnl, r_multiple=r_multiple, size=size,
        confluence_score=score, confluence_factor_count=fcount,
    )


def _equity_curve(values, start="2026-01-01", freq="15min"):
    idx = pd.date_range(start=start, periods=len(values), freq=freq)
    return pd.Series(values, index=idx)


# ---- temel metrikler --------------------------------------------------

def test_empty_trades_no_crash():
    cfg = SMCConfig()
    ec = _equity_curve([10_000.0] * 10)
    m = compute([], ec, cfg)
    assert m["trade_count"] == 0
    assert m["low_trade_count_warning"] is True


def test_trade_count_and_win_rate():
    cfg = SMCConfig()
    trades = [_trade(1.0), _trade(2.0), _trade(-1.0), _trade(-1.0)]
    ec = _equity_curve([10_000.0, 10_100.0, 10_300.0, 10_200.0, 10_100.0])
    m = compute(trades, ec, cfg)
    assert m["trade_count"] == 4
    assert m["win_rate"] == pytest.approx(0.5)


def test_profit_factor():
    cfg = SMCConfig()
    # brut kar = 100+200=300; brut zarar = 100 -> PF = 3.0
    trades = [_trade(1.0, pnl=100.0), _trade(2.0, pnl=200.0),
              _trade(-1.0, pnl=-100.0)]
    ec = _equity_curve([10_000.0] * 4)
    m = compute(trades, ec, cfg)
    assert m["profit_factor"] == pytest.approx(3.0)


def test_expectancy_formula():
    cfg = SMCConfig()
    # 2 kazanan ort R = 1.5 ; 2 kaybeden ort R = -1.0
    # expectancy = 0.5*1.5 - 0.5*1.0 = 0.25
    trades = [_trade(1.0), _trade(2.0), _trade(-1.0), _trade(-1.0)]
    ec = _equity_curve([10_000.0] * 5)
    m = compute(trades, ec, cfg)
    assert m["expectancy"] == pytest.approx(0.25)


def test_max_drawdown_pct_and_duration():
    cfg = SMCConfig()
    # equity: 100 -> 120 (peak) -> 90 (DD) -> 130 (recover)
    ec = _equity_curve([100.0, 120.0, 110.0, 90.0, 100.0, 130.0])
    trades = [_trade(1.0)]
    m = compute(trades, ec, cfg)
    # max DD = (120-90)/120 = 0.25
    assert m["max_drawdown_pct"] == pytest.approx(0.25)
    # sure: peak'ten recovery'e kadar bar sayisi > 0
    assert m["max_drawdown_duration"] >= 1


def test_sharpe_and_sortino_present():
    cfg = SMCConfig()
    np.random.seed(0)
    vals = list(np.cumsum(np.random.randn(200)) + 10_000.0)
    ec = _equity_curve(vals)
    trades = [_trade(0.5) for _ in range(40)]
    m = compute(trades, ec, cfg)
    assert "sharpe" in m and "sortino" in m
    assert isinstance(m["sharpe"], float)
    assert isinstance(m["sortino"], float)


def test_low_trade_count_warning_threshold():
    cfg = SMCConfig()
    ec = _equity_curve([10_000.0] * 50)
    # 29 trade -> uyari
    m29 = compute([_trade(0.5) for _ in range(29)], ec, cfg)
    assert m29["low_trade_count_warning"] is True
    # 30 trade -> uyari yok
    m30 = compute([_trade(0.5) for _ in range(30)], ec, cfg)
    assert m30["low_trade_count_warning"] is False


def test_r_multiple_distribution():
    cfg = SMCConfig()
    trades = [_trade(1.0), _trade(2.0), _trade(-1.0)]
    ec = _equity_curve([10_000.0] * 4)
    m = compute(trades, ec, cfg)
    dist = m["r_multiple_distribution"]
    assert dist["mean"] == pytest.approx((1.0 + 2.0 - 1.0) / 3.0)
    assert dist["min"] == pytest.approx(-1.0)
    assert dist["max"] == pytest.approx(2.0)


def test_avg_holding_period():
    cfg = SMCConfig()
    t0 = datetime(2026, 1, 1)
    trades = [
        _trade(1.0, entry_ts=t0, exit_ts=t0 + timedelta(hours=2)),
        _trade(-1.0, entry_ts=t0, exit_ts=t0 + timedelta(hours=4)),
    ]
    ec = _equity_curve([10_000.0] * 3)
    m = compute(trades, ec, cfg)
    # ort tutma suresi = 3 saat
    assert m["avg_holding_hours"] == pytest.approx(3.0)


def test_confluence_bucket_performance():
    cfg = SMCConfig()
    # dusuk skor kovasi kaybediyor, yuksek skor kovasi kazaniyor
    trades = [
        _trade(-1.0, score=0.45),
        _trade(-1.0, score=0.50),
        _trade(2.0, score=0.85),
        _trade(2.0, score=0.90),
    ]
    ec = _equity_curve([10_000.0] * 5)
    m = compute(trades, ec, cfg)
    buckets = m["confluence_buckets"]
    assert isinstance(buckets, dict)
    assert len(buckets) >= 1
    # her kovada trade sayisi + ort R bilgisi olmali
    for b, stats in buckets.items():
        assert "count" in stats and "avg_r" in stats


# ---- bootstrap Sharpe CI (Faz 6.4) ------------------------------------

def test_bootstrap_ci_deterministic():
    """Ayni trade listesi + ayni seed -> ozdes CI (numpy default_rng)."""
    cfg = SMCConfig()
    from backtest.metrics import bootstrap_sharpe_ci
    # Pozitif beklentili bilinen R-multiple listesi.
    trades = [_trade(r) for r in ([2.0] * 40 + [-1.0] * 20)]
    lo1, hi1 = bootstrap_sharpe_ci(trades, n_samples=500, ci=0.95, seed=42)
    lo2, hi2 = bootstrap_sharpe_ci(trades, n_samples=500, ci=0.95, seed=42)
    assert lo1 == lo2
    assert hi1 == hi2


def test_bootstrap_ci_ordering_and_bounds():
    """Alt sinir < ust sinir; pozitif beklentili listede alt sinir hesaplanir."""
    cfg = SMCConfig()
    from backtest.metrics import bootstrap_sharpe_ci
    trades = [_trade(r) for r in ([2.0] * 50 + [-1.0] * 10)]  # cok pozitif
    lo, hi = bootstrap_sharpe_ci(trades, n_samples=1000, ci=0.95, seed=7)
    assert lo < hi
    # Bu kadar pozitif bir listede %95 CI alt siniri pozitif olmali.
    assert lo > 0.0
    assert isinstance(lo, float) and isinstance(hi, float)


def test_bootstrap_ci_default_seed_deterministic():
    """seed verilmese de varsayilan sabit -> deterministik."""
    from backtest.metrics import bootstrap_sharpe_ci
    trades = [_trade(r) for r in ([1.0] * 30 + [-1.0] * 15)]
    a = bootstrap_sharpe_ci(trades, n_samples=300)
    b = bootstrap_sharpe_ci(trades, n_samples=300)
    assert a == b


def test_bootstrap_ci_insufficient_trades():
    """<2 trade -> (0.0, 0.0) — crash etmez."""
    from backtest.metrics import bootstrap_sharpe_ci
    assert bootstrap_sharpe_ci([], n_samples=100) == (0.0, 0.0)
    assert bootstrap_sharpe_ci([_trade(1.0)], n_samples=100) == (0.0, 0.0)


def test_bootstrap_ci_negative_expectancy_lower_bound():
    """Negatif beklentili listede %95 CI alt siniri <= 0 olmali (gate sinyali)."""
    from backtest.metrics import bootstrap_sharpe_ci
    trades = [_trade(r) for r in ([1.0] * 10 + [-1.0] * 50)]  # cok negatif
    lo, hi = bootstrap_sharpe_ci(trades, n_samples=1000, ci=0.95, seed=3)
    assert lo <= 0.0


# ---- KR-3: bootstrap CI rename + alias (trade-bazli Sharpe) ----------

def test_bootstrap_trade_sharpe_ci_new_name_exists():
    """Yeni isim ``bootstrap_trade_sharpe_ci`` modul attr olarak mevcut."""
    from backtest import metrics as m
    assert hasattr(m, "bootstrap_trade_sharpe_ci"), (
        "KR-3: bootstrap_trade_sharpe_ci adi backtest.metrics'te eksik"
    )


def test_bootstrap_trade_sharpe_ci_matches_alias():
    """``bootstrap_sharpe_ci`` (eski alias) ile ``bootstrap_trade_sharpe_ci``
    ayni seed/inputs icin ozdes CI dondurmeli (geriye uyumluluk)."""
    from backtest.metrics import bootstrap_trade_sharpe_ci, bootstrap_sharpe_ci
    trades = [_trade(r) for r in ([1.5] * 30 + [-1.0] * 20)]
    lo_new, hi_new = bootstrap_trade_sharpe_ci(
        trades, n_samples=400, ci=0.95, seed=11
    )
    lo_old, hi_old = bootstrap_sharpe_ci(
        trades, n_samples=400, ci=0.95, seed=11
    )
    assert (lo_new, hi_new) == (lo_old, hi_old)


def test_bootstrap_trade_sharpe_ci_docstring_clarifies_metric():
    """Docstring trade-bazli ile equity-curve Sharpe ayrimini belirtmeli."""
    from backtest.metrics import bootstrap_trade_sharpe_ci
    doc = (bootstrap_trade_sharpe_ci.__doc__ or "").lower()
    # Trade-bazli olduguna ve equity-curve Sharpe ile ayni olmadigina dair
    # uyari/aciklama bekliyoruz.
    assert "trade" in doc and ("equity" in doc or "ayri" in doc or "fark" in doc), (
        "KR-3: docstring trade-bazli/equity-curve Sharpe ayrimini netlestirmeli"
    )
