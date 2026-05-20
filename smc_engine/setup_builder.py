"""SMC Engine setup_builder — confluence skorlama + entry/SL/TP — Spec §7.

``build(picture: MarketPicture, config) -> Setup | None``

Iki kademe (Spec §7):
  - **Hard gate'ler** BURADA DEGIL — risk_guard'da (HTF bias uyumu, M15
    CHoCH/BOS zorunlulugu, min_rr, regime vb.).
  - **Agirlikli confluence skoru** BURADA — gecerli aday Setup'lari siralar.

setup_builder yalnizca iki "uretilebilirlik" kontrolu yapar (bunlar gate degil,
Setup'in matematiksel olarak insa edilemedigi durumlar):
  1. ``confluence_score < config.confluence_min_score`` -> ``None``
  2. SL mesafesi ``< config.sl_min_atr_multiple * ATR`` -> ``None``
     (SL entry'ye cok yakin = anlamsiz setup)

Yon: ``htf_bias`` BULLISH -> LONG, BEARISH -> SHORT, NEUTRAL -> ``None``.

**Tek setup (v1):** ``active_pois`` birden fazla olabilir; v1 en yuksek
confluence skorlu tek POI'den tek ``Setup`` uretir (coklu eszamanli setup v2).

**Confluence — 6 faktor** (Spec §7 tablosu, agirliklar ``config.confluence_weights``):
  poi_quality(0.25) · premium_discount(0.20) · liquidity_context(0.20) ·
  level_confluence(0.15) · fvg_imbalance(0.10) · clustering(0.10)
Her faktor [0,1]; agirlikli toplam [0,1] (agirliklar toplami=1.0).
**Eksik faktor -> 0 katki, agirliklar renormalize EDILMEZ** (skorlar setup'lar
arasi karsilastirilabilir kalsin — Plan Faz 3 tasarim karari #2).

``build()`` saf/deterministik: ayni (picture, config) -> ayni Setup|None.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from smc_engine.types import (
    Bias,
    Direction,
    Imbalance,
    Level,
    MarketPicture,
    POIKind,
    POIRef,
    Range,
    Setup,
    StructureBreak,
    TFSnapshot,
    TimeFrame,
    Zone,
    ZoneKind,
)

# Tuning sabitleri artik SMCConfig'te (ratchet optimize edebilsin). Asagidaki
# modul-seviyesi degerler yalnizca FALLBACK — config'te alan yoksa kullanilir
# (eski testler / harici cagiranlar kirilmasin diye getattr ile okunur).

# --- POI kalitesi: tip onceligi (Spec §7 "OB-swing > breaker > imbalance") ---
_DEFAULT_POI_KIND_QUALITY: dict[str, float] = {
    "ZONE": 1.0,
    "LEVEL": 0.6,
    "IMBALANCE": 0.5,
}

# --- ZoneStatus carpani (Spec §5.2) ---
_DEFAULT_ZONE_STATUS_FACTOR: dict[str, float] = {
    "FRESH": 1.0,
    "TESTED": 0.7,
    "MITIGATED": 0.3,
    "BROKEN": 0.0,
}

# --- TP merdiveni: her dilimin pozisyon agirligi (toplam=1.0) ---
_DEFAULT_TP_WEIGHTS = (0.5, 0.3, 0.2)

# --- TP merdiveni Fib extension carpanlari (entry-SL "1R" uzerinden) ---
_DEFAULT_TP_R_MULTIPLES = (1.5, 2.62, 4.23)

# --- SL: POI bandinin disina yapisal tampon (band genisliginin orani) ---
_DEFAULT_SL_BAND_BUFFER_MULT = 0.25
_DEFAULT_SL_ABS_BUFFER_PCT = 0.003


# Bug B (2026-05-18): float bolme TP1/entry/SL'den 1.4999999999999822 gibi
# noise uretebilir; min_rr=1.5 gate'inde "1.500" gozukse de < olur ve reject
# edilir. Quantize ondalik 4'e (0.0001 cozunurluk pratik yeterli) — defense-in
# -depth icin risk_guard'da ayri epsilon tolerance var.
_RR_QUANTIZE_DIGITS = 4


def _compute_rr(entry: float, sl_distance: float, tp1: float, direction) -> float:
    """TP1/entry/SL'den R:R hesabi — float noise'a karsi 4 ondalikta quantize."""
    if direction == Direction.LONG:
        raw = (tp1 - entry) / sl_distance
    else:
        raw = (entry - tp1) / sl_distance
    return round(raw, _RR_QUANTIZE_DIGITS)

# --- OTE (Optimal Trade Entry) bolgesi: discount/premium icinde 0.618-0.786 ---
_DEFAULT_OTE_LOW = 0.618
_DEFAULT_OTE_HIGH = 0.786

# --- Clustering: ayni fiyat bolgesi toleransi (referans fiyatin orani) ---
_DEFAULT_CLUSTER_TOLERANCE_PCT = 0.02


# ============================================================
# Config tuning erisimi — alan yoksa modul-seviyesi fallback
# ============================================================


def _cfg_tp_r_multiples(config) -> tuple:
    return tuple(getattr(config, "tp_r_multiples", _DEFAULT_TP_R_MULTIPLES))


def _cfg_tp_weights(config) -> tuple:
    return tuple(getattr(config, "tp_weights", _DEFAULT_TP_WEIGHTS))


def _cfg_ote_low(config) -> float:
    return getattr(config, "ote_low", _DEFAULT_OTE_LOW)


def _cfg_ote_high(config) -> float:
    return getattr(config, "ote_high", _DEFAULT_OTE_HIGH)


def _cfg_sl_band_buffer_mult(config) -> float:
    return getattr(config, "sl_band_buffer_mult", _DEFAULT_SL_BAND_BUFFER_MULT)


def _cfg_sl_abs_buffer_pct(config) -> float:
    return getattr(config, "sl_abs_buffer_pct", _DEFAULT_SL_ABS_BUFFER_PCT)


def _cfg_cluster_tol_pct(config) -> float:
    return getattr(
        config, "cluster_tolerance_pct", _DEFAULT_CLUSTER_TOLERANCE_PCT
    )


def _cfg_poi_kind_quality(config, kind: POIKind) -> float:
    table = getattr(config, "poi_kind_quality", _DEFAULT_POI_KIND_QUALITY)
    return table.get(kind.name, 0.5)


def _cfg_zone_status_factor(config, status_name: str) -> float:
    table = getattr(config, "zone_status_factor", _DEFAULT_ZONE_STATUS_FACTOR)
    return table.get(status_name, 1.0)


# ============================================================
# POI yardimcilari
# ============================================================


def _poi_band(poi: POIRef) -> Optional[tuple[float, float]]:
    """POI'nin fiyat bandi ``(low, high)``. Level -> tek nokta ``(p, p)``."""
    ref = poi.ref
    if isinstance(ref, (Zone, Imbalance)):
        return (float(ref.bottom), float(ref.top))
    if isinstance(ref, Level):
        return (float(ref.price), float(ref.price))
    return None


