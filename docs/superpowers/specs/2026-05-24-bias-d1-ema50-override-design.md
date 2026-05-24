# D1 EMA50 Trend-Override Bias Detection — Design

**Date**: 2026-05-24
**Author**: Brainstorm session (smc-engine)
**Status**: Design — awaiting spec review + user approval
**Related memories**:
- [[p2-robust-combos-diagnostic-2026-05-24]]
- [[bias-fix-d1-ema50-2026-05-24]]
- [[atr-regime-filter-validation-2026-05-24]]

## Motivation

2026-05-24 P2/P3 trade-level diagnostic'i ortaya çıkardı ki SMC stratejisi
zaten `htf_bias`'a göre yön seçiyor (`setup_builder.py:553-555`:
BULLISH→LONG, BEARISH→SHORT, NEUTRAL→no setup) — ancak `htf_bias` HESABI
yetersiz çözünürlükte:

### Bulgu özeti (robust_a kombo, sl_min=0.35, buf=0.25, filter-on)

| Rejim       | LONG (n, wr, expR)         | SHORT (n, wr, expR)        |
|-------------|----------------------------|----------------------------|
| P2 (bull)   | n=80, wr=0.550, **+0.717** | n=16, wr=0.375, **-0.263** |
| P3 (bear)   | n=140, wr=0.252, **-4.30** | n=38, wr=0.424, **+3.531** |

Tam simetrik yön asimetrisi: P2'de SHORT yıkıcı, P3'te LONG yıkıcı. Yani
yön seçimi mekanizması rejimi yanlış sınıflandırıyor.

### Root cause

`orchestrator._bias_from_snapshot()` (smc_engine/orchestrator.py:244-273):

```python
if structure:
    last = structure[-1]
    return (Bias.BULLISH if last.direction == Direction.LONG
            else Bias.BEARISH)
```

**htf_bias = D1'in tek son StructureBreak'inin yönü.** P3 window'unda
(2025-02-06→2025-04-30, 83 gün) yalnızca **4 D1 break** var (1 BOS, 3 CHOCH).
Bias neredeyse statik. Counter-trend retracement'larda oluşan tek BOS LONG
break, kalan tüm pencereyi BULLISH algılıyor → 168 LONG trade üretiliyor →
ana bear trende dönünce -4.30R kayıp.

CHOCH-only fix (sadece CHOCH bias'ı değiştirsin) denedi: 0 etki, çünkü 4
break'in 3'ü zaten CHOCH. Sorun **detection mantığında**, change-cadence'da
değil.

## Goals

- `_bias_from_snapshot()`'a D1 EMA50 trend-override yerleştir
- `close < ema50 → BEARISH`, `close ≥ ema50 → BULLISH`
- Yapı-bazlı ve close-trend fallback'ler eski mantıkta korunsun (config
  flag ile devre dışı bırakma + yetersiz veri kapsayışı)
- Production default `enabled=True`
- Tek dosyada lokalize değişim (orchestrator.py + config.py)

## Non-Goals (YAGNI)

- Multi-TF birleşik bias (D1+H4 majority vote): basit fix önce, multi-TF
  sonra gerek olursa
- Adaptive EMA period (volatility-aware lookback): tek sabit period (50) ile
  başla
- Bias hysteresis (close==EMA flip-flop yumuşatma): kanıt yok ki gerek;
  gerekirse sonra
- D1 slope/ADX/RSI gibi alternatif trend göstergeleri: F4 testinde slope20
  yanıltıcı çıktı (-11.36R), MA tabanlı tek metrik yeterli
- TFSnapshot'a EMA alanı ekleme: hesap inline yapılır, snapshot stateless kalır
- W1 (haftalık) bias entegrasyonu: ayrı bir kanal, ileride değerlendirilir

## Architecture

### Dosya değişimleri

