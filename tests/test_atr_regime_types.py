"""Test additions for ATR regime filter (Spec §13.2, 2026-05-23):
TFSnapshot.atr_history + Setup.regime_metrics field extensions."""


def test_tfsnapshot_atr_history_default_none():
    """Mevcut TFSnapshot(...) yapimlari kirilmamali — atr_history default None."""
    from smc_engine.types import TFSnapshot, Bias
    snap = TFSnapshot(
        range_=None, bias=Bias.NEUTRAL,
        zones=[], imbalances=[], levels=[],
        liquidity_events=[], structure=[],
    )
    assert snap.atr_history is None


def test_tfsnapshot_atr_history_stores_list():
    """atr_history kwarg verildiginde liste olarak saklanmali."""
    from smc_engine.types import TFSnapshot, Bias
    snap = TFSnapshot(
        range_=None, bias=Bias.NEUTRAL,
        zones=[], imbalances=[], levels=[],
        liquidity_events=[], structure=[],
        atr_history=[1.0, 2.0, 3.0],
    )
    assert snap.atr_history == [1.0, 2.0, 3.0]


def _make_setup():
    """Test helper — gercek constructor imzalariyla Setup uretir."""
    from datetime import datetime, timezone
    from smc_engine.types import (
        Setup, Direction, Bias, POIRef, POIKind, Level, LevelKind, TimeFrame,
    )
    level = Level(
        kind=LevelKind.YO,
        price=100.0,
        timeframe=TimeFrame.D1,
        valid_from=datetime.now(timezone.utc),
        valid_until=None,
    )
    poi = POIRef(
        kind=POIKind.LEVEL,
        ref=level,
        htf_aligned=True,
        score_hint=0.5,
    )
    return Setup(
        direction=Direction.LONG,
        entry=100.0, sl=98.0, tp=[103.0],
        tp_weights=[1.0],
        poi=poi,
        confirmation=None,
        bias_context=Bias.BULLISH,
        confluence_score=0.5, rr=1.5,
        created_at=datetime.now(timezone.utc),
    )


def test_setup_regime_metrics_default_empty_dict():
    """Mevcut Setup(...) yapimlari kirilmamali — regime_metrics default {}."""
    setup = _make_setup()
    assert setup.regime_metrics == {}


def test_setup_regime_metrics_independent_instances():
    """Mutable default footgun yok — her instance kendi dict'i (default_factory)."""
    s1 = _make_setup()
    s2 = _make_setup()
    s1.regime_metrics["atr_percentile"] = 0.9
    assert s2.regime_metrics == {}, "Setup default_factory dict olmali, paylasilan instance degil"