def _poi_mid(poi: POIRef) -> Optional[float]:
    band = _poi_band(poi)
    if band is None:
        return None
    return (band[0] + band[1]) / 2.0


def _poi_direction_aligned(poi: POIRef, direction: Direction) -> bool:
    """POI yon-uyumlu mu? LONG -> DEMAND zone / LONG imbalance; Level her zaman."""
    ref = poi.ref
    if isinstance(ref, Zone):
        if direction == Direction.LONG:
            return ref.kind == ZoneKind.DEMAND
        return ref.kind == ZoneKind.SUPPLY
    if isinstance(ref, Imbalance):
        return ref.direction == direction
    if isinstance(ref, Level):
        return True
    return False


# ============================================================
# Confluence faktorleri — her biri [0,1]
# ============================================================


def _factor_poi_quality(poi: POIRef, config) -> float:
    """POI kalitesi: tip onceligi x ZoneStatus tazeligi (Spec §7 + §5.2)."""
    base = _cfg_poi_kind_quality(config, poi.kind)
    if isinstance(poi.ref, Zone):
        base *= _cfg_zone_status_factor(config, poi.ref.status.name)
    return max(0.0, min(1.0, base))


def _factor_premium_discount(
    poi: POIRef, direction: Direction, htf_range: Optional[Range], config
) -> float:
    """Premium/discount + OTE bonusu (Spec §7).

    LONG: POI discount'ta olmali; SHORT: premium'da. OTE bolgesi
    (0.618-0.786 derinlik) ekstra bonus.

    htf_range None -> faktor degerlendirilemez -> 0 katki (renormalize yok).
    """
    if htf_range is None:
        return 0.0
    mid = _poi_mid(poi)
    if mid is None:
        return 0.0
    span = htf_range.high - htf_range.low
    if span <= 0:
        return 0.0
    # frac: 0 = range low, 1 = range high.
    frac = (mid - htf_range.low) / span
    frac = max(0.0, min(1.0, frac))
    if direction == Direction.LONG:
        # discount = alt yari; ne kadar derin o kadar iyi.
        if frac > 0.5:
            return 0.0  # premium'da long -> bu faktor 0
        depth = (0.5 - frac) / 0.5  # 0..1 (range low'a yakinlik)
    else:  # SHORT
        if frac < 0.5:
            return 0.0  # discount'ta short -> 0
        depth = (frac - 0.5) / 0.5  # 0..1 (range high'a yakinlik)
    # Dogru yarida olmak baz skor saglar (0.6); OTE bolgesi (0.618-0.786
    # derinlik) tam skor; aradakiler baz ile tam arasinda lineer.
    if _cfg_ote_low(config) <= depth <= _cfg_ote_high(config):
        score = 1.0
    else:
        score = 0.6 + 0.4 * depth
    return max(0.0, min(1.0, score))