| Dosya | Tür | Değişim |
|---|---|---|
| `smc_engine/config.py` | Modify | 2 yeni alan: `bias_use_d1_ema_trend`, `bias_d1_ema_period` |
| `smc_engine/orchestrator.py` | Modify | (a) `_bias_from_snapshot()` imza+gövde (`tf` + `config` eklenir), (b) **line 199 çağrı yeri güncellenir** (3-arg → 5-arg) |
| `tests/test_orchestrator_bias.py` | Create | Yeni unit test dosyası (10 test) |
| `tests/test_orchestrator.py` | Modify | 3 integration test ekle |
| `scripts/calibration_sweep.py` | Modify | CLI flag `--bias-use-d1-ema-trend / --no-...` + cfg override (Task ayrı) |

### Çağrı akışı

```
analyze(...) — line 596
  └→ for tf in _HTF_TFS:                          # D1 ONLY (_HTF_TFS = (D1,))
       └→ _cached_or_run(... _run_htf_detectors)  # snap üretir
       └→ snap.bias                               # = ?

_run_all_detectors(df, config, tf, tf_cfg)        # ÇAĞIRILAN HER TF İÇİN
  └→ ... structure, zones, imbalances ...
  └→ TFSnapshot(... bias=_bias_from_snapshot(df, structure, rng, tf, config))
                                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                YENI: 4. param = tf (TimeFrame), 5. = config
```

**ÖNEMLI:** `_run_all_detectors` her TF için (D1, H8, H4, H1, M15) çağrılıyor,
yani her TF'in `snap.bias`'ı bu fonksiyondan geliyor. `htf_bias = snap.bias`
ataması ise (line 596) sadece D1 için yapılıyor.

Yine de **EMA override yalnızca `tf == TimeFrame.D1`'de aktif olmalı**;
diğer TF'lerin (H8/H4/H1/M15) `snap.bias`'larının davranışını korumak için.
H4 bias şu an risk_guard'daki bazı kontrollerde kullanılıyor olabilir
(grep ile teyit). EMA H4'e taşırsa beklenmeyen yan etki olur.

**Tek call-site update (orchestrator.py:199):**
```python
# ESKI: bias = _bias_from_snapshot(df, structure, rng)
# YENI: bias = _bias_from_snapshot(df, structure, rng, tf, config)
```

## Algorithm Detail

```python
def _bias_from_snapshot(
    df: pd.DataFrame,
    structure: list[StructureBreak],
    rng: Optional[Range],
    tf: Optional[TimeFrame] = None,
    config=None,
) -> Bias:
    """Bir TF için bias türet.

    Öncelik:
      1. D1 EMA trend override (tf=D1 + config etkin + yeterli veri):
         close >= ema → BULLISH, close < ema → BEARISH
         (Equality → BULLISH; gerçek OHLC'de en fazla 1 bar etkisi)
      2. Yapı-bazlı bias (eski fallback): son StructureBreak yönü
      3. Close-trend fallback (sentetik veri için, az bar): ±0.5% eşik
      4. NEUTRAL (default — empty df / structure + flat close dahil)

    EMA: pandas Series.ewm(span=N, adjust=False).
      α = 2/(N+1), seed = first close değeri (recursive smoothing).
      Period başına en az 3×N bar olunca seed etkisi <%1.
    """
    use_ema = getattr(config, "bias_use_d1_ema_trend", True) if config else True
    ema_period = getattr(config, "bias_d1_ema_period", 50) if config else 50

    # TF gating: EMA override sadece D1'de aktif (H4/H1/M15 etkilenmez).
    if (
        use_ema
        and tf == TimeFrame.D1
        and len(df) >= ema_period
    ):
        closes = df["close"]
        ema = float(closes.ewm(span=ema_period, adjust=False).mean().iloc[-1])
        last_close = float(closes.iloc[-1])
        if last_close < ema:
            return Bias.BEARISH
        return Bias.BULLISH  # equality → BULLISH

    # --- Fallback 1: yapı-bazlı bias (eski) ---
    if structure:
        last = structure[-1]
        return (
            Bias.BULLISH if last.direction == Direction.LONG
            else Bias.BEARISH
        )

    # --- Fallback 2: close-trend (sentetik / az veri) ---
    if len(df) >= 2:
        closes_arr = df["close"].to_numpy()
        first, last_c = float(closes_arr[0]), float(closes_arr[-1])
        if first != 0:
            change = (last_c - first) / abs(first)
            if change > 0.005:
                return Bias.BULLISH
            if change < -0.005:
                return Bias.BEARISH
    return Bias.NEUTRAL
```

