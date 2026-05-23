# ATR Percentile Volatility-Regime Filter Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SMC stratejisinin selectivity sorununu azaltmak için H4 ATR percentile-tabanlı bir veto gate'i ekle. Yüksek-vol rejimde (rolling > eşik) trade alımı durdurulur.

**Architecture:** Yeni gate `risk_guard._check_volatility_regime`'e ek, `Setup.regime_metrics["atr_percentile"]` üzerinden okunur. Percentile `setup_builder` içinde H4 snapshot'taki yeni `atr_history` listesinden hesaplanır. Orchestrator H4 snapshot inşası sırasında rolling ATR'yi listeye yazar. Kalibrasyon harness'ine override CLI flag'leri eklenir.

**Tech Stack:** Python 3.10+, pandas, pytest, dataclass. Mevcut `smc_engine.detectors._atr.atr_series` rolling ATR helper'ı kullanılır.

**Spec:** `docs/superpowers/specs/2026-05-23-atr-volatility-regime-filter-design.md`

---

## File Structure

**Modify:**
- `smc_engine/config.py:95-103` — 3 yeni alan (`atr_percentile_window`, `atr_percentile_threshold`, `atr_regime_filter_enabled`)
- `smc_engine/types.py:269-281` — `TFSnapshot.atr_history: list[float] | None = None`
- `smc_engine/types.py:219-235` — `Setup.regime_metrics: dict = field(default_factory=dict)`
- `smc_engine/orchestrator.py:165-211` — `_run_all_detectors` rolling ATR hesabı
- `smc_engine/setup_builder.py:537-659` — `build_with_diagnostics` regime_metrics yazımı
- `smc_engine/risk_guard.py:207-228` — yeni `_check_volatility_regime` + `checks` listesine ekleme
- `scripts/calibration_sweep.py:329-355` ve `380-398` — CLI flag'leri + cfg override

**Create:**
- `tests/test_volatility_regime_gate.py` — yeni gate unit testleri (4 test)
- `tests/test_atr_regime_integration.py` — orchestrator→builder→risk_guard end-to-end (1 test)

**Modify (test):**
- `tests/test_calibration_sweep.py` — 2 yeni CLI/cfg override test

---

## Task 1: SMCConfig 3 yeni alan

**Files:**
- Modify: `smc_engine/config.py:95-103`
- Test: `tests/test_config.py` (mevcut dosya; YAML override testi pattern'i `tests/test_calibration_sweep.py:262-276`'da)

- [ ] **Step 1: Failing test yaz**

`tests/test_config.py`'a ekle (ya da yeni `tests/test_config_atr_regime.py` oluştur):

```python
def test_smcconfig_has_atr_regime_filter_fields_with_defaults():
    """SMCConfig 3 yeni alan icermeli (production default: filter on, p80, 96 bar)."""
    from smc_engine.config import SMCConfig
    cfg = SMCConfig()
    assert cfg.atr_percentile_window == 96
    assert cfg.atr_percentile_threshold == 0.80
    assert cfg.atr_regime_filter_enabled is True


def test_smcconfig_atr_regime_yaml_override():
    """YAML scalar override (test_config_sl_params_yaml_override pattern)."""
    import os
    import tempfile
    from smc_engine.config import load_config
    yaml_content = (
        "atr_percentile_window: 120\n"
        "atr_percentile_threshold: 0.70\n"
        "atr_regime_filter_enabled: false\n"
    )
    fd, path = tempfile.mkstemp(suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(yaml_content)
        cfg = load_config(path)
        assert cfg.atr_percentile_window == 120
        assert cfg.atr_percentile_threshold == 0.70
        assert cfg.atr_regime_filter_enabled is False
    finally:
        os.unlink(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_atr_regime.py -v` (veya `tests/test_config.py::test_smcconfig_has_atr_regime_filter_fields_with_defaults`)
Expected: FAIL — `AttributeError: 'SMCConfig' object has no attribute 'atr_percentile_window'`

- [ ] **Step 3: Implement — `smc_engine/config.py:95-103`**

Mevcut "setup_builder / risk_guard eşikleri" bloğunun sonuna ekle (line ~103):

```python
    # --- volatility regime filter (Spec §13.2, 2026-05-23) ---
    atr_percentile_window: int = 96            # H4 bar (~16 gun lookback)
    atr_percentile_threshold: float = 0.80     # > p80 ise veto
    atr_regime_filter_enabled: bool = True     # production default AÇIK
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config_atr_regime.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add smc_engine/config.py tests/test_config_atr_regime.py
git commit -m "feat(config): add ATR percentile volatility-regime filter fields"
```

---

## Task 2: TFSnapshot.atr_history alanı

**Files:**
- Modify: `smc_engine/types.py:269-281`
- Test: `tests/test_types.py` (mevcut) veya yeni `tests/test_atr_regime_types.py`

- [ ] **Step 1: Failing test yaz**

