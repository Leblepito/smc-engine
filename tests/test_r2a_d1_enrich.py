"""R2a — Ö-2 D1 enrichment (zone.age_bars / imbalance.fill_ratio)."""
from __future__ import annotations
import pandas as pd
import pytest
from smc_engine.config import SMCConfig
from smc_engine.orchestrator import analyze
from smc_engine.types import TimeFrame


def _c(o, h, l, c, v=1000.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def _df(rows, start, freq):
    idx = pd.date_range(start=start, periods=len(rows), freq=freq)
    return pd.DataFrame(rows, index=idx)[["open","high","low","close","volume"]]


def test_d1_zones_have_age_bars_enriched():
    """Ö-2: D1 snapshot'inda zone'lar age_bars=0 sahte degerinde kalmamali —
    en azindan en eski zone icin age_bars > 0 olmali (orchestrator
    enrichment'ı D1'i de gecirmeli).
    """
    # D1 fixture: pump-dump + breakout (OB) + sonra 10 bar daha (zone yaslandi).
    rows = [
        _c(110, 111, 108, 109),
        _c(109, 110, 106, 107),
        _c(107, 108, 104, 105),
        _c(105, 106, 102, 103),
        _c(103, 104, 100, 101),
        _c(96, 97, 92, 93),         # OB mumu - DEMAND zone burada
        _c(98, 109, 97, 108),       # bullish breakout
        _c(108, 114, 107, 113),
        _c(113, 118, 112, 117),
        _c(117, 121, 116, 120),
        _c(120, 123, 119, 122),
        _c(122, 125, 121, 124),
        _c(124, 127, 123, 126),
        _c(126, 129, 125, 128),
        _c(128, 131, 127, 130),
        _c(130, 133, 129, 132),
        _c(132, 135, 131, 134),
        _c(134, 137, 133, 136),
        _c(136, 139, 135, 138),
        _c(138, 141, 137, 140),
    ]
    d1 = _df(rows, "2026-01-01", "D")
    # H4 ve M15 minimal
    h4 = _df([_c(100+i*0.1, 100+i*0.1+1, 100+i*0.1-1, 100+i*0.1+0.5) for i in range(20)],
             "2026-01-01", "4h")
    m15 = _df([_c(100+i*0.05, 100+i*0.05+0.5, 100+i*0.05-0.5, 100+i*0.05+0.2) for i in range(96)],
              "2026-01-01", "15min")
    cfg = SMCConfig()
    pic = analyze({TimeFrame.D1: d1, TimeFrame.H4: h4, TimeFrame.M15: m15}, cfg)

    d1_snap = pic.per_tf.get(TimeFrame.D1)
    assert d1_snap is not None
    if d1_snap.zones:
        # En az bir zone age_bars > 0 olmali (zone DataFrame ortasinda olusup
        # sonradan barlar geldi).
        max_age = max(z.age_bars for z in d1_snap.zones)
        assert max_age > 0, \
            f"D1 zone age_bars enrichment calismiyor (max={max_age})"


def test_d1_imbalances_have_fill_ratio_enriched():
    """Ö-2: D1 snapshot'inda imbalance.fill_ratio sahte 0.0 kalmamali
    (eger sonraki barlar boslugu doldurduysa)."""
    # 3-mum bullish FVG, sonra fiyat asagi gelip dolduruyor
    rows = [
        _c(100, 105, 99, 104),     # 0
        _c(101, 103, 100, 102),    # 1  high=103 (FVG alt)
        _c(105, 112, 104, 110),    # 2  orta mum
        _c(110, 115, 108, 113),    # 3  low=108 (FVG ust); gap (103, 108)
        _c(113, 116, 111, 114),    # 4
        _c(114, 115, 109, 110),    # 5  low=109 -> FVG'yi DOLDURMAYA basliyor
        _c(110, 112, 104, 105),    # 6  low=104 -> FVG TAMAMEN dolduruluyor
        _c(105, 108, 103, 107),    # 7
        _c(107, 110, 105, 109),
        _c(109, 112, 107, 111),
        _c(111, 114, 109, 113),
        _c(113, 116, 111, 115),
        _c(115, 118, 113, 117),
        _c(117, 120, 115, 119),
        _c(119, 122, 117, 121),
        _c(121, 124, 119, 123),
        _c(123, 126, 121, 125),
        _c(125, 128, 123, 127),
        _c(127, 130, 125, 129),
        _c(129, 132, 127, 131),
    ]
    d1 = _df(rows, "2026-01-01", "D")
    h4 = _df([_c(100+i*0.1, 100+i*0.1+1, 100+i*0.1-1, 100+i*0.1+0.5) for i in range(20)],
             "2026-01-01", "4h")
    m15 = _df([_c(100+i*0.05, 100+i*0.05+0.5, 100+i*0.05-0.5, 100+i*0.05+0.2) for i in range(96)],
              "2026-01-01", "15min")
    cfg = SMCConfig()
    pic = analyze({TimeFrame.D1: d1, TimeFrame.H4: h4, TimeFrame.M15: m15}, cfg)
    d1_snap = pic.per_tf.get(TimeFrame.D1)
    assert d1_snap is not None
    if d1_snap.imbalances:
        # En az bir imbalance fill_ratio > 0 olmali.
        max_fill = max(imb.fill_ratio for imb in d1_snap.imbalances)
        assert max_fill > 0.0, \
            f"D1 imbalance fill_ratio enrichment calismiyor (max={max_fill})"