**Davranış kararları:**
- **Equality (close == ema):** BULLISH (yukarı varsayım — alternatif: NEUTRAL,
  ama down/flat ayrımı için 2 ayrı path getirir; sade tut). Gerçek OHLC
  verisinde bu durum nadirdir.
- **TF != D1:** EMA path aktif değil — eski yapı-bazlı/close-trend mantığı
  geçerli (H4/H1/M15 bias davranışı korunur).
- **Empty df (`len(df) == 0`):** Tüm path'ler fail → `Bias.NEUTRAL` döner
  (default return).
- **Yetersiz veri (`0 < len(df) < ema_period`):** Eski yapı-bazlı bias'a
  düşer. Mevcut unit testler genellikle 10-30 bar sentetik veri kullanır →
  bu test fixture'ları otomatik fallback'e gider, regression riski sınırlı.
- **Config kapalı:** Tamamen eski mantık. Regression debugging için.
- **NEUTRAL durumu EMA path'inde kalkıyor.** Eski yapı-bazlı path'te NEUTRAL
  hâlâ mümkün (yetersiz veri + structure yok + ±0.5% içinde değişim).

## Configuration

`smc_engine/config.py` SMCConfig dataclass:

```python
# --- bias detection trend override (Spec 2026-05-24) ---
bias_use_d1_ema_trend: bool = True   # production default AÇIK
bias_d1_ema_period: int = 50         # P3'te %86.5 down kapsayısı
```

**Defaults:**
- `True` — production-açık başlat. Regression test koşusu açık modda yapılır;
  test fail varsa fixture revize (gerçek bias değişimi).
- `50` — diagnostic F2'de P3'te %86.5 kapsayış, P2'de %4.2 kapsayış
  (rejim ayrımı net).

**CLI flag adlandırma:** ATR filter ile aynı argparse pattern'i:
- `--bias-d1-ema-disabled` (store_true) → cfg.bias_use_d1_ema_trend=False
- `--bias-d1-ema-period N` (int, default None) → cfg.bias_d1_ema_period
- Default'lar config'ten geliyor (filter-on, period=50)

**CLI override:** Kalibrasyon harness'ında flag eklenmeli — `calibration_sweep`
şu an `atr_percentile_threshold`/`atr_regime_filter_enabled` desteklediği
gibi, `bias_use_d1_ema_trend` da override edilebilir olmalı (paralel sweep
için):

```python
if "bias_use_d1_ema_trend" in params:
    cfg.bias_use_d1_ema_trend = params["bias_use_d1_ema_trend"]
if "bias_d1_ema_period" in params:
    cfg.bias_d1_ema_period = params["bias_d1_ema_period"]
```

## Testing Strategy

### Unit tests — `tests/test_orchestrator_bias.py` (yeni, 10 test)

Tüm EMA-path testleri **`tf=TimeFrame.D1`** ile koşulur. Bar sayısı min 150
(=3×ema_period), EMA seed etkisi <%1.