```python
def test_tfsnapshot_atr_history_default_none():
    """Mevcut TFSnapshot(...) yapimlari kirilmamali — atr_history default None."""
    from smc_engine.types import TFSnapshot, Bias
    snap = TFSnapshot(
        range_=None, bias=Bias.NEUTRAL,
        zones=[], imbalances=[], levels=[],
        liquidity_events=[], structure=[],
    )
    assert snap.atr_history is None


def test_tfsnapshot_atr_history_stores_list():
    """atr_history kwarg verildiginde liste olarak saklanmali."""
    from smc_engine.types import TFSnapshot, Bias
    snap = TFSnapshot(
        range_=None, bias=Bias.NEUTRAL,
        zones=[], imbalances=[], levels=[],
        liquidity_events=[], structure=[],
        atr_history=[1.0, 2.0, 3.0],
    )
    assert snap.atr_history == [1.0, 2.0, 3.0]
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/test_atr_regime_types.py::test_tfsnapshot_atr_history_default_none -v`
Expected: FAIL — `TypeError: TFSnapshot.__init__() got an unexpected keyword argument 'atr_history'`

- [ ] **Step 3: Implement — `smc_engine/types.py:269-281`**

`atr: float = 0.0` satırından SONRA ekle:

```python
    # --- volatility regime filter (Spec §13.2, 2026-05-23) ---
    # Son N H4 bar'in ATR degerleri (rolling history). orchestrator H4 snapshot
    # insa ederken doldurur. None = warm-up veya eski test fixture (geriye
    # uyumluluk). setup_builder bu listeden ATR percentile rank hesaplar.
    atr_history: Optional[list[float]] = None
```

`Optional` zaten import edilmiş (line 9 civarı). Eğer değilse `from typing import Optional` ekle.

- [ ] **Step 4: Run test to verify pass**

Run: `python -m pytest tests/test_atr_regime_types.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add smc_engine/types.py tests/test_atr_regime_types.py
git commit -m "feat(types): add TFSnapshot.atr_history field for rolling ATR window"
```

---

## Task 3: Setup.regime_metrics alanı

