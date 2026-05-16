"""R2a — Ö-7 INEFFICIENCY tipi (TDD)."""
from __future__ import annotations
import pandas as pd
import pytest
from smc_engine.config import SMCConfig
from smc_engine.detectors.imbalance_detector import detect
from smc_engine.types import ImbalanceKind


def _df(rows, start="2026-01-01", freq="h"):
    idx = pd.date_range(start=start, periods=len(rows), freq=freq)
    return pd.DataFrame(rows, index=idx)[["open", "high", "low", "close", "volume"]]


def _c(o, h, l, c, v=1000.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def test_inefficiency_emitted_for_very_large_fvg():
    """Cok buyuk FVG (>= inefficiency_gap_atr x ATR) INEFFICIENCY olarak
    isaretlenir; orta buyukluk LIQ_VOID, kucuk FVG.
    """
    # Stable ATR ~ 2.0; sonra ULTRA buyuk gap.
    rows = []
    for i in range(20):
        # genel kucuk barlar -> ATR kucuk
        o = 100.0 + i * 0.1
        rows.append(_c(o, o + 1.0, o - 1.0, o + 0.1))
    # idx 20-22: 3-mum bullish FVG dizisi (cok buyuk bosluk)
    rows.append(_c(100, 102, 99, 101))    # idx 20  high=102
    rows.append(_c(106, 160, 105, 158))   # idx 21  orta mum (impulse) — buyuk
    rows.append(_c(158, 162, 155, 160))   # idx 22  low=155  -> bosluk 102..155 (53 birim)
    df = _df(rows)
    cfg = SMCConfig()
    imbs = detect(df, cfg)
    # En az bir imbalance bekleriz; INEFFICIENCY tipi uretilmis olmali.
    kinds = {imb.kind for imb in imbs}
    assert ImbalanceKind.INEFFICIENCY in kinds, \
        f"INEFFICIENCY uretilmedi, sadece: {kinds}"
