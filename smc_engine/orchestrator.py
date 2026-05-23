"""SMC Engine orchestrator — MTF kaskad — Spec §7, §7.1, §5.1.

``analyze(ohlcv_by_tf, config, at_bar=None, cache=None) -> MarketPicture``

Uc katmanli zaman dilimi kaskadi (tumegelim: HTF -> LTF):

  Katman 1 — D1: range + structure + level + liquidity detektorleri
             -> ``htf_bias``, ``htf_range`` belirlenir.
  Katman 2 — H8/H4: 6 detektor calisir, HTF bias'a gore FILTRELENIR
             (bullish -> yalnizca DEMAND zone'lar POI olur, SUPPLY elenir;
             bearish simetrik tersi). Enrichment: ``Zone.age_bars`` ve
             ``Imbalance.fill_ratio`` guncellenir; ``liquidity_detector``'a
             ``known_levels`` gecirilir.
  Katman 3 — M15: yalnizca guncel fiyat aktif bir POI yakinindayken detektor
             calistirir; onaylanan POI'ler ``active_pois``'e eklenir.

**Determinizm / look-ahead:** ``at_bar`` verilince her TF DataFrame'i dahili
olarak ``[:at_bar]`` (timestamp <= at_bar) dilimlenir — yalnizca kapanmis
barlar kullanilir. ``A = analyze(full, at_bar=t)`` ile
``B = analyze(full[:t+1], at_bar=t)`` ozdes ``MarketPicture`` uretir.

**HTF cache (§7.1):** ``cache`` opsiyonel dict. Key: ``(TimeFrame, son_kapanis
timestamp)``. D1 detektorleri yalnizca yeni D1 bar kapandiginda yeniden calisir;
H4 yalnizca yeni H4 bar kapandiginda; M15 her cagri. Cache yoksa normal
hesaplama (geriye uyumlu). Orchestrator fonksiyon seviyesinde STATELESS kalir:
ayni input -> ayni output; cache yalnizca gereksiz tekrar hesabi onler.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime
from typing import Optional

import pandas as pd

from smc_engine.detectors import (
    detect_imbalances,
    detect_levels,
    detect_liquidity,
    detect_range,
    detect_structure,
    detect_zones,
)
from smc_engine.detectors._atr import atr as _atr
from smc_engine.detectors._atr import atr_series as _atr_series
from smc_engine.types import (
    Bias,
    Direction,
    Imbalance,
    Level,
    MarketPicture,
    POIKind,
    POIRef,
    Range,
    StructureBreak,
    TFSnapshot,
    TimeFrame,
    Zone,
    ZoneKind,
    ZoneStatus,
)

# HTF -> LTF sirasi. Orchestrator katmanlari bu sirayla isler.
_HTF_TFS = (TimeFrame.D1,)
_MTF_TFS = (TimeFrame.H8, TimeFrame.H4)
_LTF_TFS = (TimeFrame.M15,)

# Bir POI'nin "aktif" sayilmasi icin guncel fiyatin POI bandina goreli
# yakinlik tamponu (band genisliginin kati). Band genisligi 0 ise mutlak
# tampon olarak fiyatin %0.5'i kullanilir.
_POI_PROXIMITY_BAND_MULT = 0.5
_POI_PROXIMITY_ABS_PCT = 0.005


# ============================================================
# TF-bilincli config — detektorlere gercek TimeFrame'i gecirir
# ============================================================


def _tf_config(config, tf: TimeFrame):
    """Config'in ``timeframe`` alani ``tf`` olarak set edilmis sig kopyasi.

    Detektorler ``getattr(config, "timeframe", None)`` okuyor; orchestrator
    her TF iterasyonunda gercek TimeFrame'i bu sekilde gecirir (Faz 1'in
    ``timeframe=None`` sinirlamasi orchestrator katmaninda boyle cozulur).
    """
    cfg = copy.copy(config)
    try:
        cfg.timeframe = tf
    except Exception:  # pragma: no cover - dataclass slots vb.
        # Son care: hafif bir sarmalayici.
        class _Wrap:
            pass

        w = _Wrap()
        for k in dir(config):
            if not k.startswith("_"):
                try:
                    setattr(w, k, getattr(config, k))
                except Exception:
                    pass
        w.timeframe = tf
        return w
    return cfg


# ============================================================
# at_bar dilimleme — look-ahead bias onleme
# ============================================================


def _slice_to_at_bar(
    df: pd.DataFrame,
    at_bar: Optional[datetime],
    config=None,
    tf: Optional[TimeFrame] = None,
) -> pd.DataFrame:
    """DataFrame'i ``at_bar``'a (dahil) kadar dilimle — kapanmis barlar.

    ``at_bar`` None ise (ve config/tf yoksa) df oldugu gibi doner. Aksi halde
    ``df.index <= at_bar`` filtresi uygulanir: t sonrasi barlar sizmaz.

    KR-1 (kapsamli inceleme 2026-05-15): ``config`` ve ``tf`` verilirse,
    ust-sinir dilimlemesinden sonra **alt sinir** da uygulanir:
    ``df.iloc[-config.lookback_bars(tf):]``. Bu sayede detektorlere giden
    df boyutu TF basina sabit-ust-sinirli kalir (orchestrator backtest cost
    O(n^2) yerine O(n) olur). Look-ahead GUVENLI: yalnizca eski barlar
    atilir, gelecek bar sizmaz. Determinizm korunur: ayni input -> ayni
    son-N bar dilimi.
    """
    sliced = df
    if at_bar is not None:
        ts = pd.Timestamp(at_bar)
        sliced = sliced.loc[sliced.index <= ts]
    if config is not None and tf is not None:
        try:
            max_bars = config.lookback_bars(tf)
        except (AttributeError, KeyError):
            max_bars = None
        if max_bars is not None and len(sliced) > max_bars:
            sliced = sliced.iloc[-max_bars:]
    return sliced


# ============================================================
# HTF cache — (TimeFrame, son_kapanis_ts) anahtarli memoization
# ============================================================


def _last_ts(df: pd.DataFrame) -> Optional[pd.Timestamp]:
    if len(df) == 0:
        return None
    return df.index[-1]


def _cache_key(tf: TimeFrame, df: pd.DataFrame):
    return (tf, _last_ts(df))


# ============================================================
# Katman calisticilari
# ============================================================


def _run_all_detectors(
    df: pd.DataFrame,
    config,
    tf: TimeFrame,
    known_levels: Optional[list[float]] = None,
    tf_cfg=None,
) -> TFSnapshot:
    """Verilen TF DataFrame'inde 6 detektoru calistirir, ham TFSnapshot doner.

    Detektor sirasi Spec §5.1: range -> structure -> zone -> imbalance ->
    liquidity (known_levels ile) -> level. Filtreleme/enrichment burada YOK
    (ust katman yapar) — bu fonksiyon saf detektor toplayicidir.

    Ö-11: ``tf_cfg`` opsiyonel — analyze() basinda TF -> config eslemesi
    5x onceden hesaplanir. Verilmezse on-the-fly hesap (geri uyumlu).
    """
    cfg = tf_cfg if tf_cfg is not None else _tf_config(config, tf)
    rng_list = detect_range(df, cfg)
    structure = detect_structure(df, cfg)
    zones = detect_zones(df, cfg)
    imbalances = detect_imbalances(df, cfg)
    levels = detect_levels(df, cfg)
    # liquidity: known_levels = HTF range sinirlari + Level fiyatlari + bu TF
    # range sinirlari (Spec §5.1 enrichment).
    kl = list(known_levels) if known_levels else []
    for r in rng_list:
        kl.extend([r.high, r.low])
    for lv in levels:
        kl.append(lv.price)
    liquidity = detect_liquidity(df, cfg, known_levels=kl or None)

    rng = rng_list[0] if rng_list else None
    bias = _bias_from_snapshot(df, structure, rng)
    # ATR: bu TF'in (zaten at_bar'a kadar dilimlenmis) OHLCV'sinden.
    atr_period = getattr(config, "atr_period", 14)
    # Rolling ATR series — son N bar'i atr_history'ye yaz; son deger = atr.
    if len(df) >= 2:
        series = _atr_series(df, atr_period).dropna()
        atr_val = float(series.iloc[-1]) if len(series) > 0 else 0.0
        # atr_history yalnizca H4 icin anlamli (vol regime filter H4-tabanli).
        # Diger TF'lerde None birak — gereksiz hesap + memory yok.
        if tf == TimeFrame.H4:
            window = getattr(config, "atr_percentile_window", 96)
            atr_history = series.tail(window).tolist()
        else:
            atr_history = None
    else:
        atr_val = 0.0
        atr_history = None
    return TFSnapshot(
        range_=rng,
        bias=bias,
        zones=zones,
        imbalances=imbalances,
        levels=levels,
        liquidity_events=liquidity,
        structure=structure,
        atr=atr_val,
        atr_history=atr_history,
    )


def _run_htf_detectors(
    df: pd.DataFrame, config, tf: TimeFrame, tf_cfg=None,
) -> TFSnapshot:
    """Katman 1 (D1) — range + structure + level + liquidity.

    HTF katmaninda zone/imbalance da hesaplanir (TFSnapshot tam dolsun, ileride
    debug/confluence ihtiyaci icin) ama HTF bias yalnizca range + structure'dan
    turetilir.

    Ö-11: tf_cfg pre-built config (analyze() basinda hesaplandi) — yoksa
    on-the-fly (geri uyumlu).
    """
    return _run_all_detectors(df, config, tf, tf_cfg=tf_cfg)


def _bias_from_snapshot(
    df: pd.DataFrame,
    structure: list[StructureBreak],
    rng: Optional[Range],
) -> Bias:
    """Bir TF icin bias turet.

    Oncelik:
      1. Son ``StructureBreak`` yonu — LONG -> BULLISH, SHORT -> BEARISH.
      2. Structure yoksa: kapanis trendi fallback — son kapanis ilk kapanistan
         belirgin yuksekse BULLISH, dusukse BEARISH, aksi halde NEUTRAL.
    """
    if structure:
        last = structure[-1]
        return (
            Bias.BULLISH
            if last.direction == Direction.LONG
            else Bias.BEARISH
        )
    # Fallback: kapanis trendi (monoton sentetik setler / az veri icin).
    if len(df) >= 2:
        closes = df["close"].to_numpy()
        first, last_c = float(closes[0]), float(closes[-1])
        if first != 0:
            change = (last_c - first) / abs(first)
            if change > 0.005:
                return Bias.BULLISH
            if change < -0.005:
                return Bias.BEARISH
    return Bias.NEUTRAL


# ============================================================
# Enrichment — Zone.age_bars, Imbalance.fill_ratio (Spec §5.1)
# ============================================================


def _enrich_zones(
    zones: list[Zone], df: pd.DataFrame, current_price: float,
    config=None,
) -> list[Zone]:
    """Zone.age_bars + status'u guncelle.

    ``age_bars`` = (df icindeki son bar konumu) - (origin_candle_ts konumu).
    origin index'te bulunamazsa 0 birakilir.

    Ö-3: Status gecisi — age_bars > max_zone_age_bars ise zone MITIGATED'a
    gecirilir. Onceki kod max_zone_age_bars'i hic kullanmiyordu (olu config);
    bu kural sayesinde yasli zone'lar Confluence'ta zone_status_factor ile
    duşuk skorlu olur (FRESH=1.0 -> MITIGATED=0.3).
    """
    if not zones or len(df) == 0:
        return zones
    max_age = (
        getattr(config, "max_zone_age_bars", 200) if config is not None else 200
    )
    index = df.index
    last_pos = len(index) - 1
    out: list[Zone] = []
    for z in zones:
        try:
            pos = index.get_loc(pd.Timestamp(z.origin_candle_ts))
            if isinstance(pos, slice):  # tekrar eden index (olmamali)
                pos = pos.start
        except KeyError:
            out.append(z)
            continue
        age = max(0, last_pos - int(pos))
        # Status gecisi (Ö-3): yas esigi asildi -> MITIGATED.
        # BROKEN gecisi v2 (price-bant ihlali kontrolu — pahali; bu turda yok).
        new_status = z.status
        if z.status == ZoneStatus.FRESH and age > max_age:
            new_status = ZoneStatus.MITIGATED
        out.append(replace(z, age_bars=age, status=new_status))
    return out


def _enrich_imbalances(
    imbalances: list[Imbalance], df: pd.DataFrame, current_price: float
) -> list[Imbalance]:
    """Imbalance.fill_ratio + filled guncelle.

    Olusumdan SONRAki barlarin fiyat ekstremlerinin imbalance bandina ne kadar
    girdigini olcer. fill_ratio = bandin doldurulan orani (0.0-1.0). Band tamamen
    gecilmisse filled=True.
    """
    if not imbalances or len(df) == 0:
        return imbalances
    index = df.index
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    out: list[Imbalance] = []
    for imb in imbalances:
        band = imb.top - imb.bottom
        if band <= 0:
            out.append(imb)
            continue
        try:
            pos = index.get_loc(pd.Timestamp(imb.created_at))
            if isinstance(pos, slice):
                pos = pos.start
        except KeyError:
            out.append(imb)
            continue
        pos = int(pos)
        # Olusum sonrasi (orta mum + sonrasi) barlar imbalance'i doldurabilir.
        penetration = 0.0
        for j in range(pos + 1, len(index)):
            if imb.direction == Direction.LONG:
                # Bullish FVG: fiyat asagi gelip bandi doldurur.
                # top'tan asagi inilen mesafe.
                depth = imb.top - float(lows[j])
            else:
                # Bearish FVG: fiyat yukari cikip bandi doldurur.
                depth = float(highs[j]) - imb.bottom
            if depth > penetration:
                penetration = depth
        ratio = max(0.0, min(1.0, penetration / band))
        out.append(
            replace(imb, fill_ratio=ratio, filled=ratio >= 1.0)
        )
    return out


# ============================================================
# HTF bias filtreleme -> POIRef listesi (Spec §7, R1.1 kurali)
# ============================================================


def _zone_in_discount(zone: Zone, htf_range: Optional[Range]) -> Optional[bool]:
    """Zone'un orta noktasi HTF range'in discount bolgesinde mi?

    Donen: True (discount), False (premium), None (HTF range yok -> bilinmiyor).
    """
    if htf_range is None:
        return None
    mid = (zone.top + zone.bottom) / 2
    return mid <= htf_range.equilibrium


def _filter_pois_by_bias(
    snapshot: TFSnapshot,
    htf_bias: Bias,
    htf_range: Optional[Range],
) -> list[POIRef]:
    """HTF bias'a gore TFSnapshot'tan POIRef listesi uret (R1.1 filtreleme).

    Kural (Spec §7, R1.1 duzeltilmis):
      - htf_bias = BULLISH -> sistem yalnizca LONG arar:
          * DEMAND zone'lar tutulur. Discount'taki demand -> htf_aligned=True,
            score_hint yuksek. Premium'daki demand -> htf_aligned=False,
            score_hint dusuk (tutulur ama dusuk oncelik).
          * SUPPLY zone'lar ELENIR.
      - htf_bias = BEARISH -> simetrik tersi (SUPPLY tutulur, DEMAND elenir).
      - htf_bias = NEUTRAL -> her iki yon de tutulur (htf_aligned=False,
        notr score_hint) — yon belirsizken eleme yapilmaz.

    Imbalance ve Level POI'leri: yon uyumuna gore (Imbalance.direction) ayni
    mantik; Level her bias'ta tutulur (kurumsal referans, yon-notr).
    """
    pois: list[POIRef] = []

    for z in snapshot.zones:
        if z.status.name == "BROKEN":
            continue  # bayatlamis / kirilmis zone POI olamaz
        if htf_bias == Bias.BULLISH:
            if z.kind != ZoneKind.DEMAND:
                continue  # SUPPLY elenir
            in_disc = _zone_in_discount(z, htf_range)
            aligned = in_disc is True or in_disc is None
            score = 1.0 if aligned else 0.4
        elif htf_bias == Bias.BEARISH:
            if z.kind != ZoneKind.SUPPLY:
                continue  # DEMAND elenir
            in_disc = _zone_in_discount(z, htf_range)
            # bearish: premium uyumlu
            aligned = in_disc is False or in_disc is None
            score = 1.0 if aligned else 0.4
        else:  # NEUTRAL
            aligned = False
            score = 0.6
        pois.append(
            POIRef(
                kind=POIKind.ZONE,
                ref=z,
                htf_aligned=aligned,
                score_hint=_zone_freshness_factor(z) * score,
            )
        )

    for imb in snapshot.imbalances:
        if htf_bias == Bias.BULLISH and imb.direction != Direction.LONG:
            continue
        if htf_bias == Bias.BEARISH and imb.direction != Direction.SHORT:
            continue
        aligned = htf_bias != Bias.NEUTRAL
        # dolmus imbalance'in cazibesi az
        score = 0.7 * (1.0 - imb.fill_ratio)
        pois.append(
            POIRef(
                kind=POIKind.IMBALANCE,
                ref=imb,
                htf_aligned=aligned,
                score_hint=score,
            )
        )

    for lv in snapshot.levels:
        pois.append(
            POIRef(
                kind=POIKind.LEVEL,
                ref=lv,
                htf_aligned=False,
                score_hint=0.5,
            )
        )

    return pois


def _zone_freshness_factor(z: Zone) -> float:
    """Spec §5.2 — FRESH 1.0 / TESTED 0.7 / MITIGATED 0.3 / BROKEN 0.0."""
    return {
        "FRESH": 1.0,
        "TESTED": 0.7,
        "MITIGATED": 0.3,
        "BROKEN": 0.0,
    }.get(z.status.name, 1.0)


# ============================================================
# POI yakinlik kontrolu — Katman 3 aktivasyonu
# ============================================================


def _poi_band(poi: POIRef) -> Optional[tuple[float, float]]:
    """POI'nin fiyat bandi (low, high). Level icin tek nokta -> (p, p)."""
    ref = poi.ref
    if isinstance(ref, Zone):
        return (ref.bottom, ref.top)
    if isinstance(ref, Imbalance):
        return (ref.bottom, ref.top)
    if isinstance(ref, Level):
        return (ref.price, ref.price)
    return None