**Files:**
- Modify: `smc_engine/types.py:219-235`
- Test: `tests/test_atr_regime_types.py` (önceki task'ten devam)

- [ ] **Step 1: Failing test yaz (önceki dosyaya ekle)**

```python
def test_setup_regime_metrics_default_empty_dict():
    """Mevcut Setup(...) yapimlari kirilmamali — regime_metrics default {}."""
    from datetime import datetime, timezone
    from smc_engine.types import (
        Setup, Direction, Bias, POIRef, POIKind, Level,
    )
    setup = Setup(
        direction=Direction.LONG,
        entry=100.0, sl=98.0, tp=[103.0],
        tp_weights=[1.0],
        poi=POIRef(kind=POIKind.LEVEL, ref=Level(price=100.0, kind="HIGH")),
        confirmation=None,
        bias_context=Bias.BULLISH,
        confluence_score=0.5, rr=1.5,
        created_at=datetime.now(timezone.utc),
    )
    assert setup.regime_metrics == {}


def test_setup_regime_metrics_independent_instances():
    """Mutable default footgun yok — her instance kendi dict'i (default_factory)."""
    from datetime import datetime, timezone
    from smc_engine.types import (
        Setup, Direction, Bias, POIRef, POIKind, Level,
    )
    def _make():
        return Setup(
            direction=Direction.LONG, entry=100.0, sl=98.0, tp=[103.0],
            tp_weights=[1.0],
            poi=POIRef(kind=POIKind.LEVEL, ref=Level(price=100.0, kind="HIGH")),
            confirmation=None, bias_context=Bias.BULLISH,
            confluence_score=0.5, rr=1.5,
            created_at=datetime.now(timezone.utc),
        )
    s1 = _make()
    s2 = _make()
    s1.regime_metrics["atr_percentile"] = 0.9
    assert s2.regime_metrics == {}, "Setup default_factory dict olmali, paylasilan instance degil"
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/test_atr_regime_types.py::test_setup_regime_metrics_default_empty_dict -v`
Expected: FAIL — `AttributeError: 'Setup' object has no attribute 'regime_metrics'`

- [ ] **Step 3: Implement — `smc_engine/types.py:219-235`**

`confluence_factor_count: int = 0` satırından SONRA ekle:

```python
    # --- volatility regime filter (Spec §13.2, 2026-05-23) ---
    # setup_builder olusturma sirasinda hesapladigi rejim olcumleri.
    # Su an: {"atr_percentile": float}. risk_guard._check_volatility_regime
    # bu sozlukten okur. Default factory mutable footgun'unu engeller.
    regime_metrics: dict = field(default_factory=dict)
```

`field, dataclass` import'unun olduğundan emin ol (zaten var; line 5 civarı).

- [ ] **Step 4: Run test to verify pass**

Run: `python -m pytest tests/test_atr_regime_types.py -v`
Expected: 4 passed (2 prev + 2 new)

- [ ] **Step 5: Commit**

```bash
git add smc_engine/types.py tests/test_atr_regime_types.py
git commit -m "feat(types): add Setup.regime_metrics dict for volatility filter"
```

---

## Task 4: `_check_volatility_regime` gate logic

**Files:**
- Modify: `smc_engine/risk_guard.py:207-228` (yeni helper + `checks` listesine ekleme)
- Create: `tests/test_volatility_regime_gate.py`

- [ ] **Step 1: 4 failing test yaz**

```python
"""risk_guard._check_volatility_regime gate testleri (Spec §13.2)."""
from datetime import datetime, timezone

from smc_engine.config import SMCConfig
from smc_engine.types import (
    AccountState, Bias, Direction, Level, POIKind, POIRef, Rejection,
    Setup, ValidatedSetup,
)
from smc_engine.risk_guard import _check_volatility_regime, validate


def _make_setup(regime_metrics: dict | None = None) -> Setup:
    """Test helper — minimum setup with M15 confirmation + valid SL/TP."""
    from smc_engine.types import StructureBreak
    setup = Setup(
        direction=Direction.LONG, entry=100.0, sl=98.0, tp=[103.0],
        tp_weights=[1.0],
        poi=POIRef(kind=POIKind.LEVEL, ref=Level(price=100.0, kind="HIGH")),
        confirmation=StructureBreak(
            direction=Direction.LONG, bar_index=10, price=100.0, kind="BOS",
        ),
        bias_context=Bias.BULLISH,
        confluence_score=0.7, rr=1.5,
        created_at=datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc),
        confluence_factor_count=3,
    )
    if regime_metrics is not None:
        setup.regime_metrics = regime_metrics
    return setup


def _make_account() -> AccountState:
    return AccountState(
        equity=10_000.0, open_position=False,
        consecutive_losses=0, max_drawdown_pct=0.0,
    )


def test_volatility_regime_gate_vetos_high_atr_setup():
    """ATR percentile > threshold -> Rejection(gate='volatility_regime')."""
    setup = _make_setup(regime_metrics={"atr_percentile": 0.85})
    cfg = SMCConfig()  # threshold default 0.80
    result = validate(setup, _make_account(), cfg)
    assert isinstance(result, Rejection)
    assert result.gate == "volatility_regime"
    assert "ATR percentile" in result.reason


def test_volatility_regime_gate_admits_low_atr_setup():
    """ATR percentile < threshold -> ValidatedSetup (vol gate gecer)."""
    setup = _make_setup(regime_metrics={"atr_percentile": 0.50})
    cfg = SMCConfig()
    result = validate(setup, _make_account(), cfg)
    assert isinstance(result, ValidatedSetup), (
        f"vol gate kabul etmeli (rank=0.50 < 0.80); aldigim: {result}"
    )


def test_volatility_regime_gate_disabled_passes_all():
    """atr_regime_filter_enabled=False -> gate atlanir."""
    setup = _make_setup(regime_metrics={"atr_percentile": 0.99})
    cfg = SMCConfig()
    cfg.atr_regime_filter_enabled = False
    result = validate(setup, _make_account(), cfg)
    assert isinstance(result, ValidatedSetup), (
        "filter disabled iken yuksek rank dahi kabul edilmeli"
    )


def test_volatility_regime_gate_missing_metrics_passes():
    """regime_metrics boş -> warm-up safe default (gate atlanir)."""
    setup = _make_setup(regime_metrics={})  # bos
    cfg = SMCConfig()
    result = validate(setup, _make_account(), cfg)
    assert isinstance(result, ValidatedSetup), (
        "regime_metrics yokken (warm-up) gate atlanmali"
    )
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/test_volatility_regime_gate.py -v`
Expected: 4 FAIL — `ImportError: cannot import name '_check_volatility_regime'` (ya da ValidatedSetup dönmesi gerekirken Rejection olmaması).

- [ ] **Step 3: Implement — `smc_engine/risk_guard.py`**

`_check_funding` fonksiyonundan SONRA (line ~180), yeni helper ekle:

```python
def _check_volatility_regime(setup: Setup, config) -> str | None:
    """Yuksek-vol rejim veto'su (Spec §13.2, 2026-05-23).

    setup.regime_metrics['atr_percentile'] mevcut H4 ATR'nin son
    config.atr_percentile_window bar'daki rolling percentile rank'i.
    Esigi asarsa SMC mekanikleri sistematik basarisiz oluyor (2026-05-23
    P3 baseline 18/18 kombo kayip — bkz docs spec).

    Disabled veya regime_metrics yoksa None doner (warm-up safe default).
    """
    if not getattr(config, "atr_regime_filter_enabled", True):
        return None
    metrics = getattr(setup, "regime_metrics", {}) or {}
    rank = metrics.get("atr_percentile")
    if rank is None:
        return None  # warm-up / eski snapshot
    threshold = getattr(config, "atr_percentile_threshold", 0.80)
    if rank > threshold:
        return (
            f"volatility regime: ATR percentile={rank:.2f} > "
            f"{threshold} — yuksek-vol chop, no trade"
        )
    return None
```

`validate()` `checks` listesinde **time-gate'ten SONRA** ekleme (line ~218-223 civarı, `asset_class == "forex"`/`"crypto"` branch'lerinden sonra `for gate, reason in checks:` döngüsünden ÖNCE):

```python
    # Volatility regime — en son gate (cheap, deterministik;
    # diger gate'lerin reason'unu maskelemesin)
    checks.append(("volatility_regime", _check_volatility_regime(setup, config)))
```

- [ ] **Step 4: Run test to verify pass**

Run: `python -m pytest tests/test_volatility_regime_gate.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add smc_engine/risk_guard.py tests/test_volatility_regime_gate.py
git commit -m "feat(risk_guard): add volatility_regime gate (ATR percentile veto)"
```

---

## Task 5: Orchestrator — H4 snapshot'a `atr_history` doldur

**Files:**
- Modify: `smc_engine/orchestrator.py:165-211` (`_run_all_detectors`)
- Test: `tests/test_atr_regime_integration.py` (yeni dosya, sadece orchestrator parçası)

- [ ] **Step 1: Failing test yaz**

