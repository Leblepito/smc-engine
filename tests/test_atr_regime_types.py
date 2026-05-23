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