def _price_near_poi(price: float, poi: POIRef) -> bool:
    """Guncel fiyat POI bandina (tamponuyla) yeterince yakin mi?

    Tampon = max(band_genisligi * _POI_PROXIMITY_BAND_MULT,
                  price * _POI_PROXIMITY_ABS_PCT).
    """
    band = _poi_band(poi)
    if band is None:
        return False
    lo, hi = band
    width = hi - lo
    buffer = max(
        width * _POI_PROXIMITY_BAND_MULT,
        abs(price) * _POI_PROXIMITY_ABS_PCT,
    )
    return (lo - buffer) <= price <= (hi + buffer)


# ============================================================
# Ana giris noktasi
# ============================================================


def analyze(
    ohlcv_by_tf: dict,
    config,
    at_bar: Optional[datetime] = None,
    cache: Optional[dict] = None,
) -> MarketPicture:
    """MTF kaskadi calistir -> ``MarketPicture``.

    Args:
        ohlcv_by_tf: ``{TimeFrame | str: pd.DataFrame}`` — TF basina OHLCV.
            Anahtarlar ``TimeFrame`` enum'u veya string ("D1", "H4", "M15")
            olabilir. DataFrame'ler ``DatetimeIndex``'li, open/high/low/close
            /volume kolonlu.
        config: ``SMCConfig`` — detektor parametreleri + esikler.
        at_bar: Opsiyonel ``datetime`` — verilirse her TF DataFrame'i bu
            timestamp'e (dahil) kadar dilimlenir; t sonrasi barlar kullanilmaz
            (look-ahead bias onleme).
        cache: Opsiyonel dict — HTF detektor sonuc cache'i. Key:
            ``(TimeFrame, son_kapanis_ts)``. Verilirse ayni HTF penceresi icin
            detektorler yeniden calismaz. Verilmezse normal hesaplama.

    Returns:
        ``MarketPicture`` — ``per_tf``, ``htf_bias``, ``htf_range``,
        ``active_pois``, ``at_timestamp``, ``current_price`` dolu.

    Determinizm: cache verilse de verilmese de, ayni (ohlcv, config, at_bar)
    icin ozdes ``MarketPicture``.
    """
    # --- TF anahtarlarini normalize et (str -> TimeFrame) ---
    normalized: dict[TimeFrame, pd.DataFrame] = {}
    for k, df in ohlcv_by_tf.items():
        tf = k if isinstance(k, TimeFrame) else TimeFrame[str(k)]
        # KR-1: TF_LOOKBACK alt-sinirini de uygula (analiz cost O(n) -> sabit).
        normalized[tf] = _slice_to_at_bar(df, at_bar, config=config, tf=tf)

    if not normalized:
        raise ValueError("analyze: ohlcv_by_tf bos")

    # Ö-11: TF -> tf_cfg eslemesini analyze() basinda 5x hesapla (per-detektor
    # cagrida copy.copy(config) yapmak yerine). Sıcak yolu hafifletir.
    tf_cfg_by_tf: dict[TimeFrame, object] = {
        tf: _tf_config(config, tf) for tf in normalized
    }

    per_tf: dict[TimeFrame, TFSnapshot] = {}

    # ----------------------------------------------------------------
    # Katman 1 — D1 (HTF): range + structure + level + liquidity -> bias
    # ----------------------------------------------------------------
    htf_bias = Bias.NEUTRAL
    htf_range: Optional[Range] = None
    htf_known_levels: list[float] = []

    for tf in _HTF_TFS:
        if tf not in normalized:
            continue
        df = normalized[tf]
        if len(df) == 0:
            continue
        snap = _cached_or_run(
            cache, tf, df, config,
            lambda d, c, t=tf: _run_htf_detectors(d, c, t, tf_cfg=tf_cfg_by_tf[t]),
        )
        # Ö-2: Enrichment (age_bars / fill_ratio) D1 icin de calismali.
        # Onceki kod yalniz H8/H4'te enrich ediyordu; D1 zone/imbalance
        # sahte 0 degerleriyle confluence'a sizmaktaydi (poi_quality/
        # liquidity faktorlerini bozuyordu).
        cur_price = float(df["close"].iloc[-1])
        enriched_zones = _enrich_zones(snap.zones, df, cur_price, config)
        enriched_imb = _enrich_imbalances(snap.imbalances, df, cur_price)
        snap = TFSnapshot(
            range_=snap.range_,
            bias=snap.bias,
            zones=enriched_zones,
            imbalances=enriched_imb,
            levels=snap.levels,
            liquidity_events=snap.liquidity_events,
            structure=snap.structure,
            atr=snap.atr,
            atr_history=snap.atr_history,
        )
        per_tf[tf] = snap
        htf_bias = snap.bias
        htf_range = snap.range_
        if htf_range is not None:
            htf_known_levels.extend([htf_range.high, htf_range.low])
        htf_known_levels.extend(lv.price for lv in snap.levels)

    # ----------------------------------------------------------------
    # Katman 2 — H8/H4 (MTF): 6 detektor + HTF filtreleme + enrichment
    # ----------------------------------------------------------------
    mtf_pois: list[POIRef] = []

    for tf in _MTF_TFS:
        if tf not in normalized:
            continue
        df = normalized[tf]
        if len(df) == 0:
            continue
        snap = _cached_or_run(
            cache,
            tf,
            df,
            config,
            lambda d, c, t=tf: _run_all_detectors(
                d, c, t, known_levels=htf_known_levels,
                tf_cfg=tf_cfg_by_tf[t],
            ),
        )
        # Enrichment: age_bars / fill_ratio guncelle (Spec §5.1).
        cur_price = float(df["close"].iloc[-1])
        enriched_zones = _enrich_zones(snap.zones, df, cur_price, config)
        enriched_imb = _enrich_imbalances(snap.imbalances, df, cur_price)
        snap = TFSnapshot(
            range_=snap.range_,
            bias=snap.bias,
            zones=enriched_zones,
            imbalances=enriched_imb,
            levels=snap.levels,
            liquidity_events=snap.liquidity_events,
            structure=snap.structure,
            atr=snap.atr,
            atr_history=snap.atr_history,
        )
        per_tf[tf] = snap
        # HTF bias'a gore filtrele -> POI havuzu.
        mtf_pois.extend(
            _filter_pois_by_bias(snap, htf_bias, htf_range)
        )

    # ----------------------------------------------------------------
    # Katman 3 — M15 (LTF): yalnizca fiyat aktif POI yakininda detektor;
    # onaylanan POI'ler active_pois'e.
    # ----------------------------------------------------------------
    active_pois: list[POIRef] = []

    # current_price + at_timestamp: en kucuk mevcut TF'in son barindan.
    ltf_tf, ltf_df = _smallest_tf(normalized)
    current_price = float(ltf_df["close"].iloc[-1])
    at_timestamp = ltf_df.index[-1].to_pydatetime()

    for tf in _LTF_TFS:
        if tf not in normalized:
            continue
        df = normalized[tf]
        if len(df) == 0:
            continue
        m15_price = float(df["close"].iloc[-1])
        # Fiyat hangi POI'lere yakin?
        nearby = [p for p in mtf_pois if _price_near_poi(m15_price, p)]
        if nearby:
            # M15 detektorleri yalnizca burada calisir.
            snap = _cached_or_run(
                cache,
                tf,
                df,
                config,
                lambda d, c, t=tf: _run_all_detectors(
                    d, c, t, known_levels=htf_known_levels,
                    tf_cfg=tf_cfg_by_tf[t],
                ),
            )
            per_tf[tf] = snap
            # Yakin POI'ler M15 katmaninda onaylanir -> active_pois.
            active_pois.extend(nearby)
        else:
            # POI yok: M15 snapshot bos (detektor calismaz). U-16: atr=0.0
            # tutarlilik icin acikca verilir (TFSnapshot.atr varsayilani 0.0
            # ama snapshot'in 'detektor calismadi' durumu net olsun).
            per_tf[tf] = TFSnapshot(
                range_=None,
                bias=Bias.NEUTRAL,
                zones=[],
                imbalances=[],
                levels=[],
                liquidity_events=[],
                structure=[],
                atr=0.0,
            )

    # M15 hic yoksa: active_pois = filtrelenmis MTF POI havuzu (LTF onayi
    # mumkun degil). Bu, sadece D1+H4 verildiginde de POI gorulebilmesini
    # saglar (test_ltf_filtering bunu kullanir).
    if not any(tf in normalized for tf in _LTF_TFS):
        active_pois = list(mtf_pois)

    return MarketPicture(
        per_tf=per_tf,
        htf_bias=htf_bias,
        htf_range=htf_range,
        active_pois=active_pois,
        at_timestamp=at_timestamp,
        current_price=current_price,
    )