```python
"""Orchestrator atr_history doldurma + builder regime_metrics + risk_guard
end-to-end integration testleri (Spec §13.2)."""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone

from smc_engine.config import SMCConfig
from smc_engine.orchestrator import analyze
from smc_engine.types import TimeFrame


def _synthetic_ohlcv(n: int, base: float = 100.0, vol: float = 1.0,
                     freq: str = "4h") -> pd.DataFrame:
    """Sentetik OHLCV — sabit volatilite, hafif trend."""
    rng = pd.date_range(
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        periods=n, freq=freq,
    )
    rs = np.random.default_rng(42)
    closes = base + np.cumsum(rs.normal(0, vol, n))
    df = pd.DataFrame({
        "open": closes + rs.normal(0, vol * 0.3, n),
        "high": closes + np.abs(rs.normal(0, vol, n)),
        "low": closes - np.abs(rs.normal(0, vol, n)),
        "close": closes,
        "volume": rs.uniform(100, 1000, n),
    }, index=rng)
    df["high"] = df[["high", "open", "close"]].max(axis=1)
    df["low"] = df[["low", "open", "close"]].min(axis=1)
    return df


def test_orchestrator_writes_atr_history_to_h4_snapshot():
    """analyze() cikarinda picture.per_tf[H4].atr_history dolu olmali."""
    h4 = _synthetic_ohlcv(200, freq="4h")
    d1 = h4.resample("1D").agg({"open": "first", "high": "max", "low": "min",
                                  "close": "last", "volume": "sum"}).dropna()
    h1 = _synthetic_ohlcv(800, freq="1h")
    m15 = _synthetic_ohlcv(200, freq="15min")

    cfg = SMCConfig()
    cfg.atr_percentile_window = 96
    data = {
        TimeFrame.D1: d1, TimeFrame.H4: h4,
        TimeFrame.H1: h1, TimeFrame.M15: m15,
    }
    picture = analyze(data, cfg, at_bar=h4.index[-1].to_pydatetime())

    snap_h4 = picture.per_tf.get(TimeFrame.H4)
    assert snap_h4 is not None
    assert snap_h4.atr_history is not None, (
        "H4 snapshot'ta atr_history doldurulmali (orchestrator gorevidir)"
    )
    assert len(snap_h4.atr_history) >= cfg.atr_percentile_window // 2, (
        f"En az window/2 bar olmali; got {len(snap_h4.atr_history)}"
    )
    # son eleman snap.atr ile ayni olmali (tutarlilik)
    assert abs(snap_h4.atr_history[-1] - snap_h4.atr) < 1e-9
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/test_atr_regime_integration.py::test_orchestrator_writes_atr_history_to_h4_snapshot -v`
Expected: FAIL — `assert None is not None` (atr_history default None, orchestrator henüz doldurmuyor)

- [ ] **Step 3: Implement — `smc_engine/orchestrator.py:199-211`**

Önce module top imports'a ekle (mevcut `from smc_engine.detectors._atr import atr as _atr` satırının yanına):

```python
from smc_engine.detectors._atr import atr_series as _atr_series
```

`TimeFrame` zaten import edilmiş (orchestrator types'tan kullanır — verifiye et: line 57 `from smc_engine.types import ... TFSnapshot ...` — TimeFrame import'ı zaten mevcut).

Mevcut `atr_val = _atr(df, atr_period) if len(df) >= 2 else 0.0` satırını ve devamını şununla değiştir:

```python
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
```

- [ ] **Step 4: Run test to verify pass**

Run: `python -m pytest tests/test_atr_regime_integration.py::test_orchestrator_writes_atr_history_to_h4_snapshot -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add smc_engine/orchestrator.py tests/test_atr_regime_integration.py
git commit -m "feat(orchestrator): populate TFSnapshot.atr_history for H4 (rolling)"
```

---

## Task 6: setup_builder — `regime_metrics["atr_percentile"]` hesaplama

**Files:**
- Modify: `smc_engine/setup_builder.py:644-659` (Setup constructor)
- Test: `tests/test_atr_regime_integration.py` (mevcut dosyaya yeni test)

- [ ] **Step 1: Failing test yaz (mevcut dosyaya ekle)**

```python
def test_build_with_diagnostics_writes_atr_percentile_to_setup():
    """build_with_diagnostics H4 atr_history'den percentile hesaplayip
    Setup.regime_metrics'e yazmali."""
    from smc_engine.setup_builder import build_with_diagnostics
    from smc_engine.types import (
        Bias, Direction, Level, POIKind, POIRef, MarketPicture,
        Range, TFSnapshot, Zone, ZoneKind, ZoneStatus, StructureBreak,
    )
    cfg = SMCConfig()
    cfg.atr_percentile_window = 96
    # atr_history: 1..100; window=96 -> recent=[5..100], current=80
    # rank = count(v<=80)/96 = 76/96 ~= 0.792
    history = [float(i) for i in range(1, 101)]
    h4 = TFSnapshot(
        range_=Range(low=90.0, high=110.0,
                     started_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                     ended_at=datetime(2024, 1, 31, tzinfo=timezone.utc)),
        bias=Bias.BULLISH,
        zones=[Zone(
            kind=ZoneKind.DEMAND, top=99.0, bottom=97.0,
            origin_candle_ts=datetime(2024, 1, 10, tzinfo=timezone.utc),
            status=ZoneStatus.FRESH, age_bars=0,
        )],
        imbalances=[], levels=[], liquidity_events=[],
        structure=[StructureBreak(direction=Direction.LONG,
                                   bar_index=10, price=99.0, kind="BOS")],
        atr=80.0,
        atr_history=history,
    )
    poi = POIRef(kind=POIKind.ZONE, ref=h4.zones[0])
    picture = MarketPicture(
        per_tf={TimeFrame.H4: h4},
        htf_bias=Bias.BULLISH,
        htf_range=h4.range_,
        active_pois=[poi],
        at_timestamp=datetime(2024, 6, 1, tzinfo=timezone.utc),
        current_price=100.0,
    )
    result = build_with_diagnostics(picture, cfg)
    assert result.setup is not None, (
        f"setup uretilmesini bekliyorum; reason={result.no_setup_reason}, "
        f"diag={result.diagnostics}"
    )
    rank = result.setup.regime_metrics.get("atr_percentile")
    assert rank is not None
    import pytest
    assert rank == pytest.approx(0.792, rel=0.02), (
        f"rank ~0.792 olmali (76/96); got {rank}"
    )
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/test_atr_regime_integration.py::test_build_with_diagnostics_writes_atr_percentile_to_setup -v`
Expected: FAIL — `rank is None` (regime_metrics boş).

