# D1 EMA50 Trend-Override Bias Detection — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** D1 EMA50 trend-override mekanizmasını `orchestrator._bias_from_snapshot()` içine yerleştirip P3 bear rejimde yanlış LONG setup üretimini durdurmak (beklenen blended R: -6.97 → +1.67).

**Architecture:** Tek dosyada lokalize değişim — `orchestrator._bias_from_snapshot()` imzasına `tf: TimeFrame` ve `config` parametreleri ekleniyor. EMA path yalnızca `tf == D1` iken aktif. Yapı-bazlı ve close-trend fallback'ler korundu. Config flag (`bias_use_d1_ema_trend`, default True) regression debugging için. Calibration sweep CLI'sine override flags eklenecek.

**Tech Stack:** Python 3.10+, pandas (ewm), pytest, mevcut SMCConfig dataclass.

**Spec referansı:** `docs/superpowers/specs/2026-05-24-bias-d1-ema50-override-design.md`

---

## Chunk 1: Config + Core bias fonksiyonu

### Task 1: SMCConfig — bias EMA trend alanları

**Files:**
- Modify: `smc_engine/config.py` (SMCConfig dataclass, alan eklemesi ATR alanları yakınına)
- Create: `tests/test_config_bias_ema.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config_bias_ema.py`:
```python
"""SMCConfig bias EMA trend alanları (Spec §Configuration, 2026-05-24)."""
from smc_engine.config import SMCConfig


def test_smcconfig_has_bias_use_d1_ema_trend_default_true():
    cfg = SMCConfig()
    assert cfg.bias_use_d1_ema_trend is True


def test_smcconfig_has_bias_d1_ema_period_default_50():
    cfg = SMCConfig()
    assert cfg.bias_d1_ema_period == 50


def test_smcconfig_bias_ema_fields_overridable():
    cfg = SMCConfig()
    cfg.bias_use_d1_ema_trend = False
    cfg.bias_d1_ema_period = 100
    assert cfg.bias_use_d1_ema_trend is False
    assert cfg.bias_d1_ema_period == 100
```

- [ ] **Step 2: Run test — verify FAIL**

```bash
pytest tests/test_config_bias_ema.py -v
```
Expected: `AttributeError: 'SMCConfig' object has no attribute 'bias_use_d1_ema_trend'`

- [ ] **Step 3: Add fields to SMCConfig**

Edit `smc_engine/config.py` — ATR alanlarına yakın bir konuma ekle:

```python
# --- bias detection trend override (Spec 2026-05-24) ---
bias_use_d1_ema_trend: bool = True            # production default ACIK
bias_d1_ema_period: int = 50                  # D1 EMA periyodu
```

- [ ] **Step 4: Run test — verify PASS**

```bash
pytest tests/test_config_bias_ema.py -v
```
Expected: 3/3 PASS

- [ ] **Step 5: Commit**

```bash
git add smc_engine/config.py tests/test_config_bias_ema.py
git commit -m "feat(config): bias_use_d1_ema_trend + bias_d1_ema_period (Spec 2026-05-24)"
```

---

### Task 2: `_bias_from_snapshot()` — TF-gated EMA path (TDD micro-cycles)

**Files:**
- Modify: `smc_engine/orchestrator.py` (function definition ~244-273, single caller — find via grep, NOT hardcoded line)
- Create: `tests/test_orchestrator_bias.py`

**Onbilgi:** Spec §Algorithm Detail tam pseudocode ile mevcut. 10 unit test Spec §Testing Strategy tablosunda. Tests `tf=TimeFrame.D1` ile koşulur (EMA path için), min 150 bar (3×period).

**Caller location instruction:** `_bias_from_snapshot` çağrısı orchestrator.py içinde tek yerdedir. Line numarası fonksiyon imza değişikliği sonrası kayabilir; bu yüzden grep ile bul:
```bash
grep -n "_bias_from_snapshot(df, structure, rng)" smc_engine/orchestrator.py
```
Tam bir eşleşme bulunmalı; başka eşleşme yoksa o satırı güncelle.

