"""R2a — Ö-4 fill-bar SL/TP intrabar kontrolu (TDD)."""
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


def test_position_manager_intrabar_sl_first_on_fill_bar():
    """Bir bar fill + SL'i ayni anda gerceklestirebilir; SL ONCE kuralı.

    Senaryo: LONG entry @100, SL @95. Bar: open=100, low=94, high=102, close=101.
    Fill open=100 olur; ayni barda low=94 SL'i tetikler -> SL'de kapanmali.
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
    cfg = SMCConfig()
    # Fill bar (intrabar SL hit): open=100, low=94, high=102, close=101.
    bar = pd.Series({"open": 100.0, "high": 102.0, "low": 94.0, "close": 101.0},
                    name=pd.Timestamp("2026-01-01"))
    pos = open_position(vs, fill_price=100.0, fill_ts=bar.name, config=cfg)
    # Ayni bar update: SL low=94 < SL=95 -> SL tetiklenmeli.
    remaining, trades = pm_update(pos, bar, cfg)
    assert remaining is None
    assert len(trades) == 1
    assert trades[0].exit_reason == "SL"
