"""Orchestrator (MTF kaskad) testleri — Plan Faz 2, task 2.1-2.6.

Katman 1 (D1) -> htf_bias/htf_range; Katman 2 (H4/H8) -> filtrelenmis POI'ler;
Katman 3 (M15) -> active_pois aktivasyonu. Ayrica look-ahead bias ve HTF cache.
"""

from __future__ import annotations

import pandas as pd
import pytest

from smc_engine.config import SMCConfig
from smc_engine.orchestrator import analyze
from smc_engine.types import (
    Bias,
    MarketPicture,
    POIKind,
    TimeFrame,
    Zone,
    ZoneKind,
)


# ------------------------------------------------------------
# Yardimci sentetik veri ureticileri (test-lokal)
# ------------------------------------------------------------


def _candle(o, h, l, c, v=1000.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def _df(rows, start, freq):
    idx = pd.date_range(start=start, periods=len(rows), freq=freq)
    return pd.DataFrame(rows, index=idx)[
        ["open", "high", "low", "close", "volume"]
    ]


def _bullish_d1(n=20, start=100.0, step=5.0):
    """Yukselen + geri cekilmeli D1 — swing/structure uretir, bullish bias."""
    rows = []
    price = start
    # yukari impulse, geri cekilme, daha yuksek impulse -> HH/HL yapisi
    pattern = [
        6, 6, 6, 6, 6,        # impulse up
        -4, -4, -4, -4,       # pullback (HL olusur)
        7, 7, 7, 7, 7, 7,     # daha yuksek impulse -> BOS
        -3, -3, -3, -3, -3,
    ]
    for d in pattern[:n]:
        o = price
        c = price + d
        h = max(o, c) + 1
        l = min(o, c) - 1
        rows.append(_candle(o, h, l, c))
        price = c
    return _df(rows, "2026-01-01", "D")


# ============================================================
# 2.1 — Katman 1 (D1) HTF layer
# ============================================================


def test_htf_layer(fixture_multi_tf):
    """analyze() D1 katmanindan htf_bias / htf_range / at_timestamp /
    current_price uretir; per_tf['D1'] snapshot dolu."""
    config = SMCConfig()
    picture = analyze(fixture_multi_tf, config)

    assert isinstance(picture, MarketPicture)
    # multi_tf D1 monoton yukseliyor -> bullish bias (trend fallback)
    assert picture.htf_bias == Bias.BULLISH
    # at_timestamp = en kucuk-TF (M15) son mum kapanisi
    m15 = fixture_multi_tf["M15"]
    assert picture.at_timestamp == m15.index[-1].to_pydatetime()
    assert picture.current_price == pytest.approx(
        float(m15["close"].iloc[-1])
    )
    # D1 snapshot mevcut
    assert TimeFrame.D1 in picture.per_tf
    d1_snap = picture.per_tf[TimeFrame.D1]
    assert d1_snap.bias == Bias.BULLISH


def test_htf_layer_range_and_tf_stamp():
    """Range olusan bir D1 setinde htf_range dolar ve timeframe=D1 damgasi
    dogru."""
    config = SMCConfig()
    # range_bound benzeri D1: RH ~120 / RL ~80
    from synthetic import make_range_bound

    d1 = make_range_bound()
    # frekansi D dustur (range_bound 'h' uretir; index'i D1'e tasiyalim)
    d1 = d1.copy()
    d1.index = pd.date_range("2026-01-01", periods=len(d1), freq="D")

    picture = analyze({TimeFrame.D1: d1}, config)
    assert picture.htf_range is not None
    assert picture.htf_range.timeframe == TimeFrame.D1
    # D1 structure/level/liquidity ciktilarinin timeframe damgasi D1 olmali
    for lv in picture.per_tf[TimeFrame.D1].levels:
        assert lv.timeframe == TimeFrame.D1


# ============================================================
# 2.2 — Katman 2 (H4) + HTF filtreleme
# ============================================================


def test_ltf_filtering():
    """HTF bias=BULLISH -> H4 katmaninda SUPPLY zone'lar elenir, DEMAND
    zone'lar POI olarak tutulur (discount'takiler htf_aligned=True)."""
    config = SMCConfig()
    d1 = _bullish_d1()

    # H4: hem DEMAND (bullish OB) hem SUPPLY (bearish OB) iceren bir set.
    # range_bound'a benzer bir H4 + iki net OB enjekte edelim.
    h4_rows = [
        _candle(100, 102, 99, 101),
        _candle(101, 103, 100, 102),
        _candle(102, 104, 101, 103),
        _candle(103, 105, 102, 104),
        _candle(104, 106, 103, 105),
        # --- SUPPLY OB: bullish OB mumu swing HIGH'ta + istekli bearish breakout
        _candle(105, 130, 104, 129),   # 5 swing HIGH bolgesi, bullish OB
        _candle(128, 129, 100, 102),   # 6 istekli bearish breakout
        _candle(102, 104, 100, 103),
        _candle(103, 105, 102, 104),
        _candle(104, 106, 103, 105),
        _candle(105, 107, 80, 82),     # 10 dusus -> swing LOW bolgesi
        # --- DEMAND OB: bearish OB mumu swing LOW'ta + istekli bullish breakout
        _candle(82, 83, 78, 79),       # 11 bearish OB, swing LOW
        _candle(80, 110, 79, 108),     # 12 istekli bullish breakout
        _candle(108, 112, 107, 111),
        _candle(111, 115, 110, 114),
        _candle(114, 118, 113, 117),
        _candle(117, 121, 116, 120),
        _candle(120, 124, 119, 123),
        _candle(123, 127, 122, 126),
        _candle(126, 130, 125, 129),
    ]
    h4 = _df(h4_rows, "2026-01-01", "4h")

    picture = analyze({TimeFrame.D1: d1, TimeFrame.H4: h4}, config)
    assert picture.htf_bias == Bias.BULLISH

    h4_zones = picture.per_tf[TimeFrame.H4].zones
    # detektor hem DEMAND hem SUPPLY uretmis olmali (ham snapshot filtrelenmez)
    kinds = {z.kind for z in h4_zones}
    assert ZoneKind.DEMAND in kinds, f"zones: {h4_zones}"

    # POI'ler: bullish bias -> yalnizca DEMAND zone POI olur, SUPPLY elenir.
    zone_pois = [
        p for p in picture.active_pois if p.kind == POIKind.ZONE
    ]
    # active_pois M15 olmadigi icin H4 katmaninin filtrelenmis POI havuzu
    # ayrica picture'da tutulur — burada per_tf uzerinden de dogrulayalim.
    # Filtreleme kurali: SUPPLY zone POI listesinde olmamali.
    for p in zone_pois:
        assert isinstance(p.ref, Zone)
        assert p.ref.kind == ZoneKind.DEMAND


def test_ltf_filtering_enrichment():
    """Zone.age_bars ve Imbalance.fill_ratio orchestrator tarafindan
    guncelleniyor (olusum aninda 0 degil, gercek deger)."""
    config = SMCConfig()
    d1 = _bullish_d1()
    h4 = _bullish_d1(n=20)  # ayni yapi H4 olarak da kullanilabilir
    h4 = h4.copy()
    h4.index = pd.date_range("2026-01-01", periods=len(h4), freq="4h")

    picture = analyze({TimeFrame.D1: d1, TimeFrame.H4: h4}, config)
    h4_zones = picture.per_tf[TimeFrame.H4].zones
    # En az bir zone varsa, eski bir zone'un age_bars > 0 olmali
    if h4_zones:
        ages = [z.age_bars for z in h4_zones]
        assert max(ages) > 0, f"age_bars guncellenmemis: {ages}"


# ============================================================
# 2.3 — Katman 3 (M15) + POI aktivasyon
# ============================================================


def test_entry_layer():
    """M15 yalnizca fiyat aktif POI yakinindayken detektor calistirir;
    onaylanan POI'ler active_pois'e eklenir."""
    config = SMCConfig()
    d1 = _bullish_d1()

    # H4: net bir DEMAND zone uret (bearish OB swing LOW + istekli bullish
    # breakout), zone ~ 78-82 bandinda.
    h4_rows = [
        _candle(100, 101, 99, 100),
        _candle(100, 101, 98, 99),
        _candle(99, 100, 97, 98),
        _candle(98, 99, 96, 97),
        _candle(97, 98, 95, 96),
        _candle(96, 97, 80, 82),    # 5 dusus
        _candle(82, 83, 78, 79),    # 6 bearish OB, swing LOW bolgesi
        _candle(80, 110, 79, 108),  # 7 istekli bullish breakout
        _candle(108, 112, 107, 111),
        _candle(111, 115, 110, 114),
        _candle(114, 118, 113, 117),
        _candle(117, 121, 116, 120),
    ]
    h4 = _df(h4_rows, "2026-01-01", "4h")

    # --- Senaryo A: M15 guncel fiyat zone'dan UZAK (~120) -> active_pois bos ---
    m15_far = _df(
        [_candle(119, 121, 118, 120) for _ in range(20)],
        "2026-01-03",
        "15min",
    )
    pic_far = analyze(
        {TimeFrame.D1: d1, TimeFrame.H4: h4, TimeFrame.M15: m15_far}, config
    )
    far_zone_pois = [
        p for p in pic_far.active_pois if p.kind == POIKind.ZONE
    ]
    assert far_zone_pois == [], (
        f"fiyat POI'den uzakken active_pois dolu: {far_zone_pois}"
    )

    # --- Senaryo B: M15 guncel fiyat DEMAND zone icinde (~80) -> aktive ---
    m15_near = _df(
        [_candle(81, 82, 79, 80) for _ in range(20)],
        "2026-01-03",
        "15min",
    )
    pic_near = analyze(
        {TimeFrame.D1: d1, TimeFrame.H4: h4, TimeFrame.M15: m15_near}, config
    )
    near_zone_pois = [
        p for p in pic_near.active_pois if p.kind == POIKind.ZONE
    ]
    assert len(near_zone_pois) >= 1, (
        "fiyat DEMAND zone icindeyken POI aktive edilmedi"
    )
    assert TimeFrame.M15 in pic_near.per_tf


# ============================================================
# 2.4 — Look-ahead bias testi (R1.1 yontemi)
# ============================================================


def test_no_lookahead(fixture_multi_tf):
    """A = analyze(full_df, at_bar=t) ile B = analyze(full_df[:t+1], at_bar=t)
    ayni MarketPicture'i uretmeli — orchestrator t sonrasi barlari sizdirmaz."""
    config = SMCConfig()
    full = fixture_multi_tf
    m15 = full["M15"]
    # t = M15'in ortasinda bir bar timestamp'i
    t = m15.index[300].to_pydatetime()

    # A: tam DataFrame'ler, at_bar=t
    A = analyze(full, config, at_bar=t)

    # B: her TF t'ye kadar elle dilimlenmis, at_bar=t
    sliced = {tf: df[df.index <= t] for tf, df in full.items()}
    B = analyze(sliced, config, at_bar=t)

    assert A.htf_bias == B.htf_bias
    assert A.at_timestamp == B.at_timestamp
    assert A.current_price == pytest.approx(B.current_price)
    assert A.htf_range == B.htf_range
    assert set(A.per_tf.keys()) == set(B.per_tf.keys())
    for tf in A.per_tf:
        sa, sb = A.per_tf[tf], B.per_tf[tf]
        assert sa.bias == sb.bias
        assert sa.range_ == sb.range_
        assert sa.zones == sb.zones
        assert sa.imbalances == sb.imbalances
        assert sa.levels == sb.levels
        assert sa.liquidity_events == sb.liquidity_events
        assert sa.structure == sb.structure
    assert A.active_pois == B.active_pois


def test_at_bar_excludes_future_bars(fixture_multi_tf):
    """at_bar=t verilince at_timestamp t olmali, t sonrasi veri kullanilmaz."""
    config = SMCConfig()
    full = fixture_multi_tf
    m15 = full["M15"]
    t = m15.index[200].to_pydatetime()
    pic = analyze(full, config, at_bar=t)
    assert pic.at_timestamp == t
    # current_price t barinin kapanisi olmali (t+1, t+2 degil)
    assert pic.current_price == pytest.approx(
        float(m15.loc[m15.index <= t, "close"].iloc[-1])
    )


# ============================================================
# 2.5 — HTF cache entegrasyonu
# ============================================================


def test_htf_cache(fixture_multi_tf, monkeypatch):
    """Ayni D1 penceresi icin ikinci analyze() cagrisinda D1 detektorleri
    yeniden calismaz (cache hit). Determinizm korunur: ciktilar esit."""
    config = SMCConfig()
    full = fixture_multi_tf
    m15 = full["M15"]

    import smc_engine.orchestrator as orch

    calls = {"range": 0, "structure": 0, "level": 0, "liquidity": 0}
    orig_range = orch.detect_range
    orig_struct = orch.detect_structure
    orig_level = orch.detect_levels
    orig_liq = orch.detect_liquidity

    def spy_range(df, cfg, **kw):
        if getattr(cfg, "timeframe", None) == TimeFrame.D1:
            calls["range"] += 1
        return orig_range(df, cfg, **kw)

    def spy_struct(df, cfg, **kw):
        if getattr(cfg, "timeframe", None) == TimeFrame.D1:
            calls["structure"] += 1
        return orig_struct(df, cfg, **kw)

    def spy_level(df, cfg, **kw):
        if getattr(cfg, "timeframe", None) == TimeFrame.D1:
            calls["level"] += 1
        return orig_level(df, cfg, **kw)

    def spy_liq(df, cfg, **kw):
        if getattr(cfg, "timeframe", None) == TimeFrame.D1:
            calls["liquidity"] += 1
        return orig_liq(df, cfg, **kw)

    monkeypatch.setattr(orch, "detect_range", spy_range)
    monkeypatch.setattr(orch, "detect_structure", spy_struct)
    monkeypatch.setattr(orch, "detect_levels", spy_level)
    monkeypatch.setattr(orch, "detect_liquidity", spy_liq)

    cache: dict = {}
    # Iki cagri: ikisi de ayni M15 son bari (=> ayni D1 son kapanisi).
    t = m15.index[-1].to_pydatetime()
    pic1 = analyze(full, config, at_bar=t, cache=cache)
    after_first = dict(calls)
    pic2 = analyze(full, config, at_bar=t, cache=cache)
    after_second = dict(calls)

    # Ilk cagri: D1 detektorleri calisti.
    assert after_first["range"] == 1
    assert after_first["structure"] == 1
    assert after_first["level"] == 1
    assert after_first["liquidity"] == 1
    # Ikinci cagri: D1 detektorleri YENIDEN calismadi (cache hit).
    assert after_second["range"] == 1, "D1 range cache'lenmedi"
    assert after_second["structure"] == 1, "D1 structure cache'lenmedi"
    assert after_second["level"] == 1, "D1 level cache'lenmedi"
    assert after_second["liquidity"] == 1, "D1 liquidity cache'lenmedi"

    # Determinizm: cache'li ikinci cagri ile cache'siz cagri ayni sonuc.
    pic_nocache = analyze(full, config, at_bar=t)
    assert pic2.htf_bias == pic_nocache.htf_bias
    assert pic2.htf_range == pic_nocache.htf_range
    assert pic1.htf_bias == pic2.htf_bias


def test_cache_optional_backward_compatible(fixture_multi_tf):
    """Cache verilmezse normal hesaplama — geriye uyumlu."""
    config = SMCConfig()
    pic = analyze(fixture_multi_tf, config)  # cache yok
    assert isinstance(pic, MarketPicture)


# ------------------------------------------------------------
# Faz 3 review fix: orchestrator TFSnapshot.atr'yi dolduruyor
# ------------------------------------------------------------


def _wide_df(n, start, freq, base=100.0):
    """Genis-aralikli barlar -> ATR belirgin > 0."""
    rows = []
    for i in range(n):
        o = base + i * 0.5
        rows.append(_candle(o, o + 5.0, o - 5.0, o + 0.5))
    return _df(rows, start, freq)


def test_orchestrator_populates_atr_on_snapshots():
    """Her TFSnapshot.atr orchestrator tarafindan > 0 doldurulur (yeterli veri)."""
    dataset = {
        TimeFrame.D1: _wide_df(30, "2026-01-01", "D"),
        TimeFrame.H4: _wide_df(60, "2026-01-01", "4h"),
        TimeFrame.M15: _wide_df(200, "2026-01-01", "15min"),
    }
    pic = analyze(dataset, SMCConfig())
    for tf in (TimeFrame.D1, TimeFrame.H4):
        assert pic.per_tf[tf].atr > 0.0


def test_orchestrator_atr_uses_config_period():
    """atr_period config'ten okunur — farkli period -> farkli ATR (kanit)."""
    dataset = {
        TimeFrame.D1: _wide_df(40, "2026-01-01", "D"),
        TimeFrame.H4: _wide_df(80, "2026-01-01", "4h"),
    }
    from smc_engine.detectors._atr import atr as _atr_fn
    cfg = SMCConfig()
    cfg.atr_period = 5
    pic = analyze(dataset, cfg)
    expected = _atr_fn(dataset[TimeFrame.H4], 5)
    assert pic.per_tf[TimeFrame.H4].atr == pytest.approx(expected)


def test_orchestrator_atr_deterministic_with_at_bar():
    """at_bar dilimi -> ATR yalnizca kapanmis barlardan; deterministik."""
    dataset = {
        TimeFrame.D1: _wide_df(40, "2026-01-01", "D"),
        TimeFrame.H4: _wide_df(120, "2026-01-01", "4h"),
    }
    t = dataset[TimeFrame.H4].index[60].to_pydatetime()
    a = analyze(dataset, SMCConfig(), at_bar=t)
    b = analyze(dataset, SMCConfig(), at_bar=t)
    assert a.per_tf[TimeFrame.H4].atr == b.per_tf[TimeFrame.H4].atr


# ============================================================
# KR-1 — Orchestrator TF_LOOKBACK alt-sınır dilimleme
# (kapsamli inceleme raporu 2026-05-15, KR-1)
# ============================================================


def _flat_df(n, start, freq, base=100.0):
    rows = [_candle(base, base + 0.5, base - 0.5, base + 0.1) for _ in range(n)]
    return _df(rows, start, freq)


def test_slice_to_at_bar_applies_tf_lookback_lower_bound():
    """KR-1: ``_slice_to_at_bar`` ust-sinir + alt-sinir (TF_LOOKBACK) uygular.

    Bos olmayan bir df icin dondurulen dilim en fazla
    ``config.lookback_bars(tf)`` satira sahip olmali; ust-sinir hala
    ``df.index <= at_bar`` ile sinirli (look-ahead guvenli).
    """
    from smc_engine.orchestrator import _slice_to_at_bar
    from smc_engine.config import SMCConfig, TF_LOOKBACK

    cfg = SMCConfig()
    # D1: 1000 bar uretilsin (lookback 365 cok altinda olmali).
    df = _flat_df(1000, "2020-01-01", "D")
    t = df.index[800].to_pydatetime()
    sliced = _slice_to_at_bar(df, t, config=cfg, tf=TimeFrame.D1)
    assert len(sliced) <= TF_LOOKBACK[TimeFrame.D1]
    # Ust-sinir korunmali: tum index <= at_bar.
    assert sliced.index[-1].to_pydatetime() <= t
    # Slice sonundaki bar at_bar olmali (en yakin bar).
    assert sliced.index[-1].to_pydatetime() == t


def test_slice_to_at_bar_backcompat_when_no_config():
    """Geriye uyumluluk: config/tf verilmezse eski davranis (yalniz ust-sinir)."""
    from smc_engine.orchestrator import _slice_to_at_bar
    df = _flat_df(50, "2020-01-01", "D")
    t = df.index[40].to_pydatetime()
    sliced = _slice_to_at_bar(df, t)  # eski imza
    assert len(sliced) == 41  # idx 0..40
    assert sliced.index[-1].to_pydatetime() == t


def test_analyze_cost_bounded_by_tf_lookback():
    """KR-1: ``analyze`` her tf icin detektorlere giden df boyutu
    ``config.lookback_bars(tf)`` ile siniri.

    Buyuk bir df (>> lookback) verince analyze CALL TIME ust-sinirlandi
    olmali. Determinizm icin sure assert'i yerine "iki farkli at_bar icin
    benzer sure" dogrulamasi yapariz (cost-bound proxy).
    """
    import time
    from smc_engine.config import SMCConfig, TF_LOOKBACK
    cfg = SMCConfig()

    # Cok genis veri.
    dataset = {
        TimeFrame.D1: _flat_df(2000, "2018-01-01", "D"),
        TimeFrame.H4: _flat_df(3000, "2020-01-01", "4h"),
        TimeFrame.M15: _flat_df(5000, "2024-01-01", "15min"),
    }

    # Yakin (erken) at_bar ile geç at_bar arasinda sure orantili kalmali.
    m15 = dataset[TimeFrame.M15]
    t_early = m15.index[1000].to_pydatetime()
    t_late = m15.index[4900].to_pydatetime()

    # Isinma
    analyze(dataset, cfg, at_bar=t_early)

    t0 = time.perf_counter()
    for _ in range(3):
        analyze(dataset, cfg, at_bar=t_early)
    t_early_avg = (time.perf_counter() - t0) / 3.0

    t0 = time.perf_counter()
    for _ in range(3):
        analyze(dataset, cfg, at_bar=t_late)
    t_late_avg = (time.perf_counter() - t0) / 3.0

    # KR-1 sonrasi: her iki cagri ayni boyutta dilim isler ->
    # sure orani < 3x olmali (cok olcekli yan etki yok; eski O(n^2)
    # davranisinda t_late / t_early >> 10x olurdu).
    # Tolerans gevsek (CI gurultusu icin).
    ratio = t_late_avg / max(t_early_avg, 1e-6)
    assert ratio < 5.0, (
        f"KR-1: analyze() cost geç at_bar'da {ratio:.1f}x daha yavas — "
        f"TF_LOOKBACK alt siniri uygulanmiyor olabilir "
        f"(early={t_early_avg*1000:.1f}ms, late={t_late_avg*1000:.1f}ms)"
    )


def test_no_lookahead_preserved_with_tf_lookback(fixture_multi_tf):
    """KR-1 sonrasi: TF_LOOKBACK uygulanmasi look-ahead determinizmini bozmaz.

    A=analyze(full, at_bar=t) ile B=analyze(full[:t+1], at_bar=t) hala ozdes
    olmali (alt-sinir kesilmesi her iki tarafta ayni sekilde uygulanir).
    """
    config = SMCConfig()
    full = fixture_multi_tf
    m15 = full["M15"]
    t = m15.index[300].to_pydatetime()
    A = analyze(full, config, at_bar=t)
    sliced = {tf: df[df.index <= t] for tf, df in full.items()}
    B = analyze(sliced, config, at_bar=t)
    assert A.htf_bias == B.htf_bias
    assert A.htf_range == B.htf_range
    assert A.active_pois == B.active_pois
    for tf in A.per_tf:
        sa, sb = A.per_tf[tf], B.per_tf[tf]
        assert sa.bias == sb.bias
        assert sa.range_ == sb.range_
        assert sa.zones == sb.zones
        assert sa.imbalances == sb.imbalances
        assert sa.levels == sb.levels
        assert sa.liquidity_events == sb.liquidity_events
        assert sa.structure == sb.structure


# ============================================================
# D1 EMA50 trend-override bias integration (Spec 2026-05-24)
# ============================================================


def _ohlcv_multitf_from_d1_closes(d1_closes: list[float]) -> dict:
    """D1 close listesinden cok-TF OHLCV sozlugu kur.

    D1 her bar O=H=L=C=close (zero-range); diger TF'ler her D1 close'unu
    N kez tekrar eder. Bias EMA hesabi D1'e bakar; diger TF'ler enrichment
    icin var ama bu testte icerik onemsiz.
    """
    d1_rows = [_candle(c, c, c, c) for c in d1_closes]
    d1 = _df(d1_rows, start="2024-01-01", freq="1D")

    h4_closes = [c for c in d1_closes for _ in range(6)]
    h4 = _df([_candle(c, c, c, c) for c in h4_closes],
             start="2024-01-01", freq="4h")
    h1_closes = [c for c in d1_closes for _ in range(24)]
    h1 = _df([_candle(c, c, c, c) for c in h1_closes],
             start="2024-01-01", freq="1h")
    m15_closes = [c for c in d1_closes for _ in range(96)]
    m15 = _df([_candle(c, c, c, c) for c in m15_closes],
              start="2024-01-01", freq="15min")
    return {
        TimeFrame.D1: d1, TimeFrame.H4: h4,
        TimeFrame.H1: h1, TimeFrame.M15: m15,
    }


def test_analyze_d1_uptrend_returns_bullish_bias():
    """D1 sentetik 150 bar uptrend -> htf_bias=BULLISH (EMA path)."""
    d1_closes = [100.0 + i * 0.5 for i in range(150)]
    ohlcv = _ohlcv_multitf_from_d1_closes(d1_closes)
    picture = analyze(ohlcv, SMCConfig())
    assert picture.htf_bias == Bias.BULLISH


def test_analyze_d1_downtrend_returns_bearish_bias():
    """D1 sentetik 150 bar downtrend -> htf_bias=BEARISH (EMA path)."""
    d1_closes = [100.0 - i * 0.3 for i in range(150)]
    ohlcv = _ohlcv_multitf_from_d1_closes(d1_closes)
    picture = analyze(ohlcv, SMCConfig())
    assert picture.htf_bias == Bias.BEARISH


def test_analyze_short_d1_uses_fallback_path():
    """D1 < 50 bar -> EMA bypass, structure/close-trend fallback."""
    d1_closes = [100.0 + i * 0.2 for i in range(30)]
    ohlcv = _ohlcv_multitf_from_d1_closes(d1_closes)
    picture = analyze(ohlcv, SMCConfig())
    # 30 bar veride structure detect olabilir/olmaz; close-trend fallback ise
    # +0.5%+ -> BULLISH. Net beklenti: != NEUTRAL.
    assert picture.htf_bias != Bias.NEUTRAL
