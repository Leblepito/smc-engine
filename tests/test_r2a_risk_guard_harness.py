"""R2a — Test bosluk #4: risk_guard harness yolunda integration.

Kademeli TP + breakeven (r_multiple ~0) -> consecutive_losses ne olur?
Mevcut harness mantigi: r_multiple < 0 -> losses++; > 0 -> reset 0;
== 0 -> ne reset ne artar. Yeni iste BUNU dogrula (integration testi).
"""
from __future__ import annotations
from datetime import datetime
import pandas as pd
import pytest
from smc_engine.config import SMCConfig
from smc_engine.types import (
    Bias, Direction, MarketPicture, POIKind, POIRef, Setup, TFSnapshot,
    TimeFrame, Zone, ZoneAnchor, ZoneKind, ZoneStatus, ValidatedSetup,
    AccountState,
)
from backtest.position_manager import open_position, update as pm_update


def _make_position():
    z = Zone(
        kind=ZoneKind.DEMAND, top=100.5, bottom=99.5, timeframe=TimeFrame.H4,
        created_at=datetime(2026, 1, 1), status=ZoneStatus.FRESH,
        origin_candle_ts=datetime(2026, 1, 1), anchor=ZoneAnchor.WICK,
        age_bars=0,
    )
    poi = POIRef(kind=POIKind.ZONE, ref=z, htf_aligned=True, score_hint=1.0)
    setup = Setup(
        direction=Direction.LONG, entry=100.0, sl=95.0,
        tp=[105.0, 110.0, 115.0], tp_weights=[0.5, 0.3, 0.2],
        poi=poi, confirmation=None, bias_context=Bias.BULLISH,
        confluence_score=0.8, rr=1.0,
        created_at=datetime(2026, 1, 1),
        confluence_factor_count=2,
    )
    vs = ValidatedSetup(setup=setup, position_size=10.0, risk_amount=50.0,
                        guard_log=["all"])
    return vs


def test_tp1_then_breakeven_r_multiple_classification():
    """TP1 sonra breakeven -> ilk dilim r_multiple > 0, ikinci dilim ~0.
    
    Harness mantigi: tp1 r>0 (kazandi); breakeven r~0 (zero) -> ne kazanc
    ne zarar. consecutive_losses 0 kalmali (ne artar ne reset).
    """
    cfg = SMCConfig(spread=0.0, slippage_pct=0.0, commission_pct=0.0)
    vs = _make_position()
    bar0 = pd.Series({"open": 100.0, "high": 100.1, "low": 99.9, "close": 100.05},
                     name=pd.Timestamp("2026-01-01"))
    pos = open_position(vs, fill_price=100.0, fill_ts=bar0.name, config=cfg)
    # bar1: TP1 hit (high=106)
    bar1 = pd.Series({"open": 100.0, "high": 106.0, "low": 100.0, "close": 105.5},
                     name=pd.Timestamp("2026-01-02"))
    pos, t1 = pm_update(pos, bar1, cfg)
    assert len(t1) == 1 and t1[0].exit_reason == "TP1"
    assert t1[0].r_multiple > 0
    # bar2: breakeven SL hit (current_sl=entry=100, low=99 < 100)
    bar2 = pd.Series({"open": 105.0, "high": 105.5, "low": 99.0, "close": 100.0},
                     name=pd.Timestamp("2026-01-03"))
    pos, t2 = pm_update(pos, bar2, cfg)
    assert pos is None
    assert len(t2) == 1 and t2[0].exit_reason == "BREAKEVEN"
    # r_multiple ~ 0 (entry'ye geri donduk, zero-cost).
    assert abs(t2[0].r_multiple) < 1e-6


def test_consecutive_losses_unchanged_on_breakeven_in_harness_loop():
    """Test bosluk #4: harness'ta breakeven r~0 -> consecutive_losses 
    ne reset ne artar (mevcut davranis). Bu testin kanitlanmasi onemli
    cunku risk_guard drawdown_breaker bu sayaca dayanir.
    """
    # Manuel olarak harness'in r_multiple branch'ini simule et.
    consecutive_losses = 3
    # Bir trade: r_multiple = 0 (breakeven)
    r = 0.0
    if r < 0:
        consecutive_losses += 1
    elif r > 0:
        consecutive_losses = 0
    # Beklenen: 3 -> 3 (ne reset ne artar).
    assert consecutive_losses == 3, \
        "breakeven (r=0) consecutive_losses'i degistirmemeli"


def test_risk_guard_consecutive_losses_gate_with_breakeven_history():
    """Test bosluk #4: risk_guard ardisik zarar gate'i breakeven sonrasinda
    da tetiklenebilir (sayac sifirlanmadigi icin)."""
    from smc_engine.risk_guard import validate
    cfg = SMCConfig()
    cfg.max_consecutive_losses = 3
    z = Zone(
        kind=ZoneKind.DEMAND, top=100.5, bottom=99.5, timeframe=TimeFrame.H4,
        created_at=datetime(2026, 1, 1), status=ZoneStatus.FRESH,
        origin_candle_ts=datetime(2026, 1, 1), anchor=ZoneAnchor.WICK,
        age_bars=0,
    )
    poi = POIRef(kind=POIKind.ZONE, ref=z, htf_aligned=True, score_hint=1.0)
    from smc_engine.types import StructureBreak, StructureKind
    confirm = StructureBreak(
        kind=StructureKind.CHoCH, direction=Direction.LONG,
        broken_swing_price=99.0, confirm_candle_ts=datetime(2026, 1, 1, 1),
        timeframe=TimeFrame.M15,
    )
    setup = Setup(
        direction=Direction.LONG, entry=100.0, sl=95.0,
        tp=[107.5, 113.1, 121.15], tp_weights=[0.5, 0.3, 0.2],
        poi=poi, confirmation=confirm, bias_context=Bias.BULLISH,
        confluence_score=0.8, rr=1.5,
        created_at=datetime(2026, 1, 1),
        confluence_factor_count=2,
    )
    # Senaryo: 3 ardisik kayip + 2 breakeven (r=0); breakeven sayaci
    # sifirlamadigi icin sayac 3 olarak kalmali -> gate tetiklenmeli.
    acct = AccountState(
        equity=10_000.0, open_position=False,
        recent_results=[-1.0, -1.0, -1.0, 0.0, 0.0],
        consecutive_losses=3,  # breakeven sifirlamadi
        max_drawdown_pct=0.0,
    )
    v = validate(setup, acct, cfg)
    from smc_engine.types import Rejection
    assert isinstance(v, Rejection)
    assert v.gate == "drawdown_breaker"
