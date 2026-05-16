"""R2a — Ö-6 confluence_factor_count yalniz bagimsiz kanit (TDD)."""
from __future__ import annotations
import pytest
from datetime import datetime
from smc_engine.config import SMCConfig
from smc_engine.types import (
    Bias, Direction, MarketPicture, POIKind, POIRef, Range, TFSnapshot,
    TimeFrame, Zone, ZoneAnchor, ZoneKind, ZoneStatus,
)
from smc_engine.setup_builder import build


def _zone(kind=ZoneKind.DEMAND, top=100.0, bottom=95.0):
    return Zone(
        kind=kind, top=top, bottom=bottom, timeframe=TimeFrame.H4,
        created_at=datetime(2026, 1, 1), status=ZoneStatus.FRESH,
        origin_candle_ts=datetime(2026, 1, 1), anchor=ZoneAnchor.WICK,
        age_bars=0,
    )


def _picture(zones=None, h4_atr=1.0, htf_range=None):
    z = zones or [_zone()]
    h4 = TFSnapshot(
        range_=None, bias=Bias.BULLISH, zones=[], imbalances=[], levels=[],
        liquidity_events=[], structure=[], atr=h4_atr,
    )
    d1 = TFSnapshot(
        range_=htf_range, bias=Bias.BULLISH, zones=[], imbalances=[],
        levels=[], liquidity_events=[], structure=[], atr=0.0,
    )
    m15 = TFSnapshot(
        range_=None, bias=Bias.BULLISH, zones=[], imbalances=[], levels=[],
        liquidity_events=[], structure=[], atr=0.0,
    )
    pois = [POIRef(kind=POIKind.ZONE, ref=zz, htf_aligned=True, score_hint=1.0)
            for zz in z]
    return MarketPicture(
        per_tf={TimeFrame.H4: h4, TimeFrame.D1: d1, TimeFrame.M15: m15},
        htf_bias=Bias.BULLISH, htf_range=htf_range,
        active_pois=pois, at_timestamp=datetime(2026, 1, 1),
        current_price=97.0,
    )


def test_factor_count_excludes_poi_quality_and_premium_discount():
    """Ö-6: poi_quality ve premium_discount factor_count'a sayilmamali —
    bagimsiz kanit faktorleri yalniz: liquidity / level / fvg / clustering.
    """
    # Tek POI, htf_range yok -> premium_discount=0; poi_quality > 0 (default).
    # Hicbir bagimsiz kanit yok -> factor_count = 0 (eski koda gore =1).
    pic = _picture()
    cfg = SMCConfig()
    cfg.confluence_min_score = 0.0  # esik gevsetilmis ki build None donmesin
    cfg.sl_min_atr_multiple = 0.0   # atr 0 cinsinden ATR kontrolunu atla
    setup = build(pic, cfg)
    assert setup is not None
    # Yeni semantik: poi_quality + premium_discount sayilmaz; sayilan
    # 4 bagimsiz faktorden hicbiri >0 olmadigi icin count=0.
    assert setup.confluence_factor_count == 0, \
        f"poi_quality sayilmamali; count={setup.confluence_factor_count}"


def test_factor_count_counts_independent_evidence_only():
    """Iki bagimsiz faktor (cluster + level) >0 -> factor_count=2."""
    from smc_engine.types import Level, LevelKind
    z = _zone(top=100.0, bottom=95.0)
    z2 = _zone(top=101.0, bottom=96.0)  # cluster: cok yakin
    h4 = TFSnapshot(
        range_=None, bias=Bias.BULLISH, zones=[],
        imbalances=[],
        levels=[Level(kind=LevelKind.WO, price=97.5, timeframe=TimeFrame.H4,
                      valid_from=datetime(2026, 1, 1), valid_until=None)],
        liquidity_events=[], structure=[], atr=1.0,
    )
    d1 = TFSnapshot(
        range_=None, bias=Bias.BULLISH, zones=[], imbalances=[], levels=[],
        liquidity_events=[], structure=[], atr=0.0,
    )
    m15 = TFSnapshot(
        range_=None, bias=Bias.BULLISH, zones=[], imbalances=[], levels=[],
        liquidity_events=[], structure=[], atr=0.0,
    )
    pois = [POIRef(kind=POIKind.ZONE, ref=z, htf_aligned=True, score_hint=1.0),
            POIRef(kind=POIKind.ZONE, ref=z2, htf_aligned=True,
                   score_hint=1.0)]
    pic = MarketPicture(
        per_tf={TimeFrame.H4: h4, TimeFrame.D1: d1, TimeFrame.M15: m15},
        htf_bias=Bias.BULLISH, htf_range=None,
        active_pois=pois, at_timestamp=datetime(2026, 1, 1),
        current_price=97.0,
    )
    cfg = SMCConfig()
    cfg.confluence_min_score = 0.0
    cfg.sl_min_atr_multiple = 0.0
    setup = build(pic, cfg)
    assert setup is not None
    # Bagimsiz faktor: level_confluence>0 ve clustering>0 -> count >= 2.
    # poi_quality / premium_discount sayilmasaydı bile.
    assert setup.confluence_factor_count >= 2
