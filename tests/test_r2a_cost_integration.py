"""R2a — Ö-5 maliyet entegrasyon testi + Test bosluk #5 determinizm."""
from __future__ import annotations
from datetime import datetime, timedelta
import pandas as pd
import pytest
from smc_engine.config import SMCConfig
from smc_engine.types import (
    Bias, Direction, MarketPicture, POIKind, POIRef, Setup, TFSnapshot,
    TimeFrame, Zone, ZoneAnchor, ZoneKind, ZoneStatus, ValidatedSetup,
)
from backtest.position_manager import open_position, update as pm_update


def test_known_trade_pnl_with_costs_exact():
    """Ö-5: Bilinen tek-trade senaryosu + maliyetli config -> PnL tam beklenen.

    Senaryo: LONG entry@100, SL@95, TP1@105. Size=10. Fiyat: open=100 fill,
    sonraki bar high=106 (TP1 hit, 50% kapanir), sonraki low=98 (devam),
    sonraki low=94 (breakeven SL = 100, hit).
    
    Maliyetler: spread=0.10, slippage_pct=0.001 (=0.1%), commission_pct=0.0004.
    
    Entry fill adj: 100 + (100*0.001 + 0.10/2) = 100 + 0.15 = 100.15
    TP1 exit raw=105; adj: 105 - (105*0.001 + 0.05) = 105 - 0.155 = 104.845
    TP1 size = 10 * 0.5 = 5
    TP1 gross = (104.845 - 100.15) * 5 = 4.695 * 5 = 23.475
    Commission entry = 100.15 * 5 * 0.0004 = 0.20030
    Commission exit  = 104.845 * 5 * 0.0004 = 0.20969
    TP1 PnL = 23.475 - 0.41 = 23.065 (approx)
    
    Breakeven SL exit @ entry=100.15; adj: 100.15 - 0.05 - 100.15*0.001 =
        100.15 - 0.05 - 0.10015 = 99.99985
    BE size = 5
    BE gross = (99.99985 - 100.15) * 5 = -0.15015 * 5 = -0.75075
    Commission entry = 100.15 * 5 * 0.0004 = 0.20030
    Commission exit  = 99.99985 * 5 * 0.0004 = 0.19999970
    BE PnL = -0.75075 - 0.40029 = -1.15104
    """
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
    cfg = SMCConfig(spread=0.10, slippage_pct=0.001, commission_pct=0.0004)
    
    # Fill bar: open=100
    bar0 = pd.Series({"open": 100.0, "high": 100.1, "low": 99.9, "close": 100.05},
                     name=pd.Timestamp("2026-01-01 00:00"))
    pos = open_position(vs, fill_price=100.0, fill_ts=bar0.name, config=cfg)
    # Entry fiyati spread+slippage uygulandi:
    assert pos.entry == pytest.approx(100.15, abs=1e-6)
    
    # Bar1: TP1 hit (high=106 >= 105)
    bar1 = pd.Series({"open": 100.05, "high": 106.0, "low": 100.0, "close": 105.5},
                     name=pd.Timestamp("2026-01-01 00:15"))
    pos, tr1 = pm_update(pos, bar1, cfg)
    assert len(tr1) == 1
    tp1 = tr1[0]
    assert tp1.exit_reason == "TP1"
    # TP1 PnL hesabi
    assert tp1.pnl == pytest.approx(23.065, abs=0.01)
    
    # Bar2: breakeven SL hit (low=99 < be=100.15)
    bar2 = pd.Series({"open": 105.0, "high": 105.5, "low": 99.0, "close": 100.0},
                     name=pd.Timestamp("2026-01-01 00:30"))
    pos, tr2 = pm_update(pos, bar2, cfg)
    assert pos is None
    assert len(tr2) == 1
    assert tr2[0].exit_reason == "BREAKEVEN"
    # BE PnL hesabi (negatif - cost dominant)
    assert tr2[0].pnl == pytest.approx(-1.151, abs=0.05)
    
    # Toplam PnL maliyetler dahil
    total = tp1.pnl + tr2[0].pnl
    assert total == pytest.approx(21.91, abs=0.1)


def test_zero_cost_vs_with_cost_difference():
    """Maliyet 0 vs maliyetli config; gercek tradelerde PnL farki olur."""
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
    
    cfg_zero = SMCConfig(spread=0.0, slippage_pct=0.0, commission_pct=0.0)
    cfg_cost = SMCConfig(spread=0.10, slippage_pct=0.001, commission_pct=0.0004)
    bar0 = pd.Series({"open": 100.0, "high": 100.1, "low": 99.9, "close": 100.05},
                     name=pd.Timestamp("2026-01-01"))
    bar1 = pd.Series({"open": 100.0, "high": 106.0, "low": 100.0, "close": 105.5},
                     name=pd.Timestamp("2026-01-02"))
    p_zero = open_position(vs, 100.0, bar0.name, cfg_zero)
    p_cost = open_position(vs, 100.0, bar0.name, cfg_cost)
    _, tr_zero = pm_update(p_zero, bar1, cfg_zero)
    _, tr_cost = pm_update(p_cost, bar1, cfg_cost)
    # Maliyetli versiyon dusuk PnL (spread+slippage+commission cikti).
    assert tr_zero[0].pnl > tr_cost[0].pnl