| # | Test adı | Senaryo | Beklenti |
|---|---|---|---|
| 1 | `test_bias_ema_d1_close_above_returns_bullish` | tf=D1, 150 bar monotonic up | `BULLISH` |
| 2 | `test_bias_ema_d1_close_below_returns_bearish` | tf=D1, 150 bar monotonic down | `BEARISH` |
| 3 | `test_bias_ema_d1_close_equal_returns_bullish` | tf=D1, 150 bar sabit close; assert `pytest.approx(close, ema)` | `BULLISH` |
| 4 | `test_bias_ema_insufficient_bars_falls_back_to_structure` | tf=D1, 30 bar (<50) + structure | last break direction |
| 5 | `test_bias_ema_disabled_falls_back_to_structure` | tf=D1, 150 bar + structure + `config.bias_use_d1_ema_trend=False` | last break direction |
| 6 | `test_bias_ema_default_config_none_uses_ema` | tf=D1, 150 bar + config=None | EMA path (BULLISH veya BEARISH duruma göre) |
| 7 | `test_bias_ema_non_d1_tf_skips_ema` | **tf=H4, 150 bar uptrend + structure SHORT** | structure → `BEARISH` (EMA path bypass) |
| 8 | `test_bias_fallback_close_trend_bullish` | 5 bar + 1% rise + structure=[] | `BULLISH` |
| 9 | `test_bias_fallback_close_trend_bearish` | 5 bar + 1% fall + structure=[] | `BEARISH` |
| 10 | `test_bias_fallback_neutral_empty_df` | `len(df)==0` + structure=[] | `NEUTRAL` |

### Integration tests — `tests/test_orchestrator.py` (extend)

- `test_analyze_d1_uptrend_returns_bullish_bias`: D1 sentetik 60 bar uptrend
  → `MarketPicture.htf_bias == BULLISH`
- `test_analyze_d1_downtrend_returns_bearish_bias`: simetrik downtrend → `BEARISH`
- `test_analyze_short_d1_uses_fallback_path`: D1 <50 bar → eski mantığa düşer

### Regression strategy

1. TDD ile yeni testler GREEN (yeni unit test dosyası 10/10, integration 3/3)
2. Tam test suite koşusu: `pytest tests/ -x --tb=no -q`
3. Fail edenleri kategorize et:
   - **Bias-değişimi gerçek mi?** → fixture revize (yeni davranış doğru)
   - **Detector/zone/imbalance test mi?** → bias'a duyarsız olmalı, fixture'da
     bias_use_d1_ema_trend=False enjekte (eski davranış)
4. Maksimum 5 iterasyon — fix-revize döngüsü uzarsa root cause yeniden bak

### Sweep validation (post-implementation, ayrı task)

P1/P2/P3 × {fix-off, fix-on} = 6 sweep, her biri 18 kombo grid:
```
python scripts/calibration_sweep.py \
    --m15-offset {2000,16000,29900} \
    --m15-window 8000 \
    --sl-min-atr 0.25,0.30,0.35,0.40,0.45,0.50 \
    --sl-band-buffer 0.25,0.375,0.50 \
    [--bias-use-d1-ema-trend / --bias-d1-ema-disabled]
```

**Pass kriterleri:**
- P3 blended R: -6.97 → > -3.0 (net iyileşme)
- P3 trade sayısı: ~208 → ~60-80 (yön filtresi sıkı)
- P2 CP3-passing kombo sayısı: 3 → ≥3 (mevcut seviye korunmalı)
- P1 baseline pf: en azından korunmalı (memory baseline -2.04 exp, 1.07 pf)
- Cross-window CP3: 0/18 → ?/18 (umut: ≥1)

## Risks & Mitigation

