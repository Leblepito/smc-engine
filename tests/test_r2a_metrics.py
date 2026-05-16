"""R2a — Ö-9 / Ö-10 metrics tests (TDD)."""
from __future__ import annotations
from datetime import datetime, timedelta
import pandas as pd
import pytest
from smc_engine.config import SMCConfig
from smc_engine.types import Direction, Trade
from backtest.metrics import compute, _max_drawdown


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


# ============================================================
# Ö-9 — profit_factor inf yerine tavan + raporda string aciklama
# ============================================================

def test_profit_factor_zero_loss_returns_finite_cap():
    """Hic kaybi olmayan trade'lerde profit_factor inf yerine 999.0 (tavan)."""
    cfg = SMCConfig()
    # tum trade'ler kazanan -> gross_loss = 0; pf = inf yerine tavan
    trades = [_trade(1.0, pnl=100.0), _trade(2.0, pnl=200.0)]
    ec = _equity_curve([10_000.0, 10_100.0, 10_300.0])
    m = compute(trades, ec, cfg)
    pf = m["profit_factor"]
    import math
    assert math.isfinite(pf), f"profit_factor sonsuz olmamali (alindi: {pf})"
    assert pf == pytest.approx(999.0)


def test_profit_factor_zero_loss_label_in_report():
    """report.summary profit_factor=999.0 (tavan) icin 'tanimsiz' aciklamasi."""
    from backtest.report import summary
    from smc_engine.types import BacktestResult
    cfg = SMCConfig()
    trades = [_trade(1.0, pnl=100.0)]
    ec = _equity_curve([10_000.0, 10_100.0])
    m = compute(trades, ec, cfg)
    res = BacktestResult(trades=trades, equity_curve=ec, metrics=m)
    out = summary(res, cfg)
    # Tavan deger '999' icermeli VEYA 'tanimsiz' / 'zarar yok' aciklamasi.
    assert "999" in out or "tanimsiz" in out.lower() or "zarar yok" in out.lower()


# ============================================================
# Ö-10 — max_drawdown_duration = peak -> recovery; equity<=0 ayri
# ============================================================

def test_max_drawdown_duration_is_peak_to_recovery():
    """DD suresi peak->recovery (peak'e geri donen) bar sayisi olmali."""
    cfg = SMCConfig()
    # equity: 100 -> 120 (peak @1) -> 90 (trough @3) -> 130 (>120 recover @5)
    ec = _equity_curve([100.0, 120.0, 110.0, 90.0, 100.0, 130.0])
    trades = [_trade(1.0)]
    m = compute(trades, ec, cfg)
    # peak idx=1, recovery idx=5 -> duration = 4
    assert m["max_drawdown_duration"] == 4


def test_max_drawdown_duration_no_recovery_marks_open():
    """DD recover etmediyse duration = peak'ten son bara kadar (acik DD)."""
    cfg = SMCConfig()
    # 100 -> 120 (peak @1) -> 90 (trough @3) -> 100 (final, peak'e gelmedi)
    ec = _equity_curve([100.0, 120.0, 110.0, 90.0, 100.0])
    trades = [_trade(1.0)]
    m = compute(trades, ec, cfg)
    # peak idx=1, son bar idx=4 -> duration=3 (peak->trough->...->son)
    assert m["max_drawdown_duration"] == 3


def test_max_drawdown_negative_equity_handled():
    """Equity <=0 oluyorsa DD hesabi mantikli (0 yerine, en azindan
    DD raporlanmali veya 0 ve ayri bayrak)."""
    cfg = SMCConfig()
    # 100 -> 80 -> 0 -> -10 (catastrophic)
    ec = _equity_curve([100.0, 80.0, 0.0, -10.0])
    trades = [_trade(-1.0)]
    m = compute(trades, ec, cfg)
    # eski kod: equity<=0 -> DD=0; bu yaniltici. Yeni: ya DD>0 ya da
    # explicit "ruined" bayragi.
    # Test: ya max_dd>0 ya da equity_ruined bayragi True olmali.
    assert m["max_drawdown_pct"] > 0 or m.get("equity_ruined") is True
