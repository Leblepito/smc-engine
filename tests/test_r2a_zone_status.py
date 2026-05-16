"""R2a — Ö-3 zone status MITIGATED gecisi (max_zone_age_bars)."""
from __future__ import annotations
import pandas as pd
import pytest
from smc_engine.config import SMCConfig
from smc_engine.orchestrator import analyze
from smc_engine.types import TimeFrame, ZoneStatus


def _c(o, h, l, c, v=1000.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def _df(rows, start, freq):
    idx = pd.date_range(start=start, periods=len(rows), freq=freq)
    return pd.DataFrame(rows, index=idx)[["open","high","low","close","volume"]]


def test_zone_status_mitigated_when_age_exceeds_threshold():
    """Ö-3: age_bars > max_zone_age_bars ise zone status MITIGATED olmali.
    
    Onceki kod max_zone_age_bars'i hic kullanmiyordu (olu config).
    """
    # OB olusumu erken; sonra cok sayida bar
    rows = [
        _c(110, 111, 108, 109),
        _c(109, 110, 106, 107),
        _c(107, 108, 104, 105),
        _c(105, 106, 102, 103),
        _c(103, 104, 100, 101),
        _c(96, 97, 92, 93),       # OB - DEMAND zone
        _c(98, 109, 97, 108),     # breakout
    ]
    # ekle: cok bar (age uzasin)
    for i in range(60):
        rows.append(_c(110+i*0.1, 110+i*0.1+1, 110+i*0.1-0.5, 110+i*0.1+0.5))
    h4 = _df(rows, "2026-01-01", "4h")
    d1 = _df([_c(100+i, 102+i, 99+i, 101+i) for i in range(15)], "2026-01-01", "D")
    m15 = _df([_c(100+i*0.05, 100+i*0.05+0.5, 100+i*0.05-0.5, 100+i*0.05+0.2) for i in range(96)],
              "2026-01-01", "15min")
    cfg = SMCConfig()
    # Esiği duşür: yas 30'u gecince MITIGATED.
    cfg.max_zone_age_bars = 30
    pic = analyze({TimeFrame.D1: d1, TimeFrame.H4: h4, TimeFrame.M15: m15}, cfg)
    h4_snap = pic.per_tf.get(TimeFrame.H4)
    assert h4_snap is not None
    if h4_snap.zones:
        # En az bir zone yaslanmiş + MITIGATED durumda olmali.
        old_zones = [z for z in h4_snap.zones if z.age_bars > 30]
        assert old_zones, "Yasli zone uretilemedi (fixture problemi)"
        statuses = {z.status for z in old_zones}
        assert ZoneStatus.MITIGATED in statuses, \
            f"Yasli zone'lar MITIGATED degil: {statuses}"


def test_zone_status_remains_fresh_when_young():
    """Genc zone (age < max_zone_age_bars) FRESH kalmali."""
    rows = [
        _c(110, 111, 108, 109),
        _c(109, 110, 106, 107),
        _c(107, 108, 104, 105),
        _c(105, 106, 102, 103),
        _c(103, 104, 100, 101),
        _c(96, 97, 92, 93),       # OB
        _c(98, 109, 97, 108),     # breakout
        _c(108, 114, 107, 113),
        _c(113, 118, 112, 117),
        _c(117, 121, 116, 120),
    ]
    h4 = _df(rows, "2026-01-01", "4h")
    d1 = _df([_c(100+i, 102+i, 99+i, 101+i) for i in range(15)], "2026-01-01", "D")
    m15 = _df([_c(100+i*0.05, 100+i*0.05+0.5, 100+i*0.05-0.5, 100+i*0.05+0.2) for i in range(96)],
              "2026-01-01", "15min")
    cfg = SMCConfig()
    cfg.max_zone_age_bars = 200  # yuksek esik
    pic = analyze({TimeFrame.D1: d1, TimeFrame.H4: h4, TimeFrame.M15: m15}, cfg)
    h4_snap = pic.per_tf.get(TimeFrame.H4)
    assert h4_snap is not None
    if h4_snap.zones:
        for z in h4_snap.zones:
            if z.age_bars < 200:
                # Genc zone'lar FRESH veya en azindan MITIGATED degil.
                assert z.status != ZoneStatus.MITIGATED, \
                    f"Genc zone yanlislikla MITIGATED (age={z.age_bars})"
