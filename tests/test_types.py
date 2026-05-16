"""Spec §4 tip bütünlüğü testleri — örnekleme, alan tipleri, enum değerleri."""

from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import datetime, timezone

import pytest

from smc_engine import types as T


UTC = timezone.utc
TS = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
TS2 = datetime(2026, 1, 3, 12, 0, tzinfo=UTC)


# ---------------- Enum'lar ----------------


def test_enum_members():
    assert {e.name for e in T.TimeFrame} == {"M15", "H1", "H4", "H8", "D1"}
    assert {e.name for e in T.Bias} == {"BULLISH", "BEARISH", "NEUTRAL"}
    assert {e.name for e in T.Direction} == {"LONG", "SHORT"}
    assert {e.name for e in T.ZoneKind} == {"SUPPLY", "DEMAND"}
    assert {e.name for e in T.ZoneStatus} == {"FRESH", "TESTED", "MITIGATED", "BROKEN"}
    assert {e.name for e in T.ZoneAnchor} == {"WICK", "BODY"}
    assert {e.name for e in T.ImbalanceKind} == {"FVG", "LIQ_VOID", "INEFFICIENCY"}
    assert {e.name for e in T.LevelKind} == {
        "YO", "MO", "WO", "DO", "PMO", "PWO",
        "MONDAY_H", "MONDAY_L", "OLD_ATH", "PREV_ATH",
    }
    assert {e.name for e in T.LiquidityKind} == {"SWEEP", "DEVIATION", "SFP"}
    assert {e.name for e in T.Significance} == {"HIGH", "LOW"}
    assert {e.name for e in T.StructureKind} == {"CHoCH", "BOS"}
    assert {e.name for e in T.SwingKind} == {"HIGH", "LOW"}
    assert {e.name for e in T.POIKind} == {"ZONE", "IMBALANCE", "LEVEL"}


# ---------------- Yardımcı + detektör çıktıları (frozen) ----------------


def test_swingpoint():
    sp = T.SwingPoint(timestamp=TS, price=100.0, kind=T.SwingKind.HIGH)
    assert sp.price == 100.0
    assert sp.kind is T.SwingKind.HIGH
    with pytest.raises(FrozenInstanceError):
        sp.price = 1.0


def test_range():
    r = T.Range(
        high=110.0, low=90.0, equilibrium=100.0,
        premium_zone=(100.0, 110.0), discount_zone=(90.0, 100.0),
        timeframe=T.TimeFrame.D1, formed_at=TS,
    )
    assert r.equilibrium == 100.0
    assert isinstance(r.formed_at, datetime)
    with pytest.raises(FrozenInstanceError):
        r.high = 0.0


def test_zone():
    z = T.Zone(
        kind=T.ZoneKind.DEMAND, top=105.0, bottom=100.0,
        timeframe=T.TimeFrame.H4, created_at=TS, status=T.ZoneStatus.FRESH,
        origin_candle_ts=TS, anchor=T.ZoneAnchor.BODY, age_bars=0,
    )
    assert z.status is T.ZoneStatus.FRESH
    assert z.age_bars == 0
    with pytest.raises(FrozenInstanceError):
        z.age_bars = 5


def test_imbalance():
    im = T.Imbalance(
        kind=T.ImbalanceKind.FVG, top=102.0, bottom=100.0,
        direction=T.Direction.LONG, timeframe=T.TimeFrame.H1,
        created_at=TS, filled=False, fill_ratio=0.0,
    )
    assert im.filled is False
    assert im.fill_ratio == 0.0


def test_level():
    lv = T.Level(
        kind=T.LevelKind.WO, price=100.0, timeframe=T.TimeFrame.D1,
        valid_from=TS, valid_until=TS2,
    )
    assert lv.kind is T.LevelKind.WO
    lv2 = T.Level(
        kind=T.LevelKind.YO, price=50.0, timeframe=T.TimeFrame.D1,
        valid_from=TS, valid_until=None,
    )
    assert lv2.valid_until is None


def test_liquidity_event():
    le = T.LiquidityEvent(
        kind=T.LiquidityKind.SWEEP, swept_price=110.0,
        direction=T.Direction.SHORT, candle_ts=TS,
        reclaimed=True, significance=T.Significance.HIGH,
    )
    assert le.reclaimed is True
    assert le.significance is T.Significance.HIGH


def test_structure_break():
    sb = T.StructureBreak(
        kind=T.StructureKind.CHoCH, direction=T.Direction.LONG,
        broken_swing_price=100.0, confirm_candle_ts=TS,
        timeframe=T.TimeFrame.M15,
    )
    assert sb.kind is T.StructureKind.CHoCH
    assert isinstance(sb.confirm_candle_ts, datetime)