| Risk | Mitigation |
|---|---|
| **EMA50 flip-flop** P1 range'de (close↔ema yakın geçişler) | İlk implementation hysteresis yok. Sweep sonrası kanıt görülürse 2-bar confirm ekle (ayrı PR) |
| **660 test rejisinde büyük fail** (>50 test) | Eski fallback path'i koru → config-off ile eski davranış. Fix-on testlere `bias_use_d1_ema_trend=False` enjekte etmek yerine fixture revize tercih edilir (gerçek bias değişimi yansıtır) |
| **D1 yetersiz veri (warmup)**: ilk 50 D1 bar bias=eski mantık | Sentetik unit testler etkilenmez (eski path); production live'da ilk 50 gün warmup zaten beklenir |
| **EMA period yanlış kalibre**: 50 P3'te çalışıyor, başka rejimlerde? | Config-overridable. CLI flag ile farklı period sweep'i mümkün. P1 sweep validation'da gözlenir |
| **Yapı-bazlı bias bilgisi kaybı**: yeni LONG/SHORT geçişleri (CHOCH) görünmez | EMA tek-yönlü trend yakalar; structure'ın CHoCH/BOS ayrımı setup_builder'da hâlâ kullanılıyor (sadece bias hesabı değişiyor). Setup yönü ve POI alignment etkilenmez |
| **P1 (range) henüz test edilmedi** | Implementation öncesi P1 trade-level dump yapılmadı; sweep validation'da gözlenir. Eğer P1 bozulursa: hysteresis veya range-aware fallback eklenir |

## Expected Numerical Impact

**Pre-implementation tahmin (robust_a, P3):**
- 168 LONG → ~28 LONG kalır (140 elenir, -19R/trade ortalamasından kurtul)
- 40 SHORT → 40 SHORT kalır (hepsi downtrend bar'da, doğru tutuldu)
- Blended R: -6.97 → +1.67 (~7R/trade iyileşme)

**P2 robust_a:**
- 80 LONG → 80 LONG kalır (hepsi uptrend)
- 16 SHORT → 4 SHORT kalır (12 yanlış SHORT elendi)
- Blended R: +0.553 → +0.626

**P1 (range): bilinmiyor — sweep ile ölçülecek.**

## Rollout Plan

1. **Implementation phase** (TDD, subagent-driven-development):
   - Task 1: config.py 2 alan ekle + unit test
   - Task 2: orchestrator._bias_from_snapshot imza+gövde + 10 unit test
   - Task 3: integration test extend + tam test suite koşusu
   - Task 4: calibration_sweep CLI flag ekle + unit test
2. **Validation phase**:
   - Task 5: 6 sweep koş (P1/P2/P3 × on/off)
   - Task 6: validation analizi + memory dump (`project_bias_fix_validation_2026_05_24.md`)
3. **Decision gate**:
   - Pass → merge, production default `True`
   - Fail (P1 bozulması, regression) → root cause analizi, hysteresis veya
     ayrı tasarım dönüşü

## Out-of-Scope (deferred)

- Multi-TF bias birleşimi (D1+H4): bu fix yeterli olmazsa sonra
- EMA period otomatik kalibrasyonu (autoresearch ratchet loop): production'da
  D1=50 fix'leniyor; ileride parametre arama
- W1 (haftalık) bias ek katman
- TFSnapshot'a `bias_indicator: str` alanı (debug için "ema" vs "structure"
  yazar): observability iyileştirmesi, ayrı PR

## Acceptance Criteria

- [ ] 10 yeni unit test PASS (TF gating + empty df + equality dahil)
- [ ] 3 yeni integration test PASS
- [ ] Mevcut test suite: regression ≤10 test (revize gerekenler dokümante)
- [ ] P3 sweep blended R > -3.0
- [ ] P2 CP3-passing kombo sayısı ≥3
- [ ] CLI flag `--bias-d1-ema-disabled` ve `--bias-d1-ema-period` çalışıyor
- [ ] Memory `project_bias_fix_validation_2026_05_24.md` yazıldı

## Revision History

- **2026-05-24 v1**: İlk yazım.
- **2026-05-24 v2 (post spec-review)**: TF-gating (`tf == TimeFrame.D1`)
  eklendi (#1 IMPORTANT); orchestrator.py:199 call-site update explicit
  yazıldı (#2 IMPORTANT); EMA formula dokümante edildi + test bar count
  150 yapıldı (#3 IMPORTANT); empty-df testi eklendi (#4 IMPORTANT);
  equality semantics yorum eklendi; pytest.approx test #3 için belirtildi;
  CLI flag naming `--bias-d1-ema-disabled/--bias-d1-ema-period` olarak
  netleştirildi.