def _factor_liquidity_context(
    poi: POIRef, direction: Direction, h4_snap: Optional[TFSnapshot], config
) -> float:
    """POI'ye yon-uyumlu sweep/SFP geldi mi? (likidite alindi mi — Spec §7)."""
    if h4_snap is None or not h4_snap.liquidity_events:
        return 0.0
    band = _poi_band(poi)
    if band is None:
        return 0.0
    lo, hi = band
    width = hi - lo
    tol = max(width, abs((lo + hi) / 2.0) * _cfg_cluster_tol_pct(config))
    best = 0.0
    for ev in h4_snap.liquidity_events:
        if ev.direction != direction:
            continue
        # sweep fiyati POI bandina (tolerans ile) yakin mi?
        if (lo - tol) <= ev.swept_price <= (hi + tol):
            # HIGH onem + reclaimed -> tam skor; aksi halde kismi.
            s = 1.0 if ev.significance.name == "HIGH" else 0.6
            # reclaimed -> sinyal guclu, s aynen kalir; degilse zayiflat.
            if not ev.reclaimed:
                s *= 0.7
            best = max(best, s)
    return max(0.0, min(1.0, best))


def _factor_level_confluence(
    poi: POIRef,
    h4_snap: Optional[TFSnapshot],
    d1_snap: Optional[TFSnapshot],
    config,
) -> float:
    """POI bir kurumsal Level ile ortusuyor mu? (MO/WO/DO vb. — Spec §7)."""
    band = _poi_band(poi)
    if band is None:
        return 0.0
    lo, hi = band
    width = hi - lo
    ref_price = (lo + hi) / 2.0
    tol = max(width, abs(ref_price) * _cfg_cluster_tol_pct(config))
    levels: list[Level] = []
    if h4_snap is not None:
        levels.extend(h4_snap.levels)
    if d1_snap is not None:
        levels.extend(d1_snap.levels)
    # POI'nin kendisi Level ise: kendisiyle ortusme sayilmaz; baska Level ara.
    self_level = poi.ref if isinstance(poi.ref, Level) else None
    for lv in levels:
        if lv is self_level:
            continue
        if (lo - tol) <= lv.price <= (hi + tol):
            return 1.0
    return 0.0