**TDD yaklaşımı:** 10 testi tek seferde yazmak Red-Green disiplinine aykırı. Üç micro-cycle:
- **Cycle A (Steps 1-5):** Çekirdek EMA path — 3 test (1, 2, 6), imza güncellemesi, caller fix
- **Cycle B (Steps 6-8):** TF gating + disabled + insufficient — 4 test (3, 4, 5, 7), zaten implementasyonla geçer
- **Cycle C (Steps 9-11):** Fallback regression koruması — 3 test (8, 9, 10), zaten implementasyonla geçer

---

#### CYCLE A — Core EMA path (RED → GREEN)

- [ ] **Step 1: Write Cycle A tests (3 EMA tests)**

Create `tests/test_orchestrator_bias.py` with **only the shared helpers + 3 EMA tests** for Cycle A. The remaining 7 tests will be added in Cycle B and C steps.

```python
"""_bias_from_snapshot — D1 EMA trend override (Spec 2026-05-24)."""
import pandas as pd
import pytest
from datetime import datetime, timedelta, timezone

from smc_engine.config import SMCConfig
from smc_engine.orchestrator import _bias_from_snapshot
from smc_engine.types import (
    Bias, Direction, StructureBreak, StructureKind, TimeFrame,
)


def _make_df(closes: list[float], start: datetime | None = None) -> pd.DataFrame:
    start = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
    if not closes:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    idx = pd.DatetimeIndex(
        [start + timedelta(days=i) for i in range(len(closes))]
    )
    return pd.DataFrame({
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1.0] * len(closes),
    }, index=idx)


def _make_break(direction: Direction, ts: datetime | None = None) -> StructureBreak:
    return StructureBreak(
        kind=StructureKind.BOS,
        direction=direction,
        broken_swing_price=100.0,
        confirm_candle_ts=ts or datetime(2024, 1, 10, tzinfo=timezone.utc),
        timeframe=TimeFrame.D1,
    )


# ============================================================
# CYCLE A — Core EMA path
# ============================================================


def test_bias_ema_d1_close_above_returns_bullish():
    """tf=D1, 150 bar monotonic up → close > ema → BULLISH."""
    df = _make_df([100 + i * 0.5 for i in range(150)])
    bias = _bias_from_snapshot(df, [], None, TimeFrame.D1, SMCConfig())
    assert bias == Bias.BULLISH


def test_bias_ema_d1_close_below_returns_bearish():
    """tf=D1, 150 bar monotonic down → close < ema → BEARISH."""
    df = _make_df([100 - i * 0.5 for i in range(150)])
    bias = _bias_from_snapshot(df, [], None, TimeFrame.D1, SMCConfig())
    assert bias == Bias.BEARISH


def test_bias_ema_default_config_none_uses_ema():
    """tf=D1, 150 bar uptrend + config=None → EMA path (BULLISH)."""
    df = _make_df([100 + i * 0.5 for i in range(150)])
    bias = _bias_from_snapshot(df, [], None, TimeFrame.D1, None)
    assert bias == Bias.BULLISH
```

- [ ] **Step 2: Run Cycle A tests — verify FAIL**

```bash
pytest tests/test_orchestrator_bias.py -v
```
Expected: 3/3 tests FAIL with `TypeError: _bias_from_snapshot() takes 3 positional arguments but 5 were given`.

- [ ] **Step 3: Update `_bias_from_snapshot()` in orchestrator.py**

