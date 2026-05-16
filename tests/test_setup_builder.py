"""TDD test'leri — smc_engine/setup_builder.py (Plan Faz 3, task 3.1).

``build(picture: MarketPicture, config) -> Setup | None``

Confluence agirlikli skor (6 faktor, config.confluence_weights — Spec §7):
  poi_quality, premium_discount, liquidity_context, level_confluence,
  fvg_imbalance, clustering.
Skor [0,1]; eksik faktor -> 0 katki, renormalize YOK.
Entry/SL/TP merdiveni; SL yapisal (swing otesi); rr hesabi; tp_weights toplam=1.0.
No-setup: skor < confluence_min_score -> None; SL < sl_min_atr_multiple*ATR -> None.
``build()`` saf/deterministik.
"""

from __future__ import annotations

import pandas as pd
import pytest

from smc_engine.config import SMCConfig
from smc_engine.setup_builder import build
from smc_engine.types import (
    Bias,
    Direction,
    Imbalance,
    ImbalanceKind,
    Level,
    LevelKind,
    LiquidityEvent,
    LiquidityKind,
    MarketPicture,
    POIKind,
    POIRef,
    Range,
    Setup,
    Significance,
    StructureBreak,
    StructureKind,
    TFSnapshot,
    TimeFrame,
    Zone,
    ZoneAnchor,
    ZoneKind,
    ZoneStatus,
)

TS = pd.Timestamp("2026-03-01")


# ============================================================
# Yardimcilar — sentetik MarketPicture insa
# ============================================================


def _df(rows, start="2026-01-01", freq="4h"):
    idx = pd.date_range(start=start, periods=len(rows), freq=freq)
    return pd.DataFrame(rows, index=idx)[
        ["open", "high", "low", "close", "volume"]
    ]