def _factor_fvg_imbalance(
    poi: POIRef, direction: Direction, h4_snap: Optional[TFSnapshot], config
) -> float:
    """Yon-uyumlu, dolmamis bir FVG/imbalance POI yakininda mi? (Spec §7)."""
    if h4_snap is None or not h4_snap.imbalances:
        return 0.0
    band = _poi_band(poi)
    if band is None:
        return 0.0
    lo, hi = band
    width = hi - lo
    ref_price = (lo + hi) / 2.0
    tol = max(width, abs(ref_price) * _cfg_cluster_tol_pct(config))
    best = 0.0
    for imb in h4_snap.imbalances:
        if imb.direction != direction:
            continue
        imb_mid = (imb.top + imb.bottom) / 2.0
        if (lo - tol) <= imb_mid <= (hi + tol):
            # taze (dolmamis) imbalance daha cazip.
            s = 1.0 - max(0.0, min(1.0, imb.fill_ratio))
            best = max(best, s)
    return max(0.0, min(1.0, best))


def _factor_clustering(
    poi: POIRef, all_pois: list[POIRef], h4_snap: Optional[TFSnapshot], config
) -> float:
    """Ayni fiyat bolgesinde birden fazla POI/level birikmis mi? (Spec §7)."""
    mid = _poi_mid(poi)
    if mid is None:
        return 0.0
    tol = abs(mid) * _cfg_cluster_tol_pct(config)
    count = 0
    for other in all_pois:
        if other is poi:
            continue
        omid = _poi_mid(other)
        if omid is None:
            continue
        if abs(omid - mid) <= tol:
            count += 1
    # ek olarak h4 levels de cluster'a katki saglar.
    if h4_snap is not None:
        for lv in h4_snap.levels:
            if isinstance(poi.ref, Level) and lv is poi.ref:
                continue
            if abs(lv.price - mid) <= tol:
                count += 1
    # 0 ek -> 0.0; 1 ek -> 0.6; 2+ ek -> 1.0.
    if count <= 0:
        return 0.0
    if count == 1:
        return 0.6
    return 1.0


def _confluence_factors(
    poi: POIRef,
    direction: Direction,
    picture: MarketPicture,
    config,
) -> list[float]:
    """6 confluence faktorunu [0,1] degerleriyle hesapla (sirali liste).

    Sira: poi_quality, premium_discount, liquidity_context, level_confluence,
    fvg_imbalance, clustering. Eksik/uygulanamaz faktor -> 0.0.
    """
    h4_snap = picture.per_tf.get(TimeFrame.H4)
    d1_snap = picture.per_tf.get(TimeFrame.D1)
    return [
        _factor_poi_quality(poi, config),
        _factor_premium_discount(poi, direction, picture.htf_range, config),
        _factor_liquidity_context(poi, direction, h4_snap, config),
        _factor_level_confluence(poi, h4_snap, d1_snap, config),
        _factor_fvg_imbalance(poi, direction, h4_snap, config),
        _factor_clustering(poi, picture.active_pois, h4_snap, config),
    ]


def _score_from_factors(factors: list[float], config) -> float:
    """Faktor listesinden agirlikli [0,1] confluence skoru.

    Eksik faktor -> 0 katki; agirliklar renormalize EDILMEZ.
    """
    w = config.confluence_weights
    f_poi, f_pd, f_liq, f_lvl, f_fvg, f_clust = factors
    score = (
        w.poi_quality * f_poi
        + w.premium_discount * f_pd
        + w.liquidity_context * f_liq
        + w.level_confluence * f_lvl
        + w.fvg_imbalance * f_fvg
        + w.clustering * f_clust
    )
    return max(0.0, min(1.0, score))