- [ ] **Step 3: Implement — `smc_engine/setup_builder.py:644-659`**

`build_with_diagnostics` içinde Setup constructor öncesi (line ~644 civarı, `confirmation = _bind_confirmation(...)` satırından SONRA):

```python
    # --- 6.1. Volatility regime metrics (Spec §13.2, 2026-05-23) ---
    # h4_snap mevcut yerel degisken (line ~611, ATR okuma asamasinda alindi).
    regime_metrics: dict = {}
    history = getattr(h4_snap, "atr_history", None) if h4_snap else None
    if history:
        window = getattr(config, "atr_percentile_window", 96)
        if len(history) >= window // 2:
            recent = history[-window:]
            current_atr = float(h4_snap.atr) if h4_snap else 0.0
            rank = sum(1 for v in recent if v <= current_atr) / len(recent)
            regime_metrics["atr_percentile"] = rank
            diagnostics["atr_percentile"] = rank
```

Sonra `Setup(...)` constructor'ında `regime_metrics=regime_metrics` ekle (mevcut `confluence_factor_count=factor_count,` satırından SONRA):

```python
    setup = Setup(
        ...
        confluence_factor_count=factor_count,
        regime_metrics=regime_metrics,
    )
```

- [ ] **Step 4: Run test to verify pass**

Run: `python -m pytest tests/test_atr_regime_integration.py::test_build_with_diagnostics_writes_atr_percentile_to_setup -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add smc_engine/setup_builder.py tests/test_atr_regime_integration.py
git commit -m "feat(setup_builder): compute ATR percentile to Setup.regime_metrics"
```

---

## Task 7: End-to-end integration test

**Files:**
- Test: `tests/test_atr_regime_integration.py` (mevcut dosyaya yeni test)

- [ ] **Step 1: Failing test yaz**

```python
def test_high_vol_regime_e2e_rejection():
    """orchestrator -> setup_builder -> risk_guard zinciri yuksek-vol H4
    verisiyle volatility_regime gate'i tetiklemeli."""
    from smc_engine.risk_guard import validate
    from smc_engine.types import AccountState, Rejection

    # 100 H4 bar; ilk 80'i dusuk vol (sigma=0.5), son 20'si yuksek vol (sigma=5)
    np.random.seed(7)
    rng = pd.date_range(
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        periods=100, freq="4h",
    )
    closes = np.concatenate([
        100 + np.cumsum(np.random.normal(0, 0.5, 80)),
        100 + np.cumsum(np.random.normal(0, 5.0, 20)) + 80 * 0.0,
    ])
    high_vol_h4 = pd.DataFrame({
        "open": closes + np.random.normal(0, 0.2, 100),
        "high": closes + np.abs(np.random.normal(0, 2.0, 100)),
        "low": closes - np.abs(np.random.normal(0, 2.0, 100)),
        "close": closes,
        "volume": np.random.uniform(100, 1000, 100),
    }, index=rng)
    high_vol_h4["high"] = high_vol_h4[["high", "open", "close"]].max(axis=1)
    high_vol_h4["low"] = high_vol_h4[["low", "open", "close"]].min(axis=1)

    d1 = high_vol_h4.resample("1D").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum"
    }).dropna()
    h1 = _synthetic_ohlcv(400, freq="1h")
    m15 = _synthetic_ohlcv(100, freq="15min")

    cfg = SMCConfig()
    cfg.atr_percentile_window = 50  # daha kucuk window — sentetik veri kisa
    cfg.atr_percentile_threshold = 0.50  # son bar yuksek-vol -> rank yuksek

    data = {
        TimeFrame.D1: d1, TimeFrame.H4: high_vol_h4,
        TimeFrame.H1: h1, TimeFrame.M15: m15,
    }
    picture = analyze(data, cfg,
                      at_bar=high_vol_h4.index[-1].to_pydatetime())
    # Builder setup uretmeyebilir (sentetik veri POI'siz olabilir); o zaman
    # test atlanir — bu test wiring'i dogrular, full SMC pipeline'i degil.
    from smc_engine.setup_builder import build_with_diagnostics
    result = build_with_diagnostics(picture, cfg)
    if result.setup is None:
        import pytest
        pytest.skip(f"sentetik veride setup uretilmedi: {result.no_setup_reason}")

    # regime_metrics dolu olmali ve rank esigi asmali (son bar high vol)
    rank = result.setup.regime_metrics.get("atr_percentile")
    assert rank is not None
    assert rank > cfg.atr_percentile_threshold, (
        f"yuksek-vol son bar rank={rank} > {cfg.atr_percentile_threshold} olmali"
    )

    verdict = validate(result.setup, AccountState(
        equity=10_000.0, open_position=False,
        consecutive_losses=0, max_drawdown_pct=0.0,
    ), cfg)
    assert isinstance(verdict, Rejection)
    assert verdict.gate == "volatility_regime", (
        f"vol gate reject etmeli; gate={verdict.gate}, reason={verdict.reason}"
    )
```

