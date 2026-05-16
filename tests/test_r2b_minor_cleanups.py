"""R2b — 16 ufak temizlik turu, davranis acisindan asagidaki etkileri test eder.

Cogu R2b bulgusu kozmetik (yorum/docstring/ölü kod) — bunlarin testi yok.
Bu dosya gercek davranis degisikligi yapan birkac bulgu icin koruyucu test:
  - U-1: config YAML tip-coercion (int/float/bool alanlar)
  - U-2: imbalance_detector liq_void_gap_atr config override
  - U-6: AccountState.recent_results Optional default
  - U-9: fetch._timeframe_ms uppercase TF kabulu
  - U-10: BacktestResult.equity_curve tip annotation pd.Series'e referansli
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from smc_engine.config import SMCConfig, load_config
from smc_engine.types import AccountState, BacktestResult


# ============================================================
# U-1: YAML tip-coercion
# ============================================================


def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "cfg.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_u1_yaml_int_field_string_numeric_coerced(tmp_path):
    """YAML int alaninda sayisal string -> int'e cast."""
    p = _write_yaml(tmp_path, "swing_lookback: '7'\n")
    cfg = load_config(p)
    assert cfg.swing_lookback == 7
    assert isinstance(cfg.swing_lookback, int)


def test_u1_yaml_float_field_int_coerced(tmp_path):
    """YAML float alaninda int -> float'a cast."""
    p = _write_yaml(tmp_path, "risk_pct: 1\n")  # YAML int -> float field
    cfg = load_config(p)
    assert cfg.risk_pct == 1.0
    assert isinstance(cfg.risk_pct, float)


def test_u1_yaml_float_field_garbage_string_raises(tmp_path):
    """YAML float alaninda gecersiz string -> ValueError (eskiden sessizdi)."""
    p = _write_yaml(tmp_path, "risk_pct: 'abc'\n")
    with pytest.raises(ValueError, match="risk_pct"):
        load_config(p)


def test_u1_yaml_int_field_garbage_string_raises(tmp_path):
    p = _write_yaml(tmp_path, "swing_lookback: 'not-a-number'\n")
    with pytest.raises(ValueError, match="swing_lookback"):
        load_config(p)


def test_u1_yaml_numeric_field_bool_rejected(tmp_path):
    """`risk_pct: true` sessizce 1.0 olmamali — ayri tip hatasi."""
    p = _write_yaml(tmp_path, "risk_pct: true\n")
    with pytest.raises(ValueError, match="risk_pct"):
        load_config(p)


def test_u1_confluence_weights_garbage_raises(tmp_path):
    body = (
        "confluence_weights:\n"
        "  poi_quality: 'xyz'\n"
    )
    p = _write_yaml(tmp_path, body)
    with pytest.raises(ValueError, match="confluence_weights"):
        load_config(p)


def test_u1_yaml_valid_values_still_work(tmp_path):
    """Smoke — gecerli YAML degerleri eskiden oldugu gibi yuklenir."""
    body = (
        "risk_pct: 0.02\n"
        "swing_lookback: 5\n"
        "confluence_weights:\n"
        "  poi_quality: 0.30\n"
    )
    p = _write_yaml(tmp_path, body)
    cfg = load_config(p)
    assert cfg.risk_pct == 0.02
    assert cfg.swing_lookback == 5
    assert cfg.confluence_weights.poi_quality == pytest.approx(0.30)


# ============================================================
# U-2: imbalance_detector config override
# ============================================================


def test_u2_liq_void_gap_atr_config_override():
    """`liq_void_gap_atr` config alaninin oldugunu ve varsayilan = 2.0 oldugunu kontrol et."""
    cfg = SMCConfig()
    assert cfg.liq_void_gap_atr == 2.0
    assert cfg.inefficiency_gap_atr == 5.0