def _confluence_score(
    poi: POIRef,
    direction: Direction,
    picture: MarketPicture,
    config,
) -> float:
    """6 faktorun agirlikli toplami -> [0,1] confluence skoru.

    Eksik faktor -> 0 katki; agirliklar renormalize EDILMEZ.
    """
    return _score_from_factors(
        _confluence_factors(poi, direction, picture, config), config
    )


# ============================================================
# Entry / SL / TP merdiveni
# ============================================================


def _entry_price(poi: POIRef, direction: Direction) -> float:
    """POI bandi icinde entry — retest mantigi.

    LONG: demand zone'un ust kenari (fiyat yukaridan POI'ye gelir).
    SHORT: supply zone'un alt kenari.
    Level POI: nokta fiyatin kendisi.

    ``build()`` zaten yalnizca band'li POI'leri aday yapar; band None ise
    bu programlama hatasidir -> sessiz 0.0 yerine acikca patla.
    """
    band = _poi_band(poi)
    if band is None:
        raise ValueError("POI band yok")
    lo, hi = band
    if lo == hi:  # Level
        return lo
    return hi if direction == Direction.LONG else lo


def _structural_sl(
    poi: POIRef, direction: Direction, entry: float, atr_val: float, config
) -> float:
    """Yapisal SL — POI'yi invalidate eden swing otesi.

    LONG: POI bandinin altina yapisal tampon; SHORT: ustune.
    Tampon = max(band_genisligi * sl_band_buffer_mult,
                 entry * sl_abs_buffer_pct).
    """
    band = _poi_band(poi)
    if band is None:
        return entry
    lo, hi = band
    width = hi - lo
    buffer = max(
        width * _cfg_sl_band_buffer_mult(config),
        abs(entry) * _cfg_sl_abs_buffer_pct(config),
    )
    if direction == Direction.LONG:
        return lo - buffer
    return hi + buffer


def _tp_ladder(
    entry: float, sl: float, direction: Direction, config
) -> list[float]:
    """TP1/2/3 — entry-SL "1R" uzerinden Fib extension merdiveni."""
    r = abs(entry - sl)
    r_mults = _cfg_tp_r_multiples(config)
    if direction == Direction.LONG:
        return [entry + m * r for m in r_mults]
    return [entry - m * r for m in r_mults]


def _bind_confirmation(
    picture: MarketPicture, direction: Direction
) -> Optional[StructureBreak]:
    """M15 snapshot'inda yon-uyumlu StructureBreak varsa onu dondur (yoksa None).

    Hard gate degil — sadece baglama. risk_guard confirmation zorunlulugunu
    ayrica uygular.
    """
    m15 = picture.per_tf.get(TimeFrame.M15)
    if m15 is None or not m15.structure:
        return None
    for sb in m15.structure:
        if sb.direction == direction:
            return sb
    return None


# ============================================================
# Tani — NoSetupReason + BuildResult (İŞ 2026-05-20)
# ============================================================


class NoSetupReason(Enum):
    """``build_with_diagnostics()`` neden ``Setup`` üretemedi.

    Yalnızca ``build()``'in FİİLEN var olan erken-return noktalarını
    listeler — hayalî sebep yok. Tanı log'unda görünür; veri-driven
    kalibrasyon kararına girdi olur.
    """

    NO_HTF_BIAS = "no_htf_bias"                  # htf_bias NEUTRAL → yön yok
    NO_POI = "no_poi"                            # yön-uyumlu aday POI yok
    LOW_CONFLUENCE = "low_confluence"            # best_score < confluence_min_score
    SL_GEOMETRY_TOO_TIGHT = "sl_geometry_too_tight"  # sl_distance < sl_min_atr*ATR
    SL_GEOMETRY_INVALID = "sl_geometry_invalid"  # sl_distance <= 0 (entry == sl)