def _candle(o, h, l, c, v=1000.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def _ramp_df(n=60, base=100.0, step=0.5):
    """Yumusak yukselen df — ATR ~ sabit kucuk deger."""
    rows = []
    for i in range(n):
        o = base + i * step
        rows.append(_candle(o, o + 1.0, o - 1.0, o + step))
    return _df(rows)


def _demand_zone(top=82.0, bottom=78.0, status=ZoneStatus.FRESH):
    return Zone(
        kind=ZoneKind.DEMAND,
        top=top,
        bottom=bottom,
        timeframe=TimeFrame.H4,
        created_at=TS,
        status=status,
        origin_candle_ts=TS,
        anchor=ZoneAnchor.BODY,
        age_bars=5,
    )


def _supply_zone(top=122.0, bottom=118.0, status=ZoneStatus.FRESH):
    return Zone(
        kind=ZoneKind.SUPPLY,
        top=top,
        bottom=bottom,
        timeframe=TimeFrame.H4,
        created_at=TS,
        status=status,
        origin_candle_ts=TS,
        anchor=ZoneAnchor.BODY,
        age_bars=5,
    )


def _htf_range(high=130.0, low=70.0):
    eq = (high + low) / 2
    return Range(
        high=high,
        low=low,
        equilibrium=eq,
        premium_zone=(eq, high),
        discount_zone=(low, eq),
        timeframe=TimeFrame.D1,
        formed_at=TS,
    )


def _poi_zone(zone, htf_aligned=True, score_hint=1.0):
    return POIRef(
        kind=POIKind.ZONE, ref=zone, htf_aligned=htf_aligned,
        score_hint=score_hint,
    )


def _structure_break(direction=Direction.LONG, kind=StructureKind.CHoCH):
    return StructureBreak(
        kind=kind,
        direction=direction,
        broken_swing_price=98.0,
        confirm_candle_ts=TS,
        timeframe=TimeFrame.M15,
    )


def _picture(
    *,
    htf_bias=Bias.BULLISH,
    htf_range=None,
    active_pois=None,
    h4_df=None,
    m15_structure=None,
    h4_liquidity=None,
    h4_imbalances=None,
    h4_levels=None,
    h4_zones=None,
    current_price=80.0,
):
    """Minimal MarketPicture — setup_builder testleri icin."""
    if h4_df is None:
        h4_df = _ramp_df()
    h4_snap = TFSnapshot(
        range_=None,
        bias=htf_bias,
        zones=h4_zones or [],
        imbalances=h4_imbalances or [],
        levels=h4_levels or [],
        liquidity_events=h4_liquidity or [],
        structure=[],
    )
    m15_snap = TFSnapshot(
        range_=None,
        bias=htf_bias,
        zones=[],
        imbalances=[],
        levels=[],
        liquidity_events=[],
        structure=m15_structure or [],
    )
    d1_snap = TFSnapshot(
        range_=htf_range,
        bias=htf_bias,
        zones=[],
        imbalances=[],
        levels=[],
        liquidity_events=[],
        structure=[],
    )
    pic = MarketPicture(
        per_tf={
            TimeFrame.D1: d1_snap,
            TimeFrame.H4: h4_snap,
            TimeFrame.M15: m15_snap,
        },
        htf_bias=htf_bias,
        htf_range=htf_range,
        active_pois=active_pois or [],
        at_timestamp=h4_df.index[-1].to_pydatetime(),
        current_price=current_price,
    )
    # setup_builder ATR'yi H4 ham OHLCV'sinden okur (orchestrator/harness
    # ileride saglar; testte dogrudan baglariz).
    pic._h4_ohlcv = h4_df
    return pic


@pytest.fixture
def config():
    return SMCConfig()


# ============================================================
# Cikti sozlesmesi
# ============================================================


def test_returns_setup_or_none(config):
    pic = _picture(
        htf_range=_htf_range(),
        active_pois=[_poi_zone(_demand_zone())],
    )
    out = build(pic, config)
    assert out is None or isinstance(out, Setup)


def test_no_active_pois_returns_none(config):
    pic = _picture(active_pois=[])
    assert build(pic, config) is None


def test_neutral_bias_returns_none(config):
    """HTF bias NEUTRAL -> yon belirsiz -> setup yok."""
    pic = _picture(
        htf_bias=Bias.NEUTRAL,
        htf_range=_htf_range(),
        active_pois=[_poi_zone(_demand_zone())],
    )
    assert build(pic, config) is None


# ============================================================
# Yon
# ============================================================


def test_bullish_bias_long_setup(config):
    pic = _picture(
        htf_bias=Bias.BULLISH,
        htf_range=_htf_range(),
        active_pois=[_poi_zone(_demand_zone())],
    )
    s = build(pic, config)
    assert s is not None
    assert s.direction == Direction.LONG


def test_bearish_bias_short_setup(config):
    sz = _supply_zone()
    pic = _picture(
        htf_bias=Bias.BEARISH,
        htf_range=_htf_range(),
        active_pois=[_poi_zone(sz)],
        current_price=120.0,
    )
    s = build(pic, config)
    assert s is not None
    assert s.direction == Direction.SHORT


# ============================================================
# Confluence skoru — [0,1] araliginda, agirlikli
# ============================================================


def test_confluence_score_in_unit_range(config):
    pic = _picture(
        htf_range=_htf_range(),
        active_pois=[_poi_zone(_demand_zone())],
    )
    s = build(pic, config)
    assert s is not None
    assert 0.0 <= s.confluence_score <= 1.0


def test_fresh_zone_scores_higher_than_tested(config):
    """poi_quality faktoru: FRESH (1.0x) > TESTED (0.7x).

    Iki picture ayni — sadece zone status farkli. Ek confluence (level)
    eklenir ki TESTED zone da esigi gecebilsin; karsilastirma yine gecerli
    cunku poi_quality FRESH lehine.
    """
    level = Level(
        kind=LevelKind.WO, price=80.0, timeframe=TimeFrame.D1,
        valid_from=TS, valid_until=None,
    )
    fresh = _picture(
        htf_range=_htf_range(),
        active_pois=[_poi_zone(_demand_zone(status=ZoneStatus.FRESH))],
        h4_levels=[level],
    )
    tested = _picture(
        htf_range=_htf_range(),
        active_pois=[_poi_zone(_demand_zone(status=ZoneStatus.TESTED))],
        h4_levels=[level],
    )
    sf = build(fresh, config)
    st = build(tested, config)
    assert sf is not None and st is not None
    assert sf.confluence_score > st.confluence_score


def test_more_factors_higher_score(config):
    """Likidite + level + fvg + clustering eklenince skor artar."""
    dz = _demand_zone()
    bare = _picture(
        htf_range=_htf_range(),
        active_pois=[_poi_zone(dz)],
    )
    # Zenginlestirilmis: sweep + level cakismasi + FVG + ikinci POI (clustering)
    sweep = LiquidityEvent(
        kind=LiquidityKind.SWEEP,
        swept_price=92.0,
        direction=Direction.LONG,
        candle_ts=TS,
        reclaimed=True,
        significance=Significance.HIGH,
    )
    level = Level(
        kind=LevelKind.WO,
        price=93.5,
        timeframe=TimeFrame.D1,
        valid_from=TS,
        valid_until=None,
    )
    fvg = Imbalance(
        kind=ImbalanceKind.FVG,
        top=94.5,
        bottom=93.0,
        direction=Direction.LONG,
        timeframe=TimeFrame.H4,
        created_at=TS,
        filled=False,
        fill_ratio=0.0,
    )
    dz2 = _demand_zone(top=94.5, bottom=92.5)
    rich = _picture(
        htf_range=_htf_range(),
        active_pois=[_poi_zone(dz), _poi_zone(dz2)],
        h4_liquidity=[sweep],
        h4_levels=[level],
        h4_imbalances=[fvg],
    )
    sb = build(bare, config)
    sr = build(rich, config)
    assert sb is not None and sr is not None
    assert sr.confluence_score > sb.confluence_score


def test_missing_factors_zero_contribution_not_renormalized(config):
    """htf_range None -> premium_discount faktoru 0 katki; renormalize yok.

    Ayni POI, bir kez htf_range ile bir kez htf_range=None.
    htf_range'siz skor, htf_range'li skordan DUSUK olmali (renorm olsa esit
    olurdu) — ve fark tam olarak premium_discount agirligi kadar bir
    katkinin kaybi yonunde (somut esitlik degil, sadece <).
    """
    dz = _demand_zone()
    with_range = _picture(
        htf_range=_htf_range(),
        active_pois=[_poi_zone(dz)],
    )
    without_range = _picture(
        htf_range=None,
        active_pois=[_poi_zone(dz, htf_aligned=True)],
    )
    sw = build(with_range, config)
    swo = build(without_range, config)
    # without_range setup uretmeyebilir (skor esigin altina dusebilir);
    # ureitiyorsa skoru with_range'den dusuk olmali (renormalize edilmedi).
    if swo is not None and sw is not None:
        assert swo.confluence_score < sw.confluence_score


def test_deterministic(config):
    pic = _picture(
        htf_range=_htf_range(),
        active_pois=[_poi_zone(_demand_zone())],
    )
    a = build(pic, config)
    b = build(pic, config)
    assert a is not None and b is not None
    assert a.confluence_score == b.confluence_score
    assert a.entry == b.entry
    assert a.sl == b.sl
    assert a.tp == b.tp


# ============================================================
# Premium/discount faktoru — OTE
# ============================================================


def test_discount_demand_scores_premium_discount_factor(config):
    """Discount bolgedeki DEMAND zone (long) -> premium_discount faktoru >0."""
    # htf_range 70-130, eq=100. Zone 92-95 -> discount.
    pic_disc = _picture(
        htf_range=_htf_range(high=130.0, low=70.0),
        active_pois=[_poi_zone(_demand_zone(top=82.0, bottom=78.0))],
    )
    # Premium'da demand (kotumser): zone 115-118
    pic_prem = _picture(
        htf_range=_htf_range(high=130.0, low=70.0),
        active_pois=[_poi_zone(_demand_zone(top=118.0, bottom=115.0),
                               htf_aligned=False)],
        current_price=116.0,
    )
    sd = build(pic_disc, config)
    sp = build(pic_prem, config)
    assert sd is not None
    # discount setup premium setup'tan yuksek skorlu (premium/discount faktoru)
    if sp is not None:
        assert sd.confluence_score > sp.confluence_score


# ============================================================
# Entry / SL / TP merdiveni
# ============================================================


def test_entry_within_poi_band(config):
    dz = _demand_zone(top=82.0, bottom=78.0)
    pic = _picture(
        htf_range=_htf_range(),
        active_pois=[_poi_zone(dz)],
    )
    s = build(pic, config)
    assert s is not None
    assert dz.bottom <= s.entry <= dz.top


def test_sl_structural_below_poi_for_long(config):
    dz = _demand_zone(top=82.0, bottom=78.0)
    pic = _picture(
        htf_range=_htf_range(),
        active_pois=[_poi_zone(dz)],
    )
    s = build(pic, config)
    assert s is not None
    # LONG: SL POI'nin altinda (yapisal invalidation)
    assert s.sl < dz.bottom
    assert s.sl < s.entry


def test_sl_structural_above_poi_for_short(config):
    sz = _supply_zone(top=122.0, bottom=118.0)
    pic = _picture(
        htf_bias=Bias.BEARISH,
        htf_range=_htf_range(),
        active_pois=[_poi_zone(sz)],
        current_price=120.0,
    )
    s = build(pic, config)
    assert s is not None
    assert s.sl > sz.top
    assert s.sl > s.entry


def test_tp_ladder_three_levels(config):
    pic = _picture(
        htf_range=_htf_range(),
        active_pois=[_poi_zone(_demand_zone())],
    )
    s = build(pic, config)
    assert s is not None
    assert len(s.tp) == 3
    # LONG: TP'ler artan, hepsi entry'nin uzerinde
    assert s.tp[0] < s.tp[1] < s.tp[2]
    assert all(tp > s.entry for tp in s.tp)


def test_tp_weights_sum_to_one(config):
    pic = _picture(
        htf_range=_htf_range(),
        active_pois=[_poi_zone(_demand_zone())],
    )
    s = build(pic, config)
    assert s is not None
    assert len(s.tp_weights) == len(s.tp)
    assert sum(s.tp_weights) == pytest.approx(1.0)


def test_rr_computed_positive(config):
    pic = _picture(
        htf_range=_htf_range(),
        active_pois=[_poi_zone(_demand_zone())],
    )
    s = build(pic, config)
    assert s is not None
    # rr = (TP1 - entry) / (entry - SL) for LONG
    expected = (s.tp[0] - s.entry) / (s.entry - s.sl)
    assert s.rr == pytest.approx(expected)
    assert s.rr > 0


# ============================================================
# No-setup: dusuk confluence skoru
# ============================================================


def test_low_confluence_returns_none(config):
    """Skor < confluence_min_score -> None.

    Ciplak bir POI (likidite/level/fvg/clustering yok), htf_range yok ->
    cogu faktor 0 -> skor esigin altinda.
    """
    cfg = SMCConfig()
    cfg.confluence_min_score = 0.95  # cok yuksek esik -> kesin red
    pic = _picture(
        htf_range=_htf_range(),
        active_pois=[_poi_zone(_demand_zone(status=ZoneStatus.MITIGATED))],
    )
    assert build(pic, cfg) is None


# ============================================================
# No-setup: SL cok yakin
# ============================================================


def test_sl_too_close_returns_none(config):
    """SL mesafesi < sl_min_atr_multiple * ATR -> None.

    ATR'yi buyuk yapan bir df + dar bir POI bandi -> SL mesafesi kucuk kalir.
    """
    # Genis-ranged barlar -> buyuk ATR.
    rows = []
    for i in range(40):
        o = 100.0 + i * 0.1
        rows.append(_candle(o, o + 20.0, o - 20.0, o + 0.1))  # cok genis -> ATR buyuk
    big_atr_df = _df(rows)
    # Dar POI: 93.0-93.2 -> SL ona cok yakin olur
    dz = _demand_zone(top=93.2, bottom=93.0)
    cfg = SMCConfig()
    cfg.sl_min_atr_multiple = 0.5
    pic = _picture(
        htf_range=_htf_range(),
        active_pois=[_poi_zone(dz)],
        h4_df=big_atr_df,
        current_price=93.1,
    )
    assert build(pic, cfg) is None


# ============================================================
# Confirmation baglama — M15 StructureBreak
# ============================================================


def test_confirmation_bound_when_m15_structure_present(config):
    """M15 snapshot'inda yon uyumlu StructureBreak varsa Setup.confirmation."""
    sb = _structure_break(direction=Direction.LONG, kind=StructureKind.CHoCH)
    pic = _picture(
        htf_range=_htf_range(),
        active_pois=[_poi_zone(_demand_zone())],
        m15_structure=[sb],
    )
    s = build(pic, config)
    assert s is not None
    assert s.confirmation is sb


def test_confirmation_none_when_no_m15_structure(config):
    pic = _picture(
        htf_range=_htf_range(),
        active_pois=[_poi_zone(_demand_zone())],
        m15_structure=[],
    )
    s = build(pic, config)
    assert s is not None
    assert s.confirmation is None


def test_confirmation_ignores_opposite_direction(config):
    """Yon uyumsuz StructureBreak (SHORT) bullish setup'a baglanmaz."""
    sb_short = _structure_break(direction=Direction.SHORT)
    pic = _picture(
        htf_range=_htf_range(),
        active_pois=[_poi_zone(_demand_zone())],
        m15_structure=[sb_short],
    )
    s = build(pic, config)
    assert s is not None
    assert s.confirmation is None


# ============================================================
# Tek setup (v1) — en yuksek skorlu POI
# ============================================================


def test_single_setup_from_best_poi(config):
    """Birden fazla POI -> en yuksek confluence skorlu tek Setup."""
    # POI A: ciplak demand. POI B: ayni ama level cakismali (daha yuksek skor).
    dz_a = _demand_zone(top=95.0, bottom=92.0)
    dz_b = _demand_zone(top=99.0, bottom=96.0)
    level = Level(
        kind=LevelKind.WO, price=97.5, timeframe=TimeFrame.D1,
        valid_from=TS, valid_until=None,
    )
    pic = _picture(
        htf_range=_htf_range(),
        active_pois=[_poi_zone(dz_a), _poi_zone(dz_b)],
        h4_levels=[level],
        current_price=97.0,
    )
    s = build(pic, config)
    assert s is not None
    # B (level cakismali) secilmeli -> entry B bandinda
    assert dz_b.bottom <= s.entry <= dz_b.top


def test_setup_bias_context_and_poi_set(config):
    dz = _demand_zone()
    poi = _poi_zone(dz)
    pic = _picture(
        htf_range=_htf_range(),
        active_pois=[poi],
    )
    s = build(pic, config)
    assert s is not None
    assert s.bias_context == Bias.BULLISH
    assert s.poi is poi
    assert s.created_at == pic.at_timestamp


# ============================================================
# Faz 3 review fix #1 — ATR plumbing: picture.per_tf[H4].atr
# ============================================================


def _picture_with_atr(
    *,
    h4_atr=0.0,
    htf_bias=Bias.BULLISH,
    htf_range=None,
    active_pois=None,
    current_price=80.0,
):
    """MarketPicture — ATR dogrudan H4 TFSnapshot.atr alanindan gelir
    (duck-type _h4_ohlcv attribute YOK)."""
    h4_snap = TFSnapshot(
        range_=None, bias=htf_bias, zones=[], imbalances=[], levels=[],
        liquidity_events=[], structure=[], atr=h4_atr,
    )
    m15_snap = TFSnapshot(
        range_=None, bias=htf_bias, zones=[], imbalances=[], levels=[],
        liquidity_events=[], structure=[],
    )
    d1_snap = TFSnapshot(
        range_=htf_range, bias=htf_bias, zones=[], imbalances=[], levels=[],
        liquidity_events=[], structure=[],
    )
    return MarketPicture(
        per_tf={
            TimeFrame.D1: d1_snap,
            TimeFrame.H4: h4_snap,
            TimeFrame.M15: m15_snap,
        },
        htf_bias=htf_bias,
        htf_range=htf_range,
        active_pois=active_pois or [],
        at_timestamp=TS.to_pydatetime(),
        current_price=current_price,
    )


def test_setup_builder_reads_atr_from_h4_snapshot(config):
    """build() ATR'yi picture.per_tf[H4].atr'den okur — duck-type yok.

    Buyuk H4 ATR + dar POI bandi -> SL mesafesi cok kucuk kalir ->
    sl_min_atr_multiple guard'i tetiklenir -> None.
    """
    dz = _demand_zone(top=93.2, bottom=93.0)  # cok dar band
    pic = _picture_with_atr(
        h4_atr=50.0,  # buyuk ATR
        htf_range=_htf_range(),
        active_pois=[_poi_zone(dz)],
        current_price=93.1,
    )
    cfg = SMCConfig()
    cfg.sl_min_atr_multiple = 0.5
    # SL mesafesi ~ band buffer (kucuk) << 0.5 * 50 -> guard reddeder
    assert build(pic, cfg) is None


def test_setup_builder_atr_zero_skips_sl_guard(config):
    """H4 ATR 0 -> SL minimum-mesafe kontrolu atlanir (eski davranis korunur)."""
    dz = _demand_zone(top=82.0, bottom=78.0)
    pic = _picture_with_atr(
        h4_atr=0.0,
        htf_range=_htf_range(),
        active_pois=[_poi_zone(dz)],
    )
    s = build(pic, config)
    assert s is not None  # ATR 0 -> guard atlanir, setup uretilir


def test_setup_builder_no_duck_type_h4_ohlcv_attribute(config):
    """picture'a _h4_ohlcv baglanmasa da build() calisir (hack kaldirildi)."""
    dz = _demand_zone(top=82.0, bottom=78.0)
    pic = _picture_with_atr(
        h4_atr=1.0,
        htf_range=_htf_range(),
        active_pois=[_poi_zone(dz)],
    )
    assert not hasattr(pic, "_h4_ohlcv")
    s = build(pic, config)
    assert s is not None


def test_setup_builder_missing_h4_snapshot_skips_guard(config):
    """H4 snapshot hic yoksa ATR=0 kabul edilir -> guard atlanir, crash yok."""
    dz = _demand_zone(top=82.0, bottom=78.0)
    d1_snap = TFSnapshot(
        range_=_htf_range(), bias=Bias.BULLISH, zones=[], imbalances=[],
        levels=[], liquidity_events=[], structure=[],
    )
    pic = MarketPicture(
        per_tf={TimeFrame.D1: d1_snap},  # H4 yok
        htf_bias=Bias.BULLISH,
        htf_range=_htf_range(),
        active_pois=[_poi_zone(dz)],
        at_timestamp=TS.to_pydatetime(),
        current_price=80.0,
    )
    s = build(pic, config)
    assert s is not None


# ============================================================
# Faz 3 review fix #2 — tuning sabitleri config'ten okunuyor
# ============================================================


def test_tp_r_multiples_from_config(config):
    """config.tp_r_multiples degistirilince TP merdiveni degisir."""
    dz = _demand_zone(top=82.0, bottom=78.0)
    pic = _picture_with_atr(
        htf_range=_htf_range(), active_pois=[_poi_zone(dz)],
    )
    cfg = SMCConfig()
    cfg.tp_r_multiples = (1.0, 2.0, 3.0)
    s = build(pic, cfg)
    assert s is not None
    r = abs(s.entry - s.sl)
    assert s.tp[0] == pytest.approx(s.entry + 1.0 * r)
    assert s.tp[1] == pytest.approx(s.entry + 2.0 * r)
    assert s.tp[2] == pytest.approx(s.entry + 3.0 * r)


def test_tp_weights_from_config(config):
    dz = _demand_zone()
    pic = _picture_with_atr(
        htf_range=_htf_range(), active_pois=[_poi_zone(dz)],
    )
    cfg = SMCConfig()
    cfg.tp_weights = (0.7, 0.2, 0.1)
    s = build(pic, cfg)
    assert s is not None
    assert s.tp_weights == [0.7, 0.2, 0.1]


def test_sl_band_buffer_mult_from_config(config):
    """sl_band_buffer_mult buyutulunce SL POI'den daha uzaga gider."""
    dz = _demand_zone(top=82.0, bottom=78.0)  # width=4
    pic = _picture_with_atr(
        htf_range=_htf_range(), active_pois=[_poi_zone(dz)],
    )
    cfg_small = SMCConfig()
    cfg_small.sl_band_buffer_mult = 0.25
    cfg_small.sl_abs_buffer_pct = 0.0  # band buffer baskin olsun
    cfg_big = SMCConfig()
    cfg_big.sl_band_buffer_mult = 1.0
    cfg_big.sl_abs_buffer_pct = 0.0
    s_small = build(pic, cfg_small)
    s_big = build(pic, cfg_big)
    assert s_small is not None and s_big is not None
    # daha buyuk mult -> SL daha asagida (LONG)
    assert s_big.sl < s_small.sl


def test_poi_kind_quality_from_config(config):
    """config.poi_kind_quality degistirilince poi_quality faktoru degisir."""
    dz = _demand_zone(status=ZoneStatus.FRESH)
    pic = _picture_with_atr(
        htf_range=_htf_range(), active_pois=[_poi_zone(dz)],
    )
    cfg_hi = SMCConfig()
    cfg_hi.confluence_min_score = 0.0
    cfg_lo = SMCConfig()
    cfg_lo.confluence_min_score = 0.0
    cfg_lo.poi_kind_quality = {"ZONE": 0.1, "LEVEL": 0.6, "IMBALANCE": 0.5}
    s_hi = build(pic, cfg_hi)
    s_lo = build(pic, cfg_lo)
    assert s_hi is not None and s_lo is not None
    assert s_hi.confluence_score > s_lo.confluence_score


def test_zone_status_factor_from_config(config):
    """config.zone_status_factor degistirilince FRESH zone skoru duser."""
    dz = _demand_zone(status=ZoneStatus.FRESH)
    pic = _picture_with_atr(
        htf_range=_htf_range(), active_pois=[_poi_zone(dz)],
    )
    cfg_default = SMCConfig()
    cfg_default.confluence_min_score = 0.0
    cfg_penalized = SMCConfig()
    cfg_penalized.confluence_min_score = 0.0
    cfg_penalized.zone_status_factor = {
        "FRESH": 0.2, "TESTED": 0.7, "MITIGATED": 0.3, "BROKEN": 0.0,
    }
    s_def = build(pic, cfg_default)
    s_pen = build(pic, cfg_penalized)
    assert s_def is not None and s_pen is not None
    assert s_def.confluence_score > s_pen.confluence_score


def test_ote_band_from_config(config):
    """config.ote_low/ote_high OTE bonus bandini belirler."""
    # depth ~ 0.5'lik bir POI; default OTE (0.618-0.786) disinda kalir,
    # OTE bandini genisletince icine girer -> premium_discount faktoru artar.
    dz = _demand_zone(top=86.0, bottom=84.0)  # mid=85, range 70-130 -> frac=0.25
    pic = _picture_with_atr(
        htf_range=_htf_range(high=130.0, low=70.0),
        active_pois=[_poi_zone(dz)],
    )
    cfg_default = SMCConfig()
    cfg_wide = SMCConfig()
    cfg_wide.ote_low = 0.1
    cfg_wide.ote_high = 0.99
    s_def = build(pic, cfg_default)
    s_wide = build(pic, cfg_wide)
    assert s_def is not None and s_wide is not None
    assert s_wide.confluence_score >= s_def.confluence_score


def test_setup_builder_deterministic_with_config_tuning(config):
    """Ayni (picture, config) -> ayni Setup (tuning config'ten gelse de)."""
    dz = _demand_zone()
    pic = _picture_with_atr(
        h4_atr=2.0, htf_range=_htf_range(), active_pois=[_poi_zone(dz)],
    )
    cfg = SMCConfig()
    cfg.tp_r_multiples = (1.2, 2.0, 3.5)
    a = build(pic, cfg)
    b = build(pic, cfg)
    assert a is not None and b is not None
    assert a.entry == b.entry and a.sl == b.sl and a.tp == b.tp
    assert a.confluence_score == b.confluence_score