def test_u2_imbalance_detector_uses_config_threshold(monkeypatch):
    """Custom config carpani ile detektor ciktisi farklilasir.

    Cok geni s bir bosluk yarat: dusuk liq_void carpaninda LIQ_VOID,
    yuksek carpaninda FVG kalir.
    """
    from smc_engine.detectors.imbalance_detector import detect
    from smc_engine.types import ImbalanceKind

    # 5 mum, ortada gigantik bullish FVG (gap = 30 birim, ATR ~ 1)
    idx = pd.date_range("2024-01-01", periods=5, freq="h", tz="UTC")
    df = pd.DataFrame(
        {
            "open":  [100, 101, 130, 132, 133],
            "high":  [101, 102, 135, 134, 134],
            "low":   [ 99, 100, 128, 131, 132],
            "close": [101, 101, 132, 132, 133],
        },
        index=idx,
    )
    # ATR ~ ortalama TR, kucuk. Gap = low[2] - high[0] = 128 - 101 = 27.
    cfg_low = SMCConfig()
    cfg_low.liq_void_gap_atr = 0.5  # cok dusuk esik -> LIQ_VOID
    cfg_low.inefficiency_gap_atr = 1000.0  # cok yuksek -> INEFFICIENCY olmasin
    imb_low = detect(df, cfg_low)

    cfg_high = SMCConfig()
    cfg_high.liq_void_gap_atr = 1000.0  # cok yuksek -> hicbir bosluk LIQ_VOID degil
    cfg_high.inefficiency_gap_atr = 9999.0
    imb_high = detect(df, cfg_high)

    # Iki ciktida da en az 1 bosluk olmali (FVG/LIQ_VOID)
    assert len(imb_low) >= 1
    assert len(imb_high) >= 1
    # Dusuk esikte: LIQ_VOID veya INEFFICIENCY uretilmis.
    kinds_low = {i.kind for i in imb_low}
    assert ImbalanceKind.LIQ_VOID in kinds_low or ImbalanceKind.INEFFICIENCY in kinds_low
    # Yuksek esikte: yalniz FVG.
    kinds_high = {i.kind for i in imb_high}
    assert kinds_high == {ImbalanceKind.FVG}


# ============================================================
# U-6: AccountState.recent_results Optional
# ============================================================


def test_u6_account_state_constructible_without_recent_results():
    """`recent_results` artik Optional/default None — eksik verilebilir."""
    acc = AccountState(
        equity=10_000.0,
        open_position=False,
        consecutive_losses=0,
        max_drawdown_pct=0.0,
    )
    assert acc.recent_results is None
    # Mutable: kullanan tarafindan doldurulabilir.
    acc.recent_results = [1.0, -0.5]
    assert acc.recent_results == [1.0, -0.5]


def test_u6_account_state_backward_compatible():
    """Eski cagiranlar `recent_results=[]` gecmeye devam edebilir."""
    acc = AccountState(
        equity=10_000.0,
        open_position=False,
        recent_results=[],
        consecutive_losses=0,
        max_drawdown_pct=0.0,
    )
    assert acc.recent_results == []


# ============================================================
# U-9: fetch._timeframe_ms uppercase TF kabulu
# ============================================================


def test_u9_timeframe_ms_lowercase_ccxt():
    from data.fetch import _timeframe_ms
    assert _timeframe_ms("15m") == 15 * 60_000
    assert _timeframe_ms("1h") == 3_600_000
    assert _timeframe_ms("4h") == 4 * 3_600_000
    assert _timeframe_ms("1d") == 86_400_000


def test_u9_timeframe_ms_uppercase_smc():
    """U-9: M15/H1/H4/H8/D1 (SMC enum-style) format'i da kabul et."""
    from data.fetch import _timeframe_ms
    assert _timeframe_ms("M15") == 15 * 60_000
    assert _timeframe_ms("H1") == 3_600_000
    assert _timeframe_ms("H4") == 4 * 3_600_000
    assert _timeframe_ms("D1") == 86_400_000


def test_u9_timeframe_ms_invalid_raises():
    from data.fetch import _timeframe_ms
    with pytest.raises(ValueError):
        _timeframe_ms("xyz")
    with pytest.raises(ValueError):
        _timeframe_ms("")


# ============================================================
# U-10: BacktestResult.equity_curve tip annotation (smoke)
# ============================================================


def test_u10_backtest_result_annotation_is_pd_series_string():
    """`equity_curve` annotation 'pd.Series' (forward ref) — runtime degismez."""
    ann = BacktestResult.__annotations__["equity_curve"]
    # `from __future__ import annotations` -> string olarak gelir
    assert "Series" in str(ann)