@dataclass
class BuildResult:
    """``build_with_diagnostics()`` çıktısı.

    - ``setup`` dolu → ``no_setup_reason`` None (başarı).
    - ``setup`` None → ``no_setup_reason`` doludur (hangi gate elendi).
    - ``diagnostics`` her durumda ölçülen ara değerleri taşır (confluence
      skoru, sl_atr_ratio, aday POI sayısı vb.) — tanı log + debug için.
    """

    setup: Optional[Setup]
    no_setup_reason: Optional[NoSetupReason] = None
    diagnostics: dict = field(default_factory=dict)


# ============================================================
# Ana giris noktasi
# ============================================================


def build(picture: MarketPicture, config) -> Optional[Setup]:
    """``MarketPicture`` -> en iyi tek ``Setup`` veya ``None``.

    Davranış-koruyucu ince sarmalayıcı: ``build_with_diagnostics()``'in
    ``.setup`` alanını döner. Eski imza (``Optional[Setup]``) korunur —
    backtest harness + smoke_test bu imzaya bağımlı.

    Hard gate'ler (HTF uyumu, M15 onay zorunlulugu, min_rr, regime...) BURADA
    DEGIL — risk_guard'da. ``build()`` saf/deterministik.
    """
    return build_with_diagnostics(picture, config).setup


