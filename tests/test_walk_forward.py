"""Faz 6.3 — backtest/walk_forward.py testleri.

Kayan pencere walk-forward: her pencere bir train + test M15 dilimi; harness
her dilimde calistirilir, metrics hesaplanir. Min 3 pencere. Train/test
ayrimi look-ahead'siz (test penceresi train'den SONRA gelir, ortusmez).
Deterministik: ayni veri + ayni pencere parametreleri -> ayni sonuc.
"""

from __future__ import annotations

import pandas as pd
import pytest

from smc_engine.config import SMCConfig
from smc_engine.types import TimeFrame
from backtest.walk_forward import walk_forward


def _candle(o, h, l, c, v=1000.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def _df(rows, start, freq):
    idx = pd.date_range(start=start, periods=len(rows), freq=freq)
    return pd.DataFrame(rows, index=idx)[["open", "high", "low", "close", "volume"]]


def _dataset(n_m15=320):
    """D1+H4+H8+M15 hizali sentetik set — walk-forward icin yeterli M15 uzunlugu."""
    n_days = max(5, n_m15 // 96 + 2)
    d1_rows = []
    price = 100.0
    for i in range(n_days):
        d = 5 if i % 3 != 2 else -3
        o = price
        c = price + d
        d1_rows.append(_candle(o, max(o, c) + 1, min(o, c) - 1, c))
        price = c
    d1 = _df(d1_rows, "2026-01-01", "D")

    h4_rows = []
    for i in range(n_days * 6):
        o = 100.0 + (i % 10) * 1.5 - (i // 10)
        c = o + (1.2 if i % 2 == 0 else -1.0)
        h4_rows.append(_candle(o, max(o, c) + 0.8, min(o, c) - 0.8, c))
    h4 = _df(h4_rows, "2026-01-01", "4h")

    h8_rows = []
    for i in range(n_days * 3):
        o = 100.0 + (i % 8) * 1.2 - (i // 8)
        c = o + (1.0 if i % 2 == 0 else -0.8)
        h8_rows.append(_candle(o, max(o, c) + 0.6, min(o, c) - 0.6, c))
    h8 = _df(h8_rows, "2026-01-01", "8h")

    m15_rows = []
    for i in range(n_m15):
        o = 100.0 + i * 0.05
        m15_rows.append(_candle(o, o + 0.3, o - 0.2, o + 0.04))
    m15 = _df(m15_rows, "2026-01-01", "15min")

    return {TimeFrame.D1: d1, TimeFrame.H4: h4, TimeFrame.H8: h8, TimeFrame.M15: m15}


def test_walk_forward_min_three_windows():
    """Kayan pencere en az 3 pencere uretir."""
    cfg = SMCConfig()
    windows = walk_forward(
        _dataset(320), cfg,
        train_bars=80, test_bars=40, step_bars=40,
        m15_lookback=60,
    )
    assert len(windows) >= 3


def test_walk_forward_each_window_has_metrics():
    """Her pencere train + test metrikleri icerir."""
    cfg = SMCConfig()
    windows = walk_forward(
        _dataset(320), cfg,
        train_bars=80, test_bars=40, step_bars=40,
        m15_lookback=60,
    )
    for w in windows:
        assert "train_metrics" in w and "test_metrics" in w
        assert "trade_count" in w["train_metrics"]
        assert "trade_count" in w["test_metrics"]
        assert "sharpe" in w["test_metrics"]
        # Pencere zaman damgalari raporlanir.
        assert "train_start" in w and "train_end" in w
        assert "test_start" in w and "test_end" in w


def test_walk_forward_no_lookahead_train_before_test():
    """Her pencerede test araligi train araligindan SONRA gelir, ortusmez."""
    cfg = SMCConfig()
    windows = walk_forward(
        _dataset(320), cfg,
        train_bars=80, test_bars=40, step_bars=40,
        m15_lookback=60,
    )
    for w in windows:
        assert w["train_end"] <= w["test_start"]
        # Pencereler kayar: bir sonraki pencerenin train_start'i artar.
    starts = [w["train_start"] for w in windows]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts)  # her pencere ayri


def test_walk_forward_deterministic():
    """Ayni veri + parametre -> ozdes pencere sonuclari."""
    cfg = SMCConfig()
    ds = _dataset(320)
    w1 = walk_forward(ds, cfg, train_bars=80, test_bars=40,
                      step_bars=40, m15_lookback=60)
    w2 = walk_forward(ds, cfg, train_bars=80, test_bars=40,
                      step_bars=40, m15_lookback=60)
    assert len(w1) == len(w2)
    for a, b in zip(w1, w2):
        assert a["test_metrics"]["trade_count"] == b["test_metrics"]["trade_count"]
        assert a["test_metrics"]["sharpe"] == b["test_metrics"]["sharpe"]
        assert a["train_start"] == b["train_start"]


def test_walk_forward_too_short_raises():
    """Veri 3 pencereye yetmiyorsa ValueError."""
    cfg = SMCConfig()
    with pytest.raises(ValueError):
        walk_forward(
            _dataset(150), cfg,
            train_bars=80, test_bars=40, step_bars=40,
            m15_lookback=60,
        )
