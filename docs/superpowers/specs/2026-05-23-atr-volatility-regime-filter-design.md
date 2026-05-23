# ATR Percentile Volatility-Regime Filter — Design

**Date**: 2026-05-23
**Author**: Brainstorm session (smc-engine)
**Status**: Design — awaiting spec review + user approval

## Motivation

2026-05-23 yeniden kalibrasyon turunda (drawdown_breaker fix sonrası) ortaya
çıkan baseline (`logs/calibration/sweep-{P1,P2,P3}-2026-05-23.csv`) gösteriyor
ki SMC stratejisi 3 farklı BTC/M15 rejiminde de CP3 kriterini (her pencerede
pf≥1.5 ∧ exp>0) **18/18 kombinasyonda geçemiyor**.

Veri özet (kombo `sl_min=0.4/buf=0.375`, temsil amaçlı):

| Pencere | tc  | exp     | pf    |
|---------|-----|---------|-------|
| P1 range | 92  | +0.113  | 1.122 |
| P2 bull  | 94  | -0.590  | 1.620 |
| P3 bear  | 217 | -0.504  | 0.714 |

**Karakteristik:**
- P3 (2025-02→2025-04 ayı çöküşü) **tüm kombolarda pf<1.0** — strateji bear
  regime'de sistematik kaybediyor (yön doğru: %100 SHORT, ama timing yanlış).
- P2 boğa rallisinde `buf=0.5` ailesi `exp≈+5-6` ama `pf≈1.0-1.2` —
  outlier-driven (büyük SL = küçük position size = nadir kazananda yüksek
  R-multiple, gerçek dolar edge yok).
- P1 range'de marjinal pozitif edge, ama pf<1.5.

**Yapısal teşhis**: Strateji bir *selectivity* sorunu yaşıyor — çok trade
alıyor (P3'te 168-394 kombo başına), düşük accuracy (%25-50 win rate). Her
SMC setup'ını alıp HTF bias'a göre yön veriyor; "şu an trade alıp almama"
kararı yok. Özellikle yüksek-volatilite chop dönemlerinde POI yapıları
güvenilmez (sweep + retest mekanikleri çalışmıyor).

**Hipotez**: Yüksek volatilite rejiminde (ATR rolling percentile üst dilim)
SMC entry mekanikleri sistematik olarak başarısız oluyor. Bir volatility-
regime veto eklemek selectivity'yi yükseltir, P3'ün sistematik kaybını
durdurur, P1/P2'de en az kalitesiz trade'leri eler.

## Goals

- ATR-percentile tabanlı tek-eşik veto gate'i ekle (`risk_guard`'a)
- Mevcut SMC yapısının ruhuna uyum (ATR zaten her yerde temel primitive)
- Tek kalibrasyon parametresi (`atr_percentile_threshold`)
- Production'da güvenli default (`enabled=True`, geriye uyumlu)
- Kalibrasyon harness'inde override edilebilir (filter etkisi ölçülebilsin)

## Non-Goals (YAGNI)

- Multi-class regime detection (bear-vol, bull-vol, ranging, vb.). Sadece
  veto/skip.
