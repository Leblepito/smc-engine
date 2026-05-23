"""Orchestrator atr_history doldurma + builder regime_metrics + risk_guard
end-to-end integration testleri (Spec §13.2)."""
import numpy as np
import pandas as pd
from datetime import datetime, timezone

from smc_engine.config import SMCConfig
from smc_engine.orchestrator import analyze
from smc_engine.types import TimeFrame


def _synthetic_ohlcv(n: int, base: float = 100.0, vol: float = 1.0,
                     freq: str = "4h") -> pd.DataFrame:
    """Sentetik OHLCV — sabit volatilite, hafif trend."""
    rng = pd.date_range(
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        periods=n, freq=freq,
    )
    rs = np.random.default_rng(42)
    closes = base + np.cumsum(rs.normal(0, vol, n))
    df = pd.DataFrame({
        "open": closes + rs.normal(0, vol * 0.3, n),
        "high": closes + np.abs(rs.normal(0, vol, n)),
        "low": closes - np.abs(rs.normal(0, vol, n)),
        "close": closes,
        "volume": rs.uniform(100, 1000, n),
    }, index=rng)
    df["high"] = df[["high", "open", "close"]].max(axis=1)
    df["low"] = df[["low", "open", "close"]].min(axis=1)
    return df


def test_orchestrator_writes_atr_history_to_h4_snapshot():
    """analyze() cikarinda picture.per_tf[H4].atr_history dolu olmali."""
    h4 = _synthetic_ohlcv(200, freq="4h")
    d1 = h4.resample("1D").agg({"open": "first", "high": "max", "low": "min",
                                  "close": "last", "volume": "sum"}).dropna()
    h1 = _synthetic_ohlcv(800, freq="1h")
    m15 = _synthetic_ohlcv(200, freq="15min")

    cfg = SMCConfig()
    cfg.atr_percentile_window = 96
    data = {
        TimeFrame.D1: d1, TimeFrame.H4: h4,
        TimeFrame.H1: h1, TimeFrame.M15: m15,
    }
    picture = analyze(data, cfg, at_bar=h4.index[-1].to_pydatetime())

    snap_h4 = picture.per_tf.get(TimeFrame.H4)
    assert snap_h4 is not None
    assert snap_h4.atr_history is not None, (
        "H4 snapshot'ta atr_history doldurulmali (orchestrator gorevidir)"
    )
    # Tight length: H4 frame has 200 bars, ATR series after dropna ~ 200 valid,
    # so history length must be exactly min(window, expected_max) = min(96, 200) = 96
    expected_max = len(h4)  # 200 valid ATR values after rolling
    assert len(snap_h4.atr_history) == min(cfg.atr_percentile_window, expected_max), (
        f"atr_history length must equal min(window, expected_max)="
        f"{min(cfg.atr_percentile_window, expected_max)}; got {len(snap_h4.atr_history)}"
    )
    # son eleman snap.atr ile ayni olmali (tutarlilik)
    assert abs(snap_h4.atr_history[-1] - snap_h4.atr) < 1e-9

    # Non-H4 TFs must leave atr_history as None (memory-saving gate)
    for tf_other in (TimeFrame.D1, TimeFrame.M15):
        snap_other = picture.per_tf.get(tf_other)
        if snap_other is not None:
            assert snap_other.atr_history is None, (
                f"{tf_other} should not populate atr_history "
                f"(H4-only optimization); got {snap_other.atr_history}"
            )


def test_build_with_diagnostics_writes_atr_percentile_to_setup():
    """build_with_diagnostics H4 atr_history'den percentile hesaplayip
    Setup.regime_metrics'e yazmali."""
    import pytest
    from smc_engine.setup_builder import build_with_diagnostics
    from smc_engine.types import (
        Bias, Direction, MarketPicture, POIKind, POIRef, Range, TFSnapshot,
        TimeFrame, Zone, ZoneAnchor, ZoneKind, ZoneStatus,
    )

    cfg = SMCConfig()
    cfg.atr_percentile_window = 96
    # atr_history: 1..100; window=96 -> recent=[5..100], current=80
    # rank = count(v<=80)/96 = 76/96 ~= 0.792
    history = [float(i) for i in range(1, 101)]
    ts = datetime(2024, 6, 1, tzinfo=timezone.utc)

    # HTF range: 0..200, ekvilibrium=100. Zone (DEMAND) bottom=20 top=60 ->
    # mid=40 frac=0.20 derin discount; entry=top=60.
    htf_range = Range(
        high=200.0, low=0.0, equilibrium=100.0,
        premium_zone=(100.0, 200.0), discount_zone=(0.0, 100.0),
        timeframe=TimeFrame.H4, formed_at=ts,
    )
    demand_zone = Zone(
        kind=ZoneKind.DEMAND,
        top=60.0, bottom=20.0,
        timeframe=TimeFrame.H4,
        created_at=ts,
        status=ZoneStatus.FRESH,
        origin_candle_ts=ts,
        anchor=ZoneAnchor.BODY,
        age_bars=5,
    )
    h4 = TFSnapshot(
        range_=htf_range,
        bias=Bias.BULLISH,
        zones=[demand_zone],
        imbalances=[], levels=[], liquidity_events=[],
        structure=[],
        atr=80.0,
        atr_history=history,
    )
    # POI direction-aligned (LONG -> DEMAND).
    poi = POIRef(
        kind=POIKind.ZONE,
        ref=demand_zone,
        htf_aligned=True,
        score_hint=0.5,
    )
    picture = MarketPicture(
        per_tf={TimeFrame.H4: h4},
        htf_bias=Bias.BULLISH,
        htf_range=htf_range,
        active_pois=[poi],
        at_timestamp=ts,
        current_price=60.0,
    )

    result = build_with_diagnostics(picture, cfg)
    assert result.setup is not None, (
        f"setup uretilmesini bekliyorum; reason={result.no_setup_reason}, "
        f"diag={result.diagnostics}"
    )
    rank = result.setup.regime_metrics.get("atr_percentile")
    assert rank is not None, (
        f"regime_metrics['atr_percentile'] yazilmali; got {result.setup.regime_metrics}"
    )
    assert rank == pytest.approx(0.792, rel=0.02), (
        f"rank ~0.792 olmali (76/96); got {rank}"
    )