def test_poiref():
    z = T.Zone(
        kind=T.ZoneKind.DEMAND, top=105.0, bottom=100.0,
        timeframe=T.TimeFrame.H4, created_at=TS, status=T.ZoneStatus.FRESH,
        origin_candle_ts=TS, anchor=T.ZoneAnchor.BODY, age_bars=0,
    )
    poi = T.POIRef(kind=T.POIKind.ZONE, ref=z, htf_aligned=True, score_hint=0.8)
    assert poi.ref is z
    assert poi.htf_aligned is True


# ---------------- Kompozit / mutable tipler (frozen=False) ----------------


def _make_setup():
    z = T.Zone(
        kind=T.ZoneKind.DEMAND, top=105.0, bottom=100.0,
        timeframe=T.TimeFrame.H4, created_at=TS, status=T.ZoneStatus.FRESH,
        origin_candle_ts=TS, anchor=T.ZoneAnchor.BODY, age_bars=0,
    )
    poi = T.POIRef(kind=T.POIKind.ZONE, ref=z, htf_aligned=True, score_hint=0.8)
    return T.Setup(
        direction=T.Direction.LONG, entry=101.0, sl=99.0,
        tp=[103.0, 105.0, 108.0], tp_weights=[0.5, 0.3, 0.2],
        poi=poi, confirmation=None, bias_context=T.Bias.BULLISH,
        confluence_score=0.6, rr=2.0, created_at=TS,
    )


def test_setup_mutable():
    s = _make_setup()
    assert sum(s.tp_weights) == pytest.approx(1.0)
    assert len(s.tp) == 3
    s.confluence_score = 0.9  # mutable
    assert s.confluence_score == 0.9


def test_rejection():
    s = _make_setup()
    rej = T.Rejection(reason="score too low", gate="confluence", setup=s)
    assert rej.gate == "confluence"
    assert rej.setup is s


def test_account_state():
    acc = T.AccountState(
        equity=10000.0, open_position=False, recent_results=[1.0, -0.5],
        consecutive_losses=1, max_drawdown_pct=0.05,
    )
    acc.equity = 9500.0  # mutable
    assert acc.equity == 9500.0
    assert acc.recent_results == [1.0, -0.5]


def test_validated_setup():
    s = _make_setup()
    vs = T.ValidatedSetup(setup=s, position_size=0.1, risk_amount=100.0, guard_log=["ok"])
    assert vs.position_size == 0.1
    vs.guard_log.append("sized")
    assert "sized" in vs.guard_log


def test_tf_snapshot():
    snap = T.TFSnapshot(
        range_=None, bias=T.Bias.NEUTRAL, zones=[], imbalances=[],
        levels=[], liquidity_events=[], structure=[],
    )
    assert snap.bias is T.Bias.NEUTRAL
    assert snap.zones == []
    snap.bias = T.Bias.BULLISH  # mutable
    assert snap.bias is T.Bias.BULLISH


def test_market_picture():
    snap = T.TFSnapshot(
        range_=None, bias=T.Bias.NEUTRAL, zones=[], imbalances=[],
        levels=[], liquidity_events=[], structure=[],
    )
    mp = T.MarketPicture(
        per_tf={T.TimeFrame.D1: snap}, htf_bias=T.Bias.BULLISH,
        htf_range=None, active_pois=[], at_timestamp=TS, current_price=100.0,
    )
    assert mp.htf_bias is T.Bias.BULLISH
    assert mp.per_tf[T.TimeFrame.D1] is snap
    assert mp.current_price == 100.0


# ---------------- frozen / mutable disiplin doğrulaması ----------------


@pytest.mark.parametrize("cls", [
    T.SwingPoint, T.Range, T.Zone, T.Imbalance, T.Level,
    T.LiquidityEvent, T.StructureBreak, T.POIRef,
])
def test_frozen_dataclasses(cls):
    assert is_dataclass(cls)
    assert cls.__dataclass_params__.frozen is True


@pytest.mark.parametrize("cls", [
    T.Setup, T.Rejection, T.AccountState, T.ValidatedSetup,
    T.TFSnapshot, T.MarketPicture,
])
def test_mutable_dataclasses(cls):
    assert is_dataclass(cls)
    assert cls.__dataclass_params__.frozen is False


# ---------------- Faz 3 review fix: TFSnapshot.atr alani ----------------


def test_tf_snapshot_atr_field_defaults_zero():
    """TFSnapshot.atr default'lu (0.0) — mevcut yapim yerleri kirilmaz."""
    snap = T.TFSnapshot(
        range_=None, bias=T.Bias.NEUTRAL, zones=[], imbalances=[],
        levels=[], liquidity_events=[], structure=[],
    )
    assert snap.atr == 0.0


def test_tf_snapshot_atr_field_settable():
    """TFSnapshot.atr explicit verilebilir + mutable."""
    snap = T.TFSnapshot(
        range_=None, bias=T.Bias.NEUTRAL, zones=[], imbalances=[],
        levels=[], liquidity_events=[], structure=[], atr=12.5,
    )
    assert snap.atr == 12.5
    snap.atr = 3.0
    assert snap.atr == 3.0
