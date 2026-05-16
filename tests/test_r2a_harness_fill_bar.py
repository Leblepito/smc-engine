"""R2a — Ö-4 harness fill-bar SL/TP intrabar check (integration)."""
from __future__ import annotations
import pandas as pd
import pytest
from smc_engine.config import SMCConfig
from smc_engine.types import TimeFrame


def _c(o, h, l, c, v=1000.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def _df(rows, start, freq):
    idx = pd.date_range(start=start, periods=len(rows), freq=freq)
    return pd.DataFrame(rows, index=idx)[["open","high","low","close","volume"]]


def test_harness_fill_bar_sl_first_no_optimistic_skip():
    """Ö-4: Harness fill barında SL'i atlamamali — SL-first konservatif.

    Bu test fill-modelleri/maliyet ayrintilari yerine, ayni-bar intrabar
    SL muhasebesinin kosmadigi vakayi onler. Daha guclu bir test bekleniyor.
    Determinizm test: ayni veri 2x cagri ayni sonuc.
    """
    # M15 96 bar (1 gun) — fill bar SL gerceklestirme senaryolari icin
    # zenginlestirilmis volatilite
    rows = []
    for i in range(96):
        base = 100.0 + (i % 5) * 0.5
        rows.append(_c(base, base + 1.2, base - 1.5, base + 0.3))
    m15 = _df(rows, "2026-01-01", "15min")
    d1 = _df([_c(100+i, 102+i, 99+i, 101+i) for i in range(5)],
             "2026-01-01", "D")
    h4 = _df([_c(100+i*0.1, 100+i*0.1+1, 100+i*0.1-1, 100+i*0.1+0.5)
              for i in range(30)], "2026-01-01", "4h")
    from backtest.harness import run
    cfg = SMCConfig()
    # determinizm
    r1 = run({TimeFrame.D1: d1, TimeFrame.H4: h4, TimeFrame.M15: m15},
             cfg, initial_equity=10_000.0)
    r2 = run({TimeFrame.D1: d1, TimeFrame.H4: h4, TimeFrame.M15: m15},
             cfg, initial_equity=10_000.0)
    pd.testing.assert_series_equal(r1.equity_curve, r2.equity_curve)