def build_with_diagnostics(picture: MarketPicture, config) -> BuildResult:
    """``MarketPicture`` -> ``BuildResult`` (setup + tanı sebebi + ölçümler).

    Adimlar (her erken-return noktası spesifik ``NoSetupReason`` set eder):
      1. Yon: htf_bias BULLISH->LONG, BEARISH->SHORT, NEUTRAL->NO_HTF_BIAS.
      2. Aday POI'ler: yön-uyumlu yoksa NO_POI.
      3. Confluence skorla; best_score < esik -> LOW_CONFLUENCE.
      4. Entry/SL/TP: sl_distance < sl_min_atr*ATR -> SL_GEOMETRY_TOO_TIGHT;
         sl_distance <= 0 -> SL_GEOMETRY_INVALID.
      5. M15 yön-uyumlu StructureBreak'i confirmation'a bagla.

    ``build_with_diagnostics()`` saf/deterministik.
    """
    diagnostics: dict = {}

    # --- 1. Yon ---
    if picture.htf_bias == Bias.BULLISH:
        direction = Direction.LONG
    elif picture.htf_bias == Bias.BEARISH:
        direction = Direction.SHORT
    else:
        diagnostics["htf_bias"] = getattr(picture.htf_bias, "name", str(picture.htf_bias))
        return BuildResult(setup=None, no_setup_reason=NoSetupReason.NO_HTF_BIAS,
                           diagnostics=diagnostics)

    # --- 2. Aday POI'ler ---
    candidates = [
        p
        for p in picture.active_pois
        if _poi_band(p) is not None and _poi_direction_aligned(p, direction)
    ]
    diagnostics["active_poi_count"] = len(picture.active_pois)
    diagnostics["candidate_poi_count"] = len(candidates)
    if not candidates:
        return BuildResult(setup=None, no_setup_reason=NoSetupReason.NO_POI,
                           diagnostics=diagnostics)

    # --- 3. Confluence skorla + en iyiyi sec (deterministik tie-break) ---
    scored: list[tuple[float, int, POIRef, list[float]]] = []
    for idx, poi in enumerate(candidates):
        factors = _confluence_factors(poi, direction, picture, config)
        sc = _score_from_factors(factors, config)
        scored.append((sc, idx, poi, factors))
    # En yuksek skor; esitlikte once gelen (idx kucuk) — deterministik.
    scored.sort(key=lambda t: (-t[0], t[1]))
    best_score, _, best_poi, best_factors = scored[0]
    # Ö-6: factor_count = yalnizca BAGIMSIZ KANIT faktorleri (>0):
    #   liquidity_context (idx 2), level_confluence (idx 3),
    #   fvg_imbalance (idx 4), clustering (idx 5).
    # Sayilmayanlar:
    #   poi_quality (idx 0) — POI'nin kendi nitelifi, neredeyse hep >0;
    #     bunu saymak "bedava bir faktor" hediye eder ve gate manipule edilir.
    #   premium_discount (idx 1) — POI'nin htf_range icindeki konumundan
    #     turetilir; bagimsiz dis kanit degil.
    _INDEPENDENT_FACTOR_IDX = (2, 3, 4, 5)
    factor_count = sum(
        1 for i in _INDEPENDENT_FACTOR_IDX if best_factors[i] > 0.0
    )
    diagnostics["confluence_score"] = best_score
    diagnostics["confluence_factor_count"] = factor_count

    # --- 4. Confluence esigi ---
    min_score = getattr(config, "confluence_min_score", 0.4)
    diagnostics["confluence_threshold"] = min_score
    if best_score < min_score:
        return BuildResult(setup=None, no_setup_reason=NoSetupReason.LOW_CONFLUENCE,
                           diagnostics=diagnostics)

    # --- 5. Entry / SL / TP ---
    entry = _entry_price(best_poi, direction)

    # ATR: H4 TFSnapshot'tan okunur (orchestrator her snapshot insa ederken
    # o TF'in OHLCV'sinden hesaplayip yazar). H4 snapshot yoksa veya atr 0 ise
    # SL minimum-mesafe kontrolu atlanir (anlamli bir esik yok).
    h4_snap = picture.per_tf.get(TimeFrame.H4)
    atr_val = float(getattr(h4_snap, "atr", 0.0)) if h4_snap is not None else 0.0

    sl = _structural_sl(best_poi, direction, entry, atr_val, config)

    sl_distance = abs(entry - sl)
    sl_min_mult = getattr(config, "sl_min_atr_multiple", 0.5)
    diagnostics["atr"] = atr_val
    diagnostics["sl_distance"] = sl_distance
    diagnostics["sl_min_atr_multiple"] = sl_min_mult
    # sl_atr_ratio: SL mesafesinin ATR'ye oranı — ATR>0 ise anlamlı.
    if atr_val > 0:
        diagnostics["sl_atr_ratio"] = sl_distance / atr_val
    # ATR > 0 ise minimum mesafe kontrolu yap; ATR 0 ise (sentetik/duz veri)
    # kontrol atlanir (anlamli bir esik yok).
    if atr_val > 0 and sl_distance < sl_min_mult * atr_val:
        return BuildResult(setup=None,
                           no_setup_reason=NoSetupReason.SL_GEOMETRY_TOO_TIGHT,
                           diagnostics=diagnostics)
    if sl_distance <= 0:
        return BuildResult(setup=None,
                           no_setup_reason=NoSetupReason.SL_GEOMETRY_INVALID,
                           diagnostics=diagnostics)

    tp = _tp_ladder(entry, sl, direction, config)
    tp_weights = list(_cfg_tp_weights(config))

    # rr: TP1'e gore (entry-SL = 1R).
    rr = _compute_rr(entry=entry, sl_distance=sl_distance, tp1=tp[0], direction=direction)
    diagnostics["rr"] = rr

    # --- 6. Confirmation baglama ---
    confirmation = _bind_confirmation(picture, direction)

    setup = Setup(
        direction=direction,
        entry=entry,
        sl=sl,
        tp=tp,
        tp_weights=tp_weights,
        poi=best_poi,
        confirmation=confirmation,
        bias_context=picture.htf_bias,
        confluence_score=best_score,
        rr=rr,
        created_at=picture.at_timestamp,
        confluence_factor_count=factor_count,
    )
    return BuildResult(setup=setup, no_setup_reason=None, diagnostics=diagnostics)