def _smallest_tf(
    normalized: dict[TimeFrame, pd.DataFrame]
) -> tuple[TimeFrame, pd.DataFrame]:
    """En kucuk (en sik) mevcut TF'i ve DataFrame'ini dondur.

    M15 < H4 < H8 < D1 sirasi — at_timestamp / current_price bu TF'ten okunur.
    """
    order = [
        TimeFrame.M15,
        TimeFrame.H1,
        TimeFrame.H4,
        TimeFrame.H8,
        TimeFrame.D1,
    ]
    for tf in order:
        if tf in normalized and len(normalized[tf]) > 0:
            return tf, normalized[tf]
    # Fallback: herhangi bir dolu DataFrame.
    for tf, df in normalized.items():
        if len(df) > 0:
            return tf, df
    raise ValueError("analyze: tum DataFrame'ler bos")


def _cached_or_run(
    cache: Optional[dict],
    tf: TimeFrame,
    df: pd.DataFrame,
    config,
    runner,
) -> TFSnapshot:
    """HTF cache lookup — Spec §7.1.

    Cache None ise dogrudan ``runner(df, config)`` calistirir.
    Cache verilmisse key=(tf, son_kapanis_ts); hit varsa cache'ten doner,
    yoksa hesaplar + cache'e yazar.

    M15 her cagri yeniden calisabilir ama key son barin timestamp'i oldugu
    icin ayni M15 son bari icin de cache hit olur — bu DETERMINISTIK
    (ayni input -> ayni output) ve dogru: yeni M15 bari = yeni key.
    """
    if cache is None:
        return runner(df, config)
    key = _cache_key(tf, df)
    if key in cache:
        return cache[key]
    snap = runner(df, config)
    cache[key] = snap
    return snap