Function definition (lines 244-273):

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
         close >= ema → BULLISH, close < ema → BEARISH.
      2. Yapı-bazlı bias (eski fallback): son StructureBreak yönü.
      3. Close-trend fallback (sentetik veri için, az bar): ±0.5% eşik.
      4. NEUTRAL (default — empty df / structure + flat close dahil).

    EMA: pandas Series.ewm(span=N, adjust=False), α=2/(N+1),
    seed=first close. Period başına en az 3×N bar olunca seed etkisi <%1.
    """
    use_ema = getattr(config, "bias_use_d1_ema_trend", True) if config else True
    ema_period = getattr(config, "bias_d1_ema_period", 50) if config else 50

    # TF gating: EMA override sadece D1'de aktif.
    if use_ema and tf == TimeFrame.D1 and len(df) >= ema_period:
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

- [ ] **Step 4: Update single call-site via grep (NOT hardcoded line)**

```bash
grep -n "_bias_from_snapshot(df, structure, rng)" smc_engine/orchestrator.py
```
Tek bir eşleşme bulunmalı. O satırı şuna değiştir:
```python
bias = _bias_from_snapshot(df, structure, rng, tf, config)
```

- [ ] **Step 5: Run Cycle A tests — verify PASS**

```bash
pytest tests/test_orchestrator_bias.py -v
```
Expected: 3/3 PASS.

---

#### CYCLE B — TF gating, disabled, insufficient bars (REGRESSION-PROTECTIVE)

- [ ] **Step 6: Add Cycle B tests (4 tests)**

Append to `tests/test_orchestrator_bias.py`:

```python
# ============================================================
# CYCLE B — TF gating + config-disabled + insufficient bars
# ============================================================


def test_bias_ema_d1_close_equal_returns_bullish():
    """tf=D1, 150 bar sabit close → close == ema → BULLISH (equality up)."""
    df = _make_df([100.0] * 150)
    bias = _bias_from_snapshot(df, [], None, TimeFrame.D1, SMCConfig())
    # Sabit seriede ewm = sabit; assert equality via approx
    closes = df["close"]
    ema = closes.ewm(span=50, adjust=False).mean().iloc[-1]
    assert ema == pytest.approx(closes.iloc[-1])
    assert bias == Bias.BULLISH


def test_bias_ema_insufficient_bars_falls_back_to_structure():
    """tf=D1, 30 bar (<50) + structure → last break direction."""
    df = _make_df([100.0] * 30)
    br = _make_break(Direction.SHORT)
    bias = _bias_from_snapshot(df, [br], None, TimeFrame.D1, SMCConfig())
    assert bias == Bias.BEARISH


def test_bias_ema_disabled_falls_back_to_structure():
    """tf=D1, 150 bar + structure + config.bias_use_d1_ema_trend=False → structure."""
    df = _make_df([100 + i * 0.5 for i in range(150)])  # EMA path BULLISH derdi
    br = _make_break(Direction.SHORT)
    cfg = SMCConfig()
    cfg.bias_use_d1_ema_trend = False
    bias = _bias_from_snapshot(df, [br], None, TimeFrame.D1, cfg)
    assert bias == Bias.BEARISH  # structure öncelikli


def test_bias_ema_non_d1_tf_skips_ema():
    """tf=H4, 150 bar uptrend + structure SHORT → structure (EMA bypass)."""
    df = _make_df([100 + i * 0.5 for i in range(150)])  # EMA D1-only → bypass
    br = _make_break(Direction.SHORT)
    bias = _bias_from_snapshot(df, [br], None, TimeFrame.H4, SMCConfig())
    assert bias == Bias.BEARISH  # structure (EMA D1-only)
```

- [ ] **Step 7: Run Cycle B tests — verify PASS**

```bash
pytest tests/test_orchestrator_bias.py -v
```
Expected: 7/7 PASS (Cycle A + B). Bu testler Step 3 implementasyonunun TF gating ve fallback path'lerini doğru kurduğunu kanıtlar — fail çıkarsa implementasyon hatalı.

---

#### CYCLE C — Close-trend + NEUTRAL fallback (LEGACY-PRESERVATION)

- [ ] **Step 8: Add Cycle C tests (3 tests)**

Append to `tests/test_orchestrator_bias.py`:

```python
# ============================================================
# CYCLE C — Eski close-trend + empty-df fallback regression koruması
# ============================================================


def test_bias_fallback_close_trend_bullish():
    """5 bar + 1% rise + structure=[] → close-trend BULLISH."""
    df = _make_df([100.0, 100.3, 100.6, 100.8, 101.0])  # +1%
    bias = _bias_from_snapshot(df, [], None, TimeFrame.D1, SMCConfig())
    assert bias == Bias.BULLISH


def test_bias_fallback_close_trend_bearish():
    """5 bar + 1% fall + structure=[] → close-trend BEARISH."""
    df = _make_df([100.0, 99.7, 99.4, 99.2, 99.0])  # -1%
    bias = _bias_from_snapshot(df, [], None, TimeFrame.D1, SMCConfig())
    assert bias == Bias.BEARISH


def test_bias_fallback_neutral_empty_df():
    """len(df)==0 + structure=[] → NEUTRAL."""
    df = _make_df([])
    bias = _bias_from_snapshot(df, [], None, TimeFrame.D1, SMCConfig())
    assert bias == Bias.NEUTRAL
```

- [ ] **Step 9: Run Cycle C tests — verify PASS**

```bash
pytest tests/test_orchestrator_bias.py -v
```
Expected: 10/10 PASS. Cycle C testler mevcut close-trend ve NEUTRAL fallback yollarını koruyor — fail çıkarsa Step 3 fallback path'lerinde gerileme var.

- [ ] **Step 10: Commit**

```bash
git add smc_engine/orchestrator.py tests/test_orchestrator_bias.py
git commit -m "feat(bias): D1 EMA50 trend override in _bias_from_snapshot (Spec 2026-05-24)"
```

---

## Chunk 2: Integration tests + regression check

### Task 3: Orchestrator integration tests

**Files:**
- Modify: `tests/test_orchestrator.py` — 3 yeni test ekle (mevcut helper'ları kullan)

**Onbilgi:**
- Mevcut helper'lar: `_candle` (line 29), `_df` (line 33), `_bullish_d1` (line 40),
  `_flat_df` (line 434), `_wide_df` (line 381) — `tests/test_orchestrator.py` içinde.
- `fixture_multi_tf` fixture'ı `tests/conftest.py:59`'da tanımlı, mevcut testlerde
  D1+H4+H1+M15 OHLCV sözlüğü döndürüyor.
- `analyze()` import zaten line 13'te mevcut.

- [ ] **Step 1: Write 3 failing integration tests**

Append to `tests/test_orchestrator.py` (end of file):

```python
# ============================================================
# D1 EMA50 trend-override bias integration (Spec 2026-05-24)
# ============================================================


def _ohlcv_multitf_from_d1_closes(d1_closes: list[float]) -> dict:
    """D1 close listesinden çok-TF OHLCV sözlüğü kur.

    D1 her bar O=H=L=C=close (zero-range, swing yok); diğer TF'ler
    her bir D1 close'unu N kez tekrar eder (resample yerine basit
    çoğaltma — orchestrator için yeterli, bias EMA hesabı D1'e bakar).
    """
    n_d1 = len(d1_closes)
    d1_rows = [_candle(c, c, c, c) for c in d1_closes]
    d1 = _df(d1_rows, start="2024-01-01", freq="1D")

    # H4: 6× D1 bar count, her D1 close 6 kere
    h4_closes = [c for c in d1_closes for _ in range(6)]
    h4 = _df([_candle(c, c, c, c) for c in h4_closes],
             start="2024-01-01", freq="4h")
    # H1: 24× D1 bar count
    h1_closes = [c for c in d1_closes for _ in range(24)]
    h1 = _df([_candle(c, c, c, c) for c in h1_closes],
             start="2024-01-01", freq="1h")
    # M15: 96× D1 bar count
    m15_closes = [c for c in d1_closes for _ in range(96)]
    m15 = _df([_candle(c, c, c, c) for c in m15_closes],
              start="2024-01-01", freq="15min")
    return {
        TimeFrame.D1: d1, TimeFrame.H4: h4,
        TimeFrame.H1: h1, TimeFrame.M15: m15,
    }


def test_analyze_d1_uptrend_returns_bullish_bias():
    """D1 sentetik 150 bar uptrend → htf_bias=BULLISH (EMA path)."""
    d1_closes = [100.0 + i * 0.5 for i in range(150)]
    ohlcv = _ohlcv_multitf_from_d1_closes(d1_closes)
    picture = analyze(ohlcv, SMCConfig())
    assert picture.htf_bias == Bias.BULLISH


def test_analyze_d1_downtrend_returns_bearish_bias():
    """D1 sentetik 150 bar downtrend → htf_bias=BEARISH (EMA path)."""
    d1_closes = [100.0 - i * 0.3 for i in range(150)]
    ohlcv = _ohlcv_multitf_from_d1_closes(d1_closes)
    picture = analyze(ohlcv, SMCConfig())
    assert picture.htf_bias == Bias.BEARISH


def test_analyze_short_d1_uses_fallback_path():
    """D1 < 50 bar → EMA bypass, structure/close-trend fallback."""
    # 30 bar uptrend — close-trend fallback +0.5%↑ → BULLISH
    d1_closes = [100.0 + i * 0.2 for i in range(30)]
    ohlcv = _ohlcv_multitf_from_d1_closes(d1_closes)
    picture = analyze(ohlcv, SMCConfig())
    # 30 bar flat-ish synthetic veride structure detect olmazsa
    # close-trend fallback BULLISH döner; structure detect olursa onun yönü.
    # Her iki yönde de DEĞIL: NEUTRAL. Acceptance: ≠ NEUTRAL.
    assert picture.htf_bias != Bias.NEUTRAL
```

**NOT:** Task 2 sonrası bu testler Task 3 implementation'ı eklenmeden DOĞRUDAN
PASS olur (Task 2'nin Step 4 caller fix'i hâlâ aktif). Task 3 saf test
katmanı — kod değişmez, sadece integration test sayısı artar.

- [ ] **Step 2: Run new tests**

```bash
pytest tests/test_orchestrator.py -k "analyze_d1" -v
```
Expected: 3/3 PASS (Task 2 implementasyonu sayesinde).

Eğer FAIL: Task 2 implementasyon eksiği (call-site update unutulmuş olabilir
veya TF gating yanlış). Önce Task 2'yi kontrol et.

- [ ] **Step 3: Commit**

```bash
git add tests/test_orchestrator.py
git commit -m "test(orchestrator): D1 EMA bias path integration tests"
```

---

### Task 4: Tam test suite regression check + fix

**Files:**
- Modify: regression-failing tests (fixture revize)

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -x --tb=no -q 2>&1 | tee /tmp/regression_check.log
```

- [ ] **Step 2: Categorize failures**

**Likely-affected dosyalar (önceliğe göre):**
- `tests/test_setup_builder.py` — setup yönü `htf_bias`'tan türetilir, en yüksek olasılık
- `tests/test_risk_guard.py` — bias-aware gate'ler içerir
- `tests/test_orchestrator.py` — direkt orchestrator testleri
- `tests/test_walk_forward.py` — backtest aggregate, dolaylı
- `tests/integrations/...` — entegrasyon testleri, dolaylı

**Tanı için ön komutlar:**
```bash
# Bias kullanım yerlerini test'lerde bul:
grep -nE "htf_bias|Bias\.(BULLISH|BEARISH|NEUTRAL)" tests/ -r

# Test koleksiyonu (kategorize için):
pytest tests/ --collect-only -q | grep -iE "bias|htf"
```

Her fail test için:
- Test bias değişimini kontrol mü ediyor (legit failure — fixture revize)?
- Bias'a duyarsız bir test mi (orchestrator'dan dolaylı bias akar, beklenti güncelle)?
- Setup builder/risk_guard test mi (bias direkt etkili — yön değişebilir)?

Failure listesini bir tablo halinde özetle (`/tmp/regression_categorize.md`):

| Test path | Failure type | Fix strategy |
|---|---|---|
| ... | bias-driven | fixture revize (yeni davranış doğru) |
| ... | indirect | beklenti güncelle |

- [ ] **Step 3: Fix failing tests (fixture revize öncelik)**

`feedback_recommend_dont_ask` memory'sine göre: legit bias değişimi → fixture
revize tercih (config-off injection sadece son çare).

Max 5 iterasyon. Eğer 5'i aşarsa → root cause analiz, kullanıcıya sun.

- [ ] **Step 4: Run again — verify ALL PASS**

```bash
pytest tests/ -q
```
Expected: All PASS. Önceki test count (660-675) + 13 yeni test = ~688 PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test: regression fixture revisions for D1 EMA bias (Spec 2026-05-24)"
```

---

## Chunk 3: Calibration sweep CLI

### Task 5: `--bias-d1-ema-disabled` + `--bias-d1-ema-period` CLI flags

**Files:**
- Modify: `scripts/calibration_sweep.py`
- Modify: `tests/test_calibration_sweep.py`

**Onbilgi:** Mevcut ATR pattern (calibration_sweep.py):
- **Line 339-342**: `_RealBacktestFn.__call__` içinde ATR override block
- **Line 398-401**: `_make_real_walk_forward_fn.run_wf` içinde ATR override block
- **Line 494-495**: grid build cell merge

Her üç yere de aynı pattern ile bias override block eklenmeli.

- [ ] **Step 1: Write failing tests (concrete bodies)**

Append to `tests/test_calibration_sweep.py`:

```python
def test_real_backtest_fn_applies_bias_use_d1_ema_trend_override(
    monkeypatch, tmp_path
):
    """params.bias_use_d1_ema_trend=False cfg üzerine geçer (override)."""
    from scripts.calibration_sweep import _RealBacktestFn
    captured = {}

    def fake_run(ohlcv, cfg, **kwargs):
        captured["cfg"] = cfg
        # Minimal BacktestResult — sadece metrics okunuyor.
        from smc_engine.types import BacktestResult
        import pandas as pd
        return BacktestResult(
            trades=[], equity_curve=pd.Series(dtype=float),
            metrics={"trade_count": 0, "win_rate": 0.0,
                     "expectancy": 0.0, "profit_factor": 0.0,
                     "max_drawdown_pct": 0.0, "sharpe": 0.0},
        )

    monkeypatch.setattr("backtest.harness.run", fake_run)
    # _load_btc_ohlcv'yi mock'la (parquet okumayı atla):
    monkeypatch.setattr(
        "scripts.calibration_sweep._load_btc_ohlcv",
        lambda window, offset: {},
    )

    fn = _RealBacktestFn(m15_window=1000, m15_offset=0, m15_lookback=140)
    fn({
        "sl_min_atr_multiple": 0.4,
        "sl_band_buffer_mult": 0.25,
        "bias_use_d1_ema_trend": False,
    })
    assert captured["cfg"].bias_use_d1_ema_trend is False


def test_real_backtest_fn_applies_bias_d1_ema_period_override(
    monkeypatch
):
    """params.bias_d1_ema_period=100 cfg üzerine geçer."""
    from scripts.calibration_sweep import _RealBacktestFn
    captured = {}

    def fake_run(ohlcv, cfg, **kwargs):
        captured["cfg"] = cfg
        from smc_engine.types import BacktestResult
        import pandas as pd
        return BacktestResult(
            trades=[], equity_curve=pd.Series(dtype=float),
            metrics={"trade_count": 0, "win_rate": 0.0,
                     "expectancy": 0.0, "profit_factor": 0.0,
                     "max_drawdown_pct": 0.0, "sharpe": 0.0},
        )

    monkeypatch.setattr("backtest.harness.run", fake_run)
    monkeypatch.setattr(
        "scripts.calibration_sweep._load_btc_ohlcv",
        lambda window, offset: {},
    )

    fn = _RealBacktestFn(m15_window=1000, m15_offset=0, m15_lookback=140)
    fn({
        "sl_min_atr_multiple": 0.4,
        "sl_band_buffer_mult": 0.25,
        "bias_d1_ema_period": 100,
    })
    assert captured["cfg"].bias_d1_ema_period == 100
```

- [ ] **Step 2: Run tests — verify FAIL**

```bash
pytest tests/test_calibration_sweep.py -k bias_ -v
```
Expected: 2/2 FAIL — bias param'ı cfg'ye geçmiyor (henüz block yok).

- [ ] **Step 3: Add cfg overrides in 2 callable sites**

`scripts/calibration_sweep.py` line **342** (mevcut ATR block sonrası):
```python
if "bias_use_d1_ema_trend" in params:
    cfg.bias_use_d1_ema_trend = params["bias_use_d1_ema_trend"]
if "bias_d1_ema_period" in params:
    cfg.bias_d1_ema_period = params["bias_d1_ema_period"]
```

Aynı block line **401** sonrası (`_make_real_walk_forward_fn.run_wf`).

- [ ] **Step 4: Add CLI flags**

argparse setup (mevcut `--atr-regime-disabled` yakın bir yerde):
```python
p.add_argument("--bias-d1-ema-disabled", action="store_true",
               help="bias_use_d1_ema_trend=False override "
                    "(regression debugging için)")
p.add_argument("--bias-d1-ema-period", type=int, default=None,
               help="bias_d1_ema_period override")
```

Grid build (mevcut line 494-495'in yakın bir block'una):
```python
if args.bias_d1_ema_disabled:
    cell["bias_use_d1_ema_trend"] = False
if args.bias_d1_ema_period is not None:
    cell["bias_d1_ema_period"] = args.bias_d1_ema_period
```

- [ ] **Step 5: Run tests — verify PASS**

```bash
pytest tests/test_calibration_sweep.py -k bias_ -v
```
Expected: 2/2 PASS.

- [ ] **Step 6: Smoke test CLI**

```bash
python scripts/calibration_sweep.py --help | grep -E "bias-d1-ema"
```
Expected: `--bias-d1-ema-disabled`, `--bias-d1-ema-period N` görünmeli.

- [ ] **Step 7: Commit**

```bash
git add scripts/calibration_sweep.py tests/test_calibration_sweep.py
git commit -m "feat(calibration): --bias-d1-ema-disabled + --bias-d1-ema-period flags"
```

---

## Chunk 4: Doğrulama ve push

### Task 6: Final sanity check + push

- [ ] **Step 1: Run full test suite — final**

```bash
pytest tests/ -q --tb=short
```
Expected: ALL PASS (~688 test).

- [ ] **Step 2: Verify spec/plan committed**

```bash
git status
git log --oneline -10
```

- [ ] **Step 3: Push (kullanıcı onayı sonrası)**

```bash
git push origin main
git log origin/main --oneline -5
```

**ÖNEMLI:** Memory'deki "push verification" kuralı — push komutu çıktısı + `git log origin/main` çıktısı paylaşılmalı.

- [ ] **Step 4: Mark Adım 8 complete in TaskUpdate**

---

## Validation (post-implementation, Adım 9 — ayrı task)

**Bu plan'ın kapsamı dışı.** Spec'in §Acceptance Criteria'sındaki şu maddeler
bu plan'da yer ALMAZ, ayrı bir validation task'ında ele alınır:
- P3 sweep blended R > -3.0
- P2 CP3-passing kombo sayısı ≥3
- Memory `project_bias_fix_validation_2026_05_24.md` yazımı

Adım 9 (TaskList'te ayrı task) için protokol:
```bash
python c:/tmp/run_bias_fix_validation_2026_05_24.py
```
6 sweep koş (P1/P2/P3 × on/off), `logs/calibration/sweep-{Px}-2026-05-24-bias-{on,off}.csv`.
Analiz scripti + memory dump bu task'ın içinde.

---

## Acceptance Checklist (her commit sonrası)

- [ ] TDD disiplini: test → fail → minimal impl → pass → commit
- [ ] Her commit'te `pytest -q` PASS
- [ ] Imza değişimi tek call-site update gerektirir (grep ile lokalize edilir)
- [ ] Backwards compat: eski test fixture'ları (≤30 bar) otomatik fallback'e gider
- [ ] CLI flag adlandırma: ATR pattern (`--bias-d1-ema-disabled / --bias-d1-ema-period`)
- [ ] Memory'ler güncel kalır (her chunk sonu opsiyonel update)