- [ ] **Step 2: Run test**

Run: `python -m pytest tests/test_atr_regime_integration.py::test_high_vol_regime_e2e_rejection -v`
Expected: PASS (yukarıdaki Task 4-6 fix'leri zaten kabloladı). Eğer FAIL — wiring problemi, debug.

- [ ] **Step 3: Eğer setup üretilmediği için skip oluyorsa, sentetik veriyi POI üretecek şekilde ayarla** (zone detector için belirgin swing high/low gerek). Çoğunlukla PASS olmalı.

- [ ] **Step 4: Commit**

```bash
git add tests/test_atr_regime_integration.py
git commit -m "test: end-to-end integration test for volatility_regime gate"
```

---

## Task 8: Calibration harness — CLI flag'leri + cfg override

**Files:**
- Modify: `scripts/calibration_sweep.py:329-355` (`_RealBacktestFn.__call__`)
- Modify: `scripts/calibration_sweep.py:380-398` (`_make_real_walk_forward_fn` `run_wf`)
- Modify: `scripts/calibration_sweep.py:397-438` (`_build_arg_parser`)
- Modify: `scripts/calibration_sweep.py:441-549` (`main`, params hazırlama)
- Test: `tests/test_calibration_sweep.py` (mevcut dosyaya 2 yeni test)

- [ ] **Step 1: 2 failing test yaz**

`tests/test_calibration_sweep.py` sonuna ekle (mevcut "drawdown_breaker bypass" testlerinden sonra):

```python
# ============================================================
# ATR volatility-regime filter override — 2026-05-23 ekleme
# ============================================================


def test_calibration_backtest_accepts_atr_threshold_param(monkeypatch):
    """_RealBacktestFn params['atr_percentile_threshold'] verildiginde cfg'ye yansir."""
    mod = _load_sweep_module()
    captured: dict = {}

    def fake_harness_run(ohlcv, cfg, **kw):
        captured["atr_threshold"] = cfg.atr_percentile_threshold
        captured["atr_enabled"] = cfg.atr_regime_filter_enabled

        class _R:
            metrics = {"trade_count": 0, "win_rate": 0.0, "expectancy": 0.0,
                       "profit_factor": 0.0, "max_drawdown_pct": 0.0, "sharpe": 0.0}
        return _R()

    import backtest.harness as harness_module
    monkeypatch.setattr(harness_module, "run", fake_harness_run)

    fn = mod._RealBacktestFn(m15_window=10, m15_offset=0, m15_lookback=5)
    fn._ohlcv = {"M15": "dummy"}
    fn({
        "sl_min_atr_multiple": 0.5, "sl_band_buffer_mult": 0.25,
        "atr_percentile_threshold": 0.70,
    })
    assert captured["atr_threshold"] == 0.70


def test_calibration_backtest_atr_regime_disabled_flag(monkeypatch):
    """_RealBacktestFn params['atr_regime_filter_enabled']=False cfg'ye yansir."""
    mod = _load_sweep_module()
    captured: dict = {}

    def fake_harness_run(ohlcv, cfg, **kw):
        captured["atr_enabled"] = cfg.atr_regime_filter_enabled

        class _R:
            metrics = {"trade_count": 0, "win_rate": 0.0, "expectancy": 0.0,
                       "profit_factor": 0.0, "max_drawdown_pct": 0.0, "sharpe": 0.0}
        return _R()

    import backtest.harness as harness_module
    monkeypatch.setattr(harness_module, "run", fake_harness_run)

    fn = mod._RealBacktestFn(m15_window=10, m15_offset=0, m15_lookback=5)
    fn._ohlcv = {"M15": "dummy"}
    fn({
        "sl_min_atr_multiple": 0.5, "sl_band_buffer_mult": 0.25,
        "atr_regime_filter_enabled": False,
    })
    assert captured["atr_enabled"] is False
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/test_calibration_sweep.py::test_calibration_backtest_accepts_atr_threshold_param -v`
Expected: FAIL — `captured["atr_threshold"]` default 0.80 (params ignore edildi)

- [ ] **Step 3: Implement — `_RealBacktestFn.__call__` (line ~329-355)**

`cfg.sl_band_buffer_mult = params["sl_band_buffer_mult"]` satırından SONRA ekle:

```python
        # ATR regime filter overrides (Spec §13.2, 2026-05-23) — opsiyonel
        if "atr_percentile_threshold" in params:
            cfg.atr_percentile_threshold = params["atr_percentile_threshold"]
        if "atr_regime_filter_enabled" in params:
            cfg.atr_regime_filter_enabled = params["atr_regime_filter_enabled"]
```

`_make_real_walk_forward_fn` içindeki `run_wf` closure'ında aynı bloğu ekle (line ~380-398), `cfg.sl_band_buffer_mult = ...` satırından sonra.

- [ ] **Step 4: Run test to verify pass**

Run: `python -m pytest tests/test_calibration_sweep.py::test_calibration_backtest_accepts_atr_threshold_param tests/test_calibration_sweep.py::test_calibration_backtest_atr_regime_disabled_flag -v`
Expected: 2 passed

- [ ] **Step 5: CLI flag'leri ekle — `_build_arg_parser` (line ~397-438)**

`--baseline-trade-count` satırından önce ekle:

```python
    # Volatility regime filter (Spec §13.2, 2026-05-23)
    p.add_argument("--atr-percentile-threshold", default=None,
                   help="ATR percentile veto threshold (sweep grid, "
                        "comma-separated). Verilmezse SMCConfig default "
                        "(0.80) tek kombo kullanilir.")
    p.add_argument("--atr-regime-disabled", action="store_true",
                   help="Volatility regime gate'i KAPALI olarak calistir "
                        "(baseline karsilastirmasi icin).")
```

- [ ] **Step 6: Grid'e bağla — `main()` (line ~445)**

`build_param_grid` çağrısını şu şekilde modifiye et:

```python
    atr_thresholds = (
        _parse_float_list(args.atr_percentile_threshold)
        if args.atr_percentile_threshold else [None]
    )
    enabled_flag = not args.atr_regime_disabled

    grid = build_param_grid(
        sl_min_atr_multiple=_parse_float_list(args.sl_min_atr),
        sl_band_buffer_mult=_parse_float_list(args.sl_band_buffer),
    )
    # ATR threshold sweep 3'uncu eksen — opsiyonel.
    # NOT: enabled_flag her zaman CSV'ye yazilir (observability — kullanici
    # her satirin filter durumunu gorebilsin).
    new_grid = []
    for combo in grid:
        for thr in atr_thresholds:
            cell = dict(combo)
            if thr is not None:
                cell["atr_percentile_threshold"] = thr
            cell["atr_regime_filter_enabled"] = enabled_flag
            new_grid.append(cell)
    grid = new_grid
```

`_CSV_COLUMNS`'a sweep ekseni eklendiğinde CSV başlığı uyumlu olmalı — mevcut `sweep_rows_to_csv` zaten union-of-keys mantığı kullanıyor (line 124-149); ek kolon otomatik düşer.

- [ ] **Step 7: Smoke test çalıştır**

Run (PowerShell — Windows ortamı; `--m15-window 250` "250-bar tuzağı" memory'sinden ötürü kullanılmıyor):
```powershell
python scripts/calibration_sweep.py `
    --sl-min-atr 0.5 --sl-band-buffer 0.25 `
    --atr-percentile-threshold 0.70,0.80 `
    --workers 1 `
    --m15-offset 0 --m15-window 1000 `
    --out c:/tmp/smoke_atr.csv
```
Expected: 2 row CSV; her satırda `atr_percentile_threshold` ve `atr_regime_filter_enabled` kolonu var.

- [ ] **Step 8: Commit**

```bash
git add scripts/calibration_sweep.py tests/test_calibration_sweep.py
git commit -m "feat(calibration): --atr-percentile-threshold + --atr-regime-disabled flags"
```

---

## Task 9: Tam regression suite

- [ ] **Step 1: Tüm testleri çalıştır**

Run: `python -m pytest --tb=short -q`
Expected: **660 baseline + 15 yeni test = 675 passed**, 1 skipped (mevcut). Kırılan yok.

Yeni test sayımı:
- Task 1: 2 (config defaults + yaml override)
- Task 2: 2 (TFSnapshot.atr_history default + stores list)
- Task 3: 2 (Setup.regime_metrics default + independent instances)
- Task 4: 4 (gate vetos/admits/disabled/missing)
- Task 5: 1 (orchestrator writes atr_history)
- Task 6: 1 (builder writes regime_metrics)
- Task 7: 1 (e2e integration)
- Task 8: 2 (calibration accepts threshold + disabled flag)
- Toplam: 15

- [ ] **Step 2: Eğer mevcut testler kırıldıysa**

Muhtemel kırılma noktaları:
- `tests/test_risk_guard*.py` — gate sayısı veya order assertion'ı
- `tests/test_setup_builder*.py` — Setup(...) constructor pozisyonel arg sırası
- `tests/test_orchestrator*.py` — TFSnapshot construction

Her birini fix et (geriye uyumluluk — `default_factory` ve `Optional` default'lar sayesinde minor). Yeni test eklemen değil mevcut testlerin pasifleşmesini sağlamak.

- [ ] **Step 3: Commit (regression fix gerekirse)**

```bash
git add tests/...
git commit -m "test: backward-compat fixes for atr_history/regime_metrics additions"
```

---

## Task 10: Validation sweep (P1/P2/P3 × filter on/off)

**Files:**
- Output: `logs/calibration/sweep-{P1,P2,P3}-2026-05-24-{filter-off,filter-on}.csv`

- [ ] **Step 1: Baseline (filter-off) yeniden çalıştır**

Mevcut 2026-05-23 sweep zaten filter-off baseline; ama production default `atr_regime_filter_enabled=True` olduğu için kalibrasyon harness'ı şimdi YENI default'la çalışır. Filter-off karşılaştırması için `--atr-regime-disabled` ile yeniden çalıştır:

Orkestratör scripti yarat (Windows PowerShell ortamı için Python — bash heredoc yok):

```python
# c:/tmp/run_validation_2026_05_24.py
import subprocess, sys
from pathlib import Path
REPO = Path(r"c:/Users/utkuc/OneDrive/Masaüstü/smc-engine")
OUTDIR = REPO / "logs" / "calibration"
WINDOWS = [("P1", 2000), ("P2", 16000), ("P3", 29900)]
COMMON = [
    "--m15-window", "8000",
    "--sl-min-atr", "0.25,0.30,0.35,0.40,0.45,0.50",
    "--sl-band-buffer", "0.25,0.375,0.50",
    "--workers", "18",
]
for label, offset in WINDOWS:
    for tag, extra in [("filter-off", ["--atr-regime-disabled"]),
                       ("filter-on", [])]:
        out = OUTDIR / f"sweep-{label}-2026-05-24-{tag}.csv"
        cmd = [sys.executable, str(REPO / "scripts" / "calibration_sweep.py"),
               "--m15-offset", str(offset), *COMMON, *extra, "--out", str(out)]
        print(f"[{label}/{tag}] {' '.join(cmd)}", flush=True)
        rc = subprocess.run(cmd, cwd=str(REPO)).returncode
        if rc != 0: sys.exit(rc)
print("TUM PENCERELER TAMAM")
```

Run: `python c:/tmp/run_validation_2026_05_24.py` (PowerShell veya background).
Expected: 6 CSV (3 pencere × 2 mod), ~30-90 dk toplam.

- [ ] **Step 2: (Birleştirildi Step 1 ile — Python orkestratör hem filter-off hem filter-on koşar.)**

- [ ] **Step 3: Cross-window analiz**

`c:/tmp/analyze_2026_05_23.py` benzeri scripti yeni CSV'ler için adapte et veya hızlı manuel analiz: filter-on vs filter-off her pencere için trade-count, exp, pf delta.

**Beklenen P3**: trade-count %50+ düşmeli; expectancy negatif → 0'a yaklaşmalı.
**Beklenen P1/P2**: marjinal etki.

Eğer beklentiler tutmazsa: hipotez yanlış, kararı kullanıcıya götür (fix geri alma veya farklı threshold).

- [ ] **Step 4: Sonuçları memory'ye yaz**

`C:\Users\utkuc\.claude\projects\C--Users-utkuc-OneDrive-Masa-st--smc-engine\memory\project_atr_regime_filter_2026_05_24.md` oluştur:

```markdown
---
name: atr-regime-filter-validation-2026-05-24
description: ATR percentile rejim filtresi validation sweep sonuclari (filter-on vs filter-off P1/P2/P3)
metadata:
  node_type: memory
  type: project
---

# ATR Rejim Filtresi Validation — 2026-05-24

## Filter-on vs filter-off karsilastirmasi (kombo sl_min=0.4/buf=0.375)

| Pencere | filter-off tc/exp/pf | filter-on tc/exp/pf | Delta |
|---------|----------------------|---------------------|-------|
| P1 | ... | ... | ... |
| P2 | ... | ... | ... |
| P3 | ... | ... | ... |

[Sonuc yorumlama]

## CP3 degerlendirmesi

[3 pencerede pf>=1.5 + exp>0 saglanan kombo var mi?]

## Sıradaki adım

[Hipotez 2 (TP rejim-adaptif) veya farkli yon?]
```

`MEMORY.md` index'i de güncelle.

- [ ] **Step 5: Push + verify**

```bash
git push origin main
git log origin/main --oneline -3
```

---

## Task 11: Final commit + cleanup

- [ ] **Step 1: TaskList kontrol**

`TaskList` ile tüm task'leri completed işaretli mi kontrol et.

- [ ] **Step 2: Final regression**

Run: `python -m pytest -q`
Expected: 673+ passed.

- [ ] **Step 3: Memory MEMORY.md güncelle**

`atr-regime-filter-validation-2026-05-24` linkini ekle.

---

## Files Touched Summary

```
smc_engine/config.py                         (+5 lines)
smc_engine/types.py                          (+10 lines)
smc_engine/orchestrator.py                   (modify _run_all_detectors, +15 lines)
smc_engine/setup_builder.py                  (+15 lines)
smc_engine/risk_guard.py                     (+30 lines)
scripts/calibration_sweep.py                 (+25 lines)
tests/test_config_atr_regime.py              (NEW, ~30 lines)
tests/test_atr_regime_types.py               (NEW, ~50 lines)
tests/test_volatility_regime_gate.py         (NEW, ~80 lines)
tests/test_atr_regime_integration.py         (NEW, ~120 lines)
tests/test_calibration_sweep.py              (+50 lines)
docs/superpowers/specs/2026-05-23-...md      (already committed)
docs/superpowers/plans/2026-05-23-...md      (THIS FILE)
```

Total: ~430 satır ekleme, 0 silme. Geriye uyumluluk korunmuş.

## Acceptance Criteria (spec'ten)

- [x] (planlanmış) 8 yeni TDD test RED → GREEN (Task 4: 4, Task 5-6: 2, Task 8: 2 = 8)
- [x] (planlanmış) Regression 660 → 673 passed
- [x] (planlanmış) `Setup(...)` ve `TFSnapshot(...)` constructor backward-compat (Task 2-3)
- [x] (planlanmış) End-to-end integration test (Task 7)
- [x] (planlanmış) CLI parse uyumu (Task 8 Step 7 smoke)
- [x] (planlanmış) Validation sweep + memory (Task 10)
- [x] (planlanmış) Commit + push verify