- Adaptive parametre değişimi (örn. yüksek vol'de SL bandını genişlet). Veto
  yeterli; ek karmaşıklık ölçülmeden değer kanıtlamaz.
- ADX, D1 slope veya başka indikatörlerin eklenmesi (`feedback-recommend-
  dont-ask`'taki tercih: tek native primitive ile başla, gerek olursa genişlet).
- Live trading entegrasyonu. Bu round backtest doğrulama; live deploy
  CP3 kararına bağlı (ayrı süreç).

## Architecture

### Components

1. **`TFSnapshot.atr_history: list[float] | None`** (mevcut tip genişletme)
   - H4 snapshot'a yeni alan: son N H4 bar'ın ATR değerleri
   - Default `None` (geriye uyumlu — eski test fixture'lar etkilenmez)
   - Orchestrator H4 inşa ederken doldurur

2. **`Setup.regime_metrics: dict` (mevcut tip genişletme)**
   - `{"atr_percentile": float}` — setup oluşturulduğunda hesaplanmış değer
   - Default `{}` (geriye uyumlu)
   - `setup_builder.build_with_diagnostics` doldurur

3. **`smc_engine.risk_guard._check_volatility_regime(setup, config)`** (yeni)
   - `setup.regime_metrics["atr_percentile"]`'i okur
   - `config.atr_percentile_threshold` ile karşılaştırır
   - Yüksekse `str` (rejection reason) döner; düşükse `None`

4. **`SMCConfig`** (yeni alanlar)
   ```python
   atr_percentile_window: int = 96            # H4 bar (~16 gün)
   atr_percentile_threshold: float = 0.80     # > p80 ise veto
   atr_regime_filter_enabled: bool = True     # production default AÇIK
   ```

5. **`scripts/calibration_sweep.py`** (kalibrasyon harness entegrasyonu)
   - Yeni CLI: `--atr-percentile-threshold` (grid sweep)
   - Veya `--atr-regime-disabled` flag (baseline karşılaştırması için)
   - `_RealBacktestFn.__call__` ve `_make_real_walk_forward_fn` `run_wf`
     closure'larına param-ekleme

### Data Flow

```
M15 bar arrives
  │
  ▼
orchestrator.analyze(...)
  │  H4 snapshot inşası:
  │    - atr (mevcut)
  │    - atr_history: son N bar  ← YENİ
  │  picture.per_tf[H4] = snapshot
  ▼
setup_builder.build_with_diagnostics(picture, config)
  │  best POI seçildikten sonra:
  │    h4.atr_history ve current atr'den percentile hesapla
  │    setup.regime_metrics = {"atr_percentile": rank}  ← YENİ
  ▼
risk_guard.validate(setup, account_state, config)
  │  gate listesi (sıralı):
  │    confluence → regime → deviation → no_sl → min_rr
  │    → averaging → drawdown_breaker → funding
  │    → volatility_regime  ← YENİ (sona ekle: cheap, deterministik)
  ▼
ValidatedSetup veya Rejection(reason, gate="volatility_regime")
```

### State Management

ATR history yalnızca H4 snapshot içinde, snapshot-immutable. Orchestrator her
M15 barda H4 snapshot'ı yeniden inşa ediyor; her seferinde son N H4 bar'ın
ATR'sini hesaplar. **Cache ile uyumlu**: snapshot cache key
`(tf, son_bar_timestamp)` — aynı snapshot tekrar üretilmez.

Performance: ATR-history hesabı O(N), N=96 — ihmal edilebilir.

## Detailed Design

### `_check_volatility_regime` semantics

```python
def _check_volatility_regime(setup: Setup, config) -> str | None:
    """Karar — yuksek-vol rejimde setup veto et.

    setup.regime_metrics['atr_percentile'] mevcut H4 ATR'nin son N bar
    icindeki rolling percentile rank'i. Esik asilirsa "high-vol chop"
    olarak isaretlenir; SMC entry mekanikleri (POI retest, sweep + reclaim)
    bu rejimde sistematik olarak basarisiz oluyor (2026-05-23 P3 baseline
    18/18 kombo kayip).

    Disabled veya regime_metrics yoksa (warm-up / eski snapshot) -> None.
    """
    if not getattr(config, "atr_regime_filter_enabled", True):
        return None
    metrics = getattr(setup, "regime_metrics", {}) or {}
    rank = metrics.get("atr_percentile")
    if rank is None:
        return None  # warm-up veya snapshot eksik — safe pass
    threshold = getattr(config, "atr_percentile_threshold", 0.80)
    if rank > threshold:
        return (
            f"volatility regime: ATR percentile={rank:.2f} > "
            f"{threshold} — yuksek-vol chop, no trade"
        )
    return None
```

**Gate ordering**: `risk_guard.validate()` `checks` listesinin **sonuna**
eklenir. Diğer gate'ler (regime, deviation, min_rr) zaten geçti varsayımıyla
çalışır → kalitesiz setup'ı erken eleme yetkisini onlardan almaz, sadece
"kaliteli ama yanlış rejimde" setup'ı eler.

### Percentile hesabı (`setup_builder`)

```python
# build_with_diagnostics icinde, setup donmeden once:
h4 = picture.per_tf.get(TimeFrame.H4)
regime_metrics = {}
if h4 is not None and getattr(h4, "atr_history", None):
    history = h4.atr_history
    window = getattr(config, "atr_percentile_window", 96)
    if len(history) >= window // 2:
        recent = history[-window:]
        current = float(h4.atr)
        # rank: history icinde mevcut'tan KUCUK ya da ESIT eleman orani
        rank = sum(1 for v in recent if v <= current) / len(recent)
        regime_metrics["atr_percentile"] = rank
        diagnostics["atr_percentile"] = rank
```

**Convention**: `rank = (≤ current) / N`. Yani current = max history => rank
= 1.0; current = min => rank ≈ 1/N. Eşik **0.80** = "mevcut ATR son N H4
bar'ın %80'inden büyük/eşit".

### Orchestrator: `atr_history` doldurma

H4 ATR hesabı zaten `orchestrator.analyze` içinde TFSnapshot kurarken
yapılıyor (`smc_engine.orchestrator.build_h4_snapshot` veya benzeri).
Modifikasyon:

```python
# Mevcut: tek bir atr_val hesaplanıyor (snapshot.atr)
# Yeni: son N H4 bar icin atr listesi
h4_ohlcv = data[TimeFrame.H4]  # DataFrame, snapshot olusturma noktasinda
window = config.atr_percentile_window
# H4 bar'larin son window+ATR_PERIOD adetini al, rolling ATR hesapla
atr_series = _compute_atr_series(h4_ohlcv.tail(window + atr_period), atr_period)
snapshot.atr_history = atr_series.tail(window).tolist()
snapshot.atr = float(atr_series.iloc[-1])  # mevcut alan; tutarli
```

`_compute_atr_series` zaten var ya da yeni helper; `compute_atr` (tek değer)
varsa onu rolling'e sar.

### `SMCConfig` ekleme yeri

`smc_engine/config.py:95-103` (mevcut "setup_builder / risk_guard eşikleri"
bloğu) içine eklenir:

```python
# --- volatility regime filter (Spec §13.2, 2026-05-23 ekleme) ---
atr_percentile_window: int = 96
atr_percentile_threshold: float = 0.80
atr_regime_filter_enabled: bool = True
```

YAML override desteklenir (`load_config` zaten düz scalar'ları okuyor —
`test_config_sl_params_yaml_override` pattern'i geçerli).

### Kalibrasyon harness entegrasyonu

`scripts/calibration_sweep.py` `_RealBacktestFn.__call__` ve
`_make_real_walk_forward_fn` `run_wf`:

```python
cfg.sl_min_atr_multiple = params["sl_min_atr_multiple"]
cfg.sl_band_buffer_mult = params["sl_band_buffer_mult"]
# YENI: filter parametresi sweep edilebilir
if "atr_percentile_threshold" in params:
    cfg.atr_percentile_threshold = params["atr_percentile_threshold"]
if "atr_regime_filter_enabled" in params:
    cfg.atr_regime_filter_enabled = params["atr_regime_filter_enabled"]
# Drawdown breaker bypass (mevcut, 2026-05-23 fix)
cfg.max_consecutive_losses = 10**9
cfg.max_drawdown_pct = 1.0
```

CLI:
- `--atr-percentile-threshold 0.70,0.80,0.90` (grid)
- `--atr-regime-disabled` (filter kapalı baseline koşusu için)

Default sweep grid değişmez (sl_min × sl_buf) — yeni parametre opsiyonel
3'üncü eksen.

## Testing Strategy

### Failing tests (TDD RED, fix öncesi yazılır)

1. **`test_volatility_regime_gate_vetos_high_atr_setup`**
   - Setup `regime_metrics={"atr_percentile": 0.85}`, config threshold 0.80
   - `risk_guard.validate()` → `Rejection(gate="volatility_regime")`

2. **`test_volatility_regime_gate_admits_low_atr_setup`**
   - Setup `regime_metrics={"atr_percentile": 0.50}`, threshold 0.80
   - `risk_guard.validate()` → `ValidatedSetup`

3. **`test_volatility_regime_gate_disabled_passes_all`**
   - `config.atr_regime_filter_enabled = False`, regime_metrics ne olursa
   - Gate atlanır

4. **`test_volatility_regime_gate_missing_metrics_passes`**
   - Setup `regime_metrics={}` veya yok
   - Gate atlanır (warm-up safe default)

5. **`test_build_with_diagnostics_writes_atr_percentile_to_setup`**
   - H4 snapshot `atr_history=[1.0, 2.0, 3.0, ..., 100.0]`, current_atr=80
   - Setup `regime_metrics["atr_percentile"]` ≈ 0.80

6. **`test_orchestrator_writes_atr_history_to_h4_snapshot`**
   - Yapay H4 OHLCV, orchestrator çağrısı sonrası `picture.per_tf[H4].atr_history`
     None değil, uzunluğu config.atr_percentile_window ya da daha az (warm-up)

7. **`test_calibration_sweep_accepts_atr_threshold_sweep_param`**
   - `_RealBacktestFn` `params["atr_percentile_threshold"]=0.70` ile çağrıldığında
     cfg'ye yansır (monkeypatch harness.run + capture pattern)

8. **`test_calibration_sweep_atr_regime_disabled_flag`**
   - `_RealBacktestFn` `params["atr_regime_filter_enabled"]=False` ile çağrıldığında
     cfg'de False

### Regression

Mevcut 660 test geçmeye devam etmeli. Spesifik dikkat:
- `tests/test_risk_guard*.py` — gate ekleme mevcut gate sayısı/order assertion'larını
  bozmamalı
- `tests/test_setup_builder*.py` — `regime_metrics` default `{}` eski test
  fixture'larında yan etki yapmaz
- `tests/test_orchestrator*.py` — `atr_history` default None eski snapshot
  testlerini etkilemez

### Validation plan (post-fix, manuel)

```bash
# A. Filter kapalı (mevcut baseline)
python scripts/calibration_sweep.py ... \
    --atr-regime-disabled \
    --out logs/calibration/sweep-P3-2026-05-23-filter-off.csv

# B. Filter açık, threshold 0.80 (default)
python scripts/calibration_sweep.py ... \
    --out logs/calibration/sweep-P3-2026-05-24-filter-on-080.csv

# C. Threshold sweep
python scripts/calibration_sweep.py ... \
    --atr-percentile-threshold 0.70,0.80,0.90 \
    --out logs/calibration/sweep-P3-2026-05-24-threshold-sweep.csv
```

**Beklenen P3 sonucu**: A→B trade sayısı %50+ düşmeli, expectancy 0'a doğru
kaymalı, pf 0.7→1.0+ yükselmeli. Eğer beklentiler tutmazsa hipotez yanlış —
fix geri alınır.

**Beklenen P1/P2 sonucu**: marjinal etki (range zaten low-vol, bull
trend'de vol yüksek ama trend yapısı POI retest'e izin veriyor).

**Cross-window CP3 değerlendirmesi**: filter'lı baseline'da pf≥1.5 + exp>0
sağlanan kombo VAR mı? Varsa SMC + regime filter robust edge taşır;
yoksa Hipotez 2 (TP rejim-adaptif) veya başka yapısal adım gerekir.

## Risks and Open Questions

### Risk 1: ATR history yetersiz veri kaynağı

H4 ATR'sinin 96 bar lookback'ı için en az 96+ATR_PERIOD H4 barı gerekir. P1/P2/P3
8000-M15-bar pencereleri 8000/16 = 500 H4 bar içerir — yeterli. Ama daha
küçük backtestlerde warm-up'da snapshot `atr_history=None` veya kısa olabilir;
gate `safe pass`'i bunu handle eder.

### Risk 2: Threshold 0.80 keyfi

Başlangıç noktası. Validation phase'inde 0.70/0.80/0.90 sweep ile ayarlanır.
Eğer hiçbiri P3'ü kurtarmazsa hipotez (yüksek-vol = chop) yanlış olabilir;
o zaman ADX veya trend-quality göstergeleri masaya gelir.

### Risk 3: P2 boğa rallisi etkilenebilir

Boğa rallisinde de ATR yüksek olabilir. Filter trend-yön ayrımı yapmıyor —
sadece vol. P2'nin kâr eden (azı) trade'leri elenebilir. Bu validation
sweep'inden net görülecek.

### Açık soru — gate ordering

`_check_volatility_regime` checks listesinin **sonuna** eklenmesi öneriliyor
(cheap, deterministik, diğer gate'lerin başarısızlık reason'unu maskelemesin).
Alternatif: regime gate'inin hemen ardına. Bence sonda — diğer gate'ler erken
filtreliyor, vol gate'i ise "geride kalan kaliteli setup'ı reddet" demek.
Spec review sürecinde tartışılabilir.

## Implementation Plan (özet)

(Detayı writing-plans skill'inde ele alınacak; bu spec onaylandıktan sonra.)

1. **TFSnapshot.atr_history alanı** + orchestrator H4 inşası rolling ATR
2. **Setup.regime_metrics alanı** + `build_with_diagnostics`'te percentile hesabı
3. **SMCConfig yeni alanlar** + YAML override testi
4. **`risk_guard._check_volatility_regime`** + `validate()` checks listesine ekleme
5. **TDD failing tests** (1-8 yukarıda) → implementations
6. **Calibration harness entegrasyonu** + CLI flag'leri
7. **Regression: 660 test + 8 yeni test = 668 expected**
8. **Validation sweep** (P1/P2/P3 × filter on/off × threshold 0.7/0.8/0.9)
9. **Memory + commit + push verify**

## Acceptance Criteria

- [ ] 8 yeni TDD test RED → GREEN
- [ ] Regression 660 → 668 passed (mevcut testler kırılmaz)
- [ ] Validation sweep P3 filter-off → filter-on(0.80): trade sayısı düşer
      VE expectancy 0'a yaklaşır (pozitif yön)
- [ ] Production default `atr_regime_filter_enabled=True` ama mevcut
      backtest sonuçlarını bozmaz (eski yaml config'ler geriye uyumlu)
- [ ] Commit + push verify (`git log origin/main`)
- [ ] Memory: `project_atr_regime_filter_2026_05_23.md`

## References

- [[calibration-2026-05-23-breaker-fix]] — bu round'un öncesindeki bug fix ve
  doğru baseline
- [[reference-calibration-windows]] — P1/P2/P3 pencere tanımları
- `smc_engine/risk_guard.py:188-244` — `validate()` gate listesi pattern
- `smc_engine/setup_builder.py:489-516` — `NoSetupReason` + `BuildResult`
  diagnostics pattern (regime_metrics aynı tasarımla)
- `scripts/calibration_sweep.py:329-355` — `_RealBacktestFn.__call__` (override
  noktası)
