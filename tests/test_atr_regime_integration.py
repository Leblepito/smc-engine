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
