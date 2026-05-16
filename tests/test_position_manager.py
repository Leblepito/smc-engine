"""Faz 5.1 — backtest/position_manager.py testleri.

Pozisyon acma/kapama, TP merdiveni kademeli kapanis, TP1 sonrasi breakeven,
bar OHLC ile SL/TP kontrolu, bar-ici cakisma -> SL once, spread/commission/
slippage.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from smc_engine.config import SMCConfig
from smc_engine.types import (
    AccountState,
    Bias,
    Direction,
    POIKind,
    POIRef,
    Setup,
    ValidatedSetup,
    Zone,
    ZoneAnchor,
    ZoneKind,
    ZoneStatus,
    TimeFrame,
)
from backtest.position_manager import open_position, update


# ---- yardimcilar -------------------------------------------------------

def _zero_cfg():
    """Maliyetsiz config — kesin-deger assert eden testler icin."""
    return SMCConfig(spread=0.0, commission_pct=0.0, slippage_pct=0.0)

def _zone():
    return Zone(
        kind=ZoneKind.DEMAND, top=100.0, bottom=99.0, timeframe=TimeFrame.H4,
        created_at=datetime(2026, 1, 1), status=ZoneStatus.FRESH,
        origin_candle_ts=datetime(2026, 1, 1), anchor=ZoneAnchor.BODY, age_bars=1,
    )


def _setup(direction=Direction.LONG, entry=100.0, sl=98.0,
           tp=(102.0, 104.0, 108.0), tp_weights=(0.5, 0.3, 0.2),
           score=0.7, fcount=3):
    poi = POIRef(kind=POIKind.ZONE, ref=_zone(), htf_aligned=True, score_hint=0.8)
    return Setup(
        direction=direction, entry=entry, sl=sl, tp=list(tp),
        tp_weights=list(tp_weights), poi=poi, confirmation=None,
        bias_context=Bias.BULLISH, confluence_score=score, rr=2.0,
        created_at=datetime(2026, 1, 1), confluence_factor_count=fcount,
    )


def _vsetup(**kw):
    s = _setup(**kw)
    return ValidatedSetup(setup=s, position_size=10.0, risk_amount=100.0,
                          guard_log=["r_sizing"])


def _bar(o, h, l, c, ts=None):
    ts = ts or datetime(2026, 1, 2)
    return pd.Series({"open": o, "high": h, "low": l, "close": c, "volume": 1.0},
                     name=pd.Timestamp(ts))


# ---- open_position -----------------------------------------------------

def test_open_position_long_basic():
    cfg = _zero_cfg()  # zero costs
    vs = _vsetup()
    pos = open_position(vs, fill_price=100.0, fill_ts=datetime(2026, 1, 1), config=cfg)
    assert pos.direction == Direction.LONG
    assert pos.entry == pytest.approx(100.0)
    assert pos.current_sl == pos.original_sl == 98.0
    assert pos.total_size == pytest.approx(pos.remaining_size)
    assert pos.tp_hits == []


def test_open_position_applies_spread_slippage_long():
    cfg = SMCConfig(spread=0.0, slippage_pct=0.001)
    vs = _vsetup()
    pos = open_position(vs, fill_price=100.0, fill_ts=datetime(2026, 1, 1), config=cfg)
    # long -> slippage fiyati yukari iter (kotu yon)
    assert pos.entry > 100.0


# ---- update: TP/SL detection ------------------------------------------

def test_sl_hit_closes_full_position():
    cfg = _zero_cfg()
    pos = open_position(_vsetup(), 100.0, datetime(2026, 1, 1), cfg)
    bar = _bar(99.5, 99.6, 97.0, 98.5)  # low pierces SL 98
    remaining, trades = update(pos, bar, cfg)
    assert remaining is None
    assert len(trades) == 1
    assert trades[0].exit_reason == "SL"
    assert trades[0].r_multiple == pytest.approx(-1.0, abs=1e-6)


def test_tp1_hit_partial_close_and_breakeven():
    cfg = _zero_cfg()
    pos = open_position(_vsetup(), 100.0, datetime(2026, 1, 1), cfg)
    bar = _bar(100.5, 102.5, 100.0, 101.5)  # high reaches TP1 102
    remaining, trades = update(pos, bar, cfg)
    assert remaining is not None
    assert 0 in remaining.tp_hits
    # breakeven: SL entry'ye kaydi
    assert remaining.current_sl == pytest.approx(remaining.entry)
    # kismi kapanis: remaining_size azaldi
    assert remaining.remaining_size < remaining.total_size
    assert len(trades) == 1
    assert trades[0].exit_reason == "TP1"
    assert trades[0].size == pytest.approx(pos.total_size * 0.5)


def test_all_tps_hit_in_one_bar_closes_all():
    cfg = _zero_cfg()
    pos = open_position(_vsetup(), 100.0, datetime(2026, 1, 1), cfg)
    bar = _bar(100.5, 109.0, 100.4, 108.5)  # high beyond TP3
    remaining, trades = update(pos, bar, cfg)
    assert remaining is None
    assert {t.exit_reason for t in trades} == {"TP1", "TP2", "TP3"}
    assert sum(t.size for t in trades) == pytest.approx(pos.total_size)


def test_intrabar_sl_and_tp_conflict_sl_first():
    cfg = _zero_cfg()
    pos = open_position(_vsetup(), 100.0, datetime(2026, 1, 1), cfg)
    # bar hem SL 98 hem TP1 102 dokunuyor -> SL once (en kotu)
    bar = _bar(100.0, 102.5, 97.5, 100.0)
    remaining, trades = update(pos, bar, cfg)
    assert remaining is None
    assert len(trades) == 1
    assert trades[0].exit_reason == "SL"


def test_no_hit_returns_position_unchanged():
    cfg = _zero_cfg()
    pos = open_position(_vsetup(), 100.0, datetime(2026, 1, 1), cfg)
    bar = _bar(100.2, 101.0, 99.5, 100.5)  # SL 98 / TP1 102 ikisi de degil
    remaining, trades = update(pos, bar, cfg)
    assert remaining is not None
    assert trades == []
    assert remaining.remaining_size == pos.total_size


def test_breakeven_sl_protects_after_tp1():
    cfg = _zero_cfg()
    pos = open_position(_vsetup(), 100.0, datetime(2026, 1, 1), cfg)
    # bar1: TP1 vur -> breakeven
    bar1 = _bar(100.5, 102.5, 100.2, 101.0)
    pos2, t1 = update(pos, bar1, cfg)
    assert pos2 is not None and 0 in pos2.tp_hits
    # bar2: fiyat entry'ye geri dusuyor -> breakeven SL tetiklenir
    bar2 = _bar(101.0, 101.2, 99.5, 99.8)
    pos3, t2 = update(pos2, bar2, cfg)
    assert pos3 is None
    assert len(t2) == 1
    assert t2[0].exit_reason in ("BREAKEVEN", "SL")
    # breakeven exit -> r_multiple ~ 0 (komisyon haric)
    assert abs(t2[0].r_multiple) < 0.05


def test_short_position_sl_tp_directionality():
    cfg = _zero_cfg()
    vs = _vsetup(direction=Direction.SHORT, entry=100.0, sl=102.0,
                 tp=(98.0, 96.0, 92.0))
    pos = open_position(vs, 100.0, datetime(2026, 1, 1), cfg)
    # short: high 102.5 -> SL hit
    bar = _bar(100.0, 102.5, 99.0, 101.0)
    remaining, trades = update(pos, bar, cfg)
    assert remaining is None
    assert trades[0].exit_reason == "SL"
    assert trades[0].r_multiple == pytest.approx(-1.0, abs=1e-6)


def test_short_tp1_hit():
    cfg = _zero_cfg()
    vs = _vsetup(direction=Direction.SHORT, entry=100.0, sl=102.0,
                 tp=(98.0, 96.0, 92.0))
    pos = open_position(vs, 100.0, datetime(2026, 1, 1), cfg)
    bar = _bar(99.5, 99.8, 97.5, 98.2)  # low 97.5 reaches TP1 98
    remaining, trades = update(pos, bar, cfg)
    assert remaining is not None and 0 in remaining.tp_hits
    assert remaining.current_sl == pytest.approx(remaining.entry)
    assert trades[0].exit_reason == "TP1"


def test_commission_reduces_pnl():
    cfg = SMCConfig(commission_pct=0.001)
    pos = open_position(_vsetup(), 100.0, datetime(2026, 1, 1), cfg)
    bar = _bar(100.5, 102.5, 100.0, 101.5)  # TP1
    _, trades = update(pos, bar, cfg)
    # komisyon dusulmus -> net pnl, brut TP kazancindan kucuk
    gross = (102.0 - 100.0) * (pos.total_size * 0.5)
    assert trades[0].pnl < gross
