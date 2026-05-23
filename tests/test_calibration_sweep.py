"""scripts/calibration_sweep.py testleri (SL geometry kalibrasyon harness).

run_backtest_fn injection ile — gerçek backtest çağrılmaz, deterministik
mock kullanılır. CLI parquet-yükleme tarafı ayrı (smoke, bash gibi).
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest


def _load_sweep_module():
    """scripts/calibration_sweep.py'yi modül olarak yükle.

    ``scripts/`` sys.path'e eklenir ve ``import calibration_sweep`` ile
    yüklenir — böylece modül adıyla yeniden import edilebilir. Bu,
    ProcessPoolExecutor worker'larının modül içindeki sınıf/fonksiyonları
    (``_RealBacktestFn``, ``_run_one_combo``) pickle ile çözebilmesi için
    zorunludur (spec_from_file_location ile yüklenen modül adıyla import
    edilemez → pickle "No module named" hatası).
    """
    import sys

    repo_root = Path(__file__).resolve().parent.parent
    scripts_dir = str(repo_root / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import calibration_sweep  # noqa: E402
    return calibration_sweep


# ============================================================
# build_param_grid
# ============================================================


def test_build_param_grid_cartesian_product():
    """2 sl_min_atr × 3 sl_band_buffer → 6 kombinasyon."""
    mod = _load_sweep_module()
    grid = mod.build_param_grid(
        sl_min_atr_multiple=[0.25, 0.5],
        sl_band_buffer_mult=[0.25, 0.375, 0.5],
    )
    assert len(grid) == 6
    # Her eleman iki anahtarı da içermeli
    for combo in grid:
        assert "sl_min_atr_multiple" in combo
        assert "sl_band_buffer_mult" in combo
    # Belirli bir kombinasyon var mı
    assert {"sl_min_atr_multiple": 0.25, "sl_band_buffer_mult": 0.5} in grid


def test_build_param_grid_single_values():
    """Tek değerli listeler → 1 kombinasyon."""
    mod = _load_sweep_module()
    grid = mod.build_param_grid(
        sl_min_atr_multiple=[0.5], sl_band_buffer_mult=[0.25],
    )
    assert grid == [{"sl_min_atr_multiple": 0.5, "sl_band_buffer_mult": 0.25}]


# ============================================================
# run_sweep — run_backtest_fn injection
# ============================================================


def test_run_sweep_calls_backtest_per_combo_and_collects_metrics():
    """Her grid kombinasyonu için run_backtest_fn çağrılır; metrikler toplanır."""
    mod = _load_sweep_module()
    grid = mod.build_param_grid(
        sl_min_atr_multiple=[0.25, 0.5], sl_band_buffer_mult=[0.25],
    )
    calls = []

    def fake_backtest(params):
        calls.append(params)
        # Deterministik sahte metrikler — sl_min_atr küçükse daha çok trade
        return {
            "trade_count": 20 if params["sl_min_atr_multiple"] == 0.25 else 5,
            "win_rate": 0.50,
            "expectancy": 0.30,
            "profit_factor": 1.5,
            "max_drawdown_pct": 0.10,
            "sharpe": 1.2,
        }

    rows = mod.run_sweep(grid, fake_backtest)
    assert len(rows) == 2
    assert len(calls) == 2
    # Her satır params + metrikleri birlikte taşımalı
    for row in rows:
        assert "sl_min_atr_multiple" in row
        assert "sl_band_buffer_mult" in row
        assert "trade_count" in row
        assert "win_rate" in row
        assert "expectancy" in row


def test_run_sweep_backtest_failure_recorded_not_crash():
    """run_backtest_fn bir kombinasyonda exception fırlatırsa sweep çökmemeli;
    o satır error işaretli kaydedilir, diğerleri devam eder."""
    mod = _load_sweep_module()
    grid = mod.build_param_grid(
        sl_min_atr_multiple=[0.25, 0.5], sl_band_buffer_mult=[0.25],
    )

    def flaky_backtest(params):
        if params["sl_min_atr_multiple"] == 0.25:
            raise RuntimeError("simulated backtest failure")
        return {
            "trade_count": 5, "win_rate": 0.5, "expectancy": 0.3,
            "profit_factor": 1.5, "max_drawdown_pct": 0.1, "sharpe": 1.0,
        }

    rows = mod.run_sweep(grid, flaky_backtest)
    assert len(rows) == 2  # ikisi de kaydedildi
    errored = [r for r in rows if r.get("error")]
    ok = [r for r in rows if not r.get("error")]
    assert len(errored) == 1
    assert len(ok) == 1
    assert "simulated backtest failure" in errored[0]["error"]


# ============================================================
# sweep_rows_to_csv
# ============================================================


def test_sweep_rows_to_csv_writes_header_and_rows(tmp_path):
    """CSV: header + her satır params+metrikler."""
    mod = _load_sweep_module()
    rows = [
        {"sl_min_atr_multiple": 0.25, "sl_band_buffer_mult": 0.25,
         "trade_count": 20, "win_rate": 0.5, "expectancy": 0.3,
         "profit_factor": 1.5, "max_drawdown_pct": 0.1, "sharpe": 1.2},
        {"sl_min_atr_multiple": 0.5, "sl_band_buffer_mult": 0.25,
         "trade_count": 5, "win_rate": 0.6, "expectancy": 0.4,
         "profit_factor": 2.0, "max_drawdown_pct": 0.08, "sharpe": 1.5},
    ]
    out = tmp_path / "results.csv"
    mod.sweep_rows_to_csv(rows, out)
    assert out.exists()
    with out.open(newline="", encoding="utf-8") as fh:
        reader = list(csv.DictReader(fh))
    assert len(reader) == 2
    assert reader[0]["sl_min_atr_multiple"] == "0.25"
    assert reader[0]["trade_count"] == "20"
    assert reader[1]["win_rate"] == "0.6"


def test_sweep_rows_to_csv_empty_rows_writes_nothing_or_header(tmp_path):
    """Boş satır listesi → çökmemeli."""
    mod = _load_sweep_module()
    out = tmp_path / "empty.csv"
    mod.sweep_rows_to_csv([], out)
    # Dosya oluşmuş olabilir (sadece header) ya da hiç — çökmemesi yeterli
    assert True


# ============================================================
# select_top_candidates — ratchet kuralı
# ============================================================


def test_select_top_candidates_ratchet_rule():
    """Ratchet: expectancy >= baseline VE trade_count baseline'dan fazla VE
    max_dd <= baseline*1.2 olan kombinasyonlar kabul edilir."""
    mod = _load_sweep_module()
    baseline_expectancy = 0.30
    baseline_max_dd = 0.10
    baseline_trade_count = 5
    rows = [
        # Kabul: expectancy korunmuş, trade arttı, dd sınırda
        {"sl_min_atr_multiple": 0.25, "sl_band_buffer_mult": 0.5,
         "trade_count": 20, "expectancy": 0.32, "max_drawdown_pct": 0.11,
         "win_rate": 0.5},
        # Red: expectancy düştü
        {"sl_min_atr_multiple": 0.25, "sl_band_buffer_mult": 0.25,
         "trade_count": 25, "expectancy": 0.20, "max_drawdown_pct": 0.10,
         "win_rate": 0.45},
        # Red: max_dd baseline*1.2'yi (0.12) aştı
        {"sl_min_atr_multiple": 0.30, "sl_band_buffer_mult": 0.25,
         "trade_count": 18, "expectancy": 0.35, "max_drawdown_pct": 0.15,
         "win_rate": 0.5},
        # Red: trade count artmadı (setup akışı açılmadı)
        {"sl_min_atr_multiple": 0.5, "sl_band_buffer_mult": 0.25,
         "trade_count": 5, "expectancy": 0.40, "max_drawdown_pct": 0.08,
         "win_rate": 0.6},
    ]
    accepted = mod.select_top_candidates(
        rows, n=3,
        baseline_expectancy=baseline_expectancy,
        baseline_max_dd=baseline_max_dd,
        baseline_trade_count=baseline_trade_count,
    )
    # Sadece ilk satır ratchet'i geçer
    assert len(accepted) == 1
    assert accepted[0]["sl_min_atr_multiple"] == 0.25
    assert accepted[0]["sl_band_buffer_mult"] == 0.5


def test_select_top_candidates_skips_errored_rows():
    """error işaretli satırlar ratchet'e girmeden elenmeli."""
    mod = _load_sweep_module()
    rows = [
        {"sl_min_atr_multiple": 0.25, "sl_band_buffer_mult": 0.5,
         "error": "backtest failed"},
        {"sl_min_atr_multiple": 0.3, "sl_band_buffer_mult": 0.25,
         "trade_count": 20, "expectancy": 0.35, "max_drawdown_pct": 0.10,
         "win_rate": 0.5},
    ]
    accepted = mod.select_top_candidates(
        rows, n=5, baseline_expectancy=0.30, baseline_max_dd=0.10,
        baseline_trade_count=5,
    )
    # Errored satır atlanır; sağlam satır ratchet'i geçer
    assert all(not r.get("error") for r in accepted)
    assert len(accepted) == 1


def test_select_top_candidates_ranks_by_expectancy_then_trade_count():
    """Birden çok kabul → expectancy'ye göre sırala (yüksek önce)."""
    mod = _load_sweep_module()
    rows = [
        {"sl_min_atr_multiple": 0.30, "sl_band_buffer_mult": 0.5,
         "trade_count": 15, "expectancy": 0.33, "max_drawdown_pct": 0.10,
         "win_rate": 0.5},
        {"sl_min_atr_multiple": 0.25, "sl_band_buffer_mult": 0.5,
         "trade_count": 20, "expectancy": 0.40, "max_drawdown_pct": 0.11,
         "win_rate": 0.52},
    ]
    accepted = mod.select_top_candidates(
        rows, n=5, baseline_expectancy=0.30, baseline_max_dd=0.10,
        baseline_trade_count=5,
    )
    assert len(accepted) == 2
    # En yüksek expectancy ilk sırada
    assert accepted[0]["expectancy"] == 0.40


# ============================================================
# config sl param override — handoff Adım 5.4 test_config_sl_params_override
# ============================================================


def test_config_sl_params_are_overridable():
    """sl_min_atr_multiple + sl_band_buffer_mult SMCConfig field'ı; sweep
    harness bunları runtime'da set edebilmeli (config exposure ZATEN var,
    refactor gerekmedi — bu test o varsayımı kilitler)."""
    from smc_engine.config import SMCConfig
    cfg = SMCConfig()
    # Field'lar mevcut + yazılabilir
    cfg.sl_min_atr_multiple = 0.3
    cfg.sl_band_buffer_mult = 0.5
    assert cfg.sl_min_atr_multiple == 0.3
    assert cfg.sl_band_buffer_mult == 0.5


def test_config_sl_params_yaml_override():
    """config.yaml'dan sl param override edilebilir (load_config düz scalar)."""
    import tempfile
    import os
    from smc_engine.config import load_config
    yaml_content = "sl_min_atr_multiple: 0.35\nsl_band_buffer_mult: 0.45\n"
    fd, path = tempfile.mkstemp(suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(yaml_content)
        cfg = load_config(path)
        assert cfg.sl_min_atr_multiple == 0.35
        assert cfg.sl_band_buffer_mult == 0.45
    finally:
        os.unlink(path)


# ============================================================
# run_walk_forward_validation — handoff Adım 5.3
# En iyi adayları out-of-sample doğrula; overfit ele.
# ============================================================


def test_run_walk_forward_validation_calls_wf_per_candidate():
    """Her aday kombinasyon için walk_forward çağrılır; out-of-sample
    (test_metrics) ortalaması toplanır."""
    mod = _load_sweep_module()
    candidates = [
        {"sl_min_atr_multiple": 0.25, "sl_band_buffer_mult": 0.5,
         "expectancy": 0.40, "trade_count": 20},
        {"sl_min_atr_multiple": 0.30, "sl_band_buffer_mult": 0.5,
         "expectancy": 0.33, "trade_count": 15},
    ]
    wf_calls = []

    def fake_wf(params):
        wf_calls.append(params)
        # 2 pencere — her birinin test_metrics'i
        return [
            {"test_metrics": {"expectancy": 0.35, "trade_count": 8,
                              "win_rate": 0.5}},
            {"test_metrics": {"expectancy": 0.30, "trade_count": 7,
                              "win_rate": 0.48}},
        ]

    results = mod.run_walk_forward_validation(candidates, fake_wf)
    assert len(results) == 2
    assert len(wf_calls) == 2
    # Her sonuç in-sample expectancy + out-of-sample ortalama içermeli
    for r in results:
        assert "in_sample_expectancy" in r
        assert "oos_expectancy_mean" in r
        assert "oos_window_count" in r
    # İlk aday: oos expectancy ort = (0.35+0.30)/2 = 0.325
    assert abs(results[0]["oos_expectancy_mean"] - 0.325) < 1e-9
    assert results[0]["oos_window_count"] == 2


def test_run_walk_forward_validation_flags_overfit():
    """OOS expectancy in-sample'ın belirgin altındaysa overfit işaretle."""
    mod = _load_sweep_module()
    candidates = [
        # in-sample 0.40 ama OOS çöküyor → overfit
        {"sl_min_atr_multiple": 0.25, "sl_band_buffer_mult": 0.5,
         "expectancy": 0.40, "trade_count": 20},
    ]

    def fake_wf(params):
        return [
            {"test_metrics": {"expectancy": 0.05, "trade_count": 3,
                              "win_rate": 0.3}},
            {"test_metrics": {"expectancy": 0.02, "trade_count": 2,
                              "win_rate": 0.3}},
        ]

    results = mod.run_walk_forward_validation(candidates, fake_wf)
    assert results[0]["overfit"] is True


def test_run_walk_forward_validation_no_overfit_when_oos_holds():
    """OOS expectancy in-sample'a yakınsa overfit DEĞİL."""
    mod = _load_sweep_module()
    candidates = [
        {"sl_min_atr_multiple": 0.30, "sl_band_buffer_mult": 0.5,
         "expectancy": 0.33, "trade_count": 15},
    ]

    def fake_wf(params):
        return [
            {"test_metrics": {"expectancy": 0.31, "trade_count": 7,
                              "win_rate": 0.5}},
            {"test_metrics": {"expectancy": 0.30, "trade_count": 6,
                              "win_rate": 0.49}},
        ]

    results = mod.run_walk_forward_validation(candidates, fake_wf)
    assert results[0]["overfit"] is False


def test_run_walk_forward_validation_computes_oos_profit_factor():
    """OOS test dilimlerinin profit_factor ortalaması raporlanmalı.

    expectancy outlier'a esir (Soru 4) — OOS pf bağımsız teyit metriği.
    """
    mod = _load_sweep_module()
    candidates = [
        {"sl_min_atr_multiple": 0.35, "sl_band_buffer_mult": 0.375,
         "expectancy": 1.10, "profit_factor": 1.44, "trade_count": 20},
    ]

    def fake_wf(params):
        return [
            {"test_metrics": {"expectancy": 0.30, "profit_factor": 1.6,
                              "trade_count": 8, "win_rate": 0.5}},
            {"test_metrics": {"expectancy": 0.20, "profit_factor": 1.2,
                              "trade_count": 6, "win_rate": 0.48}},
        ]

    results = mod.run_walk_forward_validation(candidates, fake_wf)
    assert "oos_profit_factor_mean" in results[0]
    # OOS pf ort = (1.6 + 1.2) / 2 = 1.4
    assert abs(results[0]["oos_profit_factor_mean"] - 1.4) < 1e-9


def test_run_walk_forward_validation_oos_pf_missing_metric_defaults_zero():
    """test_metrics'te profit_factor yoksa 0.0 sayılır (crash etmez)."""
    mod = _load_sweep_module()
    candidates = [
        {"sl_min_atr_multiple": 0.25, "sl_band_buffer_mult": 0.5,
         "expectancy": 0.30, "trade_count": 10},
    ]

    def fake_wf(params):
        # profit_factor anahtarı YOK
        return [{"test_metrics": {"expectancy": 0.28, "trade_count": 5}}]

    results = mod.run_walk_forward_validation(candidates, fake_wf)
    assert results[0]["oos_profit_factor_mean"] == 0.0


def test_run_walk_forward_validation_preserves_existing_fields():
    """OOS pf eklenince mevcut alanlar (exp, overfit) bozulmamalı."""
    mod = _load_sweep_module()
    candidates = [
        {"sl_min_atr_multiple": 0.30, "sl_band_buffer_mult": 0.25,
         "expectancy": 0.40, "profit_factor": 1.7, "trade_count": 20},
    ]

    def fake_wf(params):
        return [
            {"test_metrics": {"expectancy": 0.38, "profit_factor": 1.65,
                              "trade_count": 7, "win_rate": 0.5}},
        ]

    results = mod.run_walk_forward_validation(candidates, fake_wf)
    r = results[0]
    assert r["in_sample_expectancy"] == 0.40
    assert abs(r["oos_expectancy_mean"] - 0.38) < 1e-9
    assert r["oos_window_count"] == 1
    assert r["overfit"] is False


def test_walk_forward_oos_pf_appears_in_csv(tmp_path):
    """oos_profit_factor_mean walk-forward CSV çıktısında görünmeli."""
    mod = _load_sweep_module()
    wf_rows = [
        {"sl_min_atr_multiple": 0.35, "sl_band_buffer_mult": 0.375,
         "expectancy": 1.10, "in_sample_expectancy": 1.10,
         "oos_expectancy_mean": 0.25, "oos_profit_factor_mean": 1.42,
         "oos_window_count": 3, "overfit": False},
    ]
    out = tmp_path / "wf.csv"
    mod.sweep_rows_to_csv(wf_rows, out)
    with out.open(newline="", encoding="utf-8") as fh:
        reader = list(csv.DictReader(fh))
    assert "oos_profit_factor_mean" in reader[0]
    assert reader[0]["oos_profit_factor_mean"] == "1.42"


# ============================================================
# sweep_rows_to_csv — walk-forward (farklı şema) union-of-keys (M-7)
# ============================================================


def test_sweep_rows_to_csv_walk_forward_schema_union_of_keys(tmp_path):
    """Walk-forward sonuçları sweep'ten farklı kolonlar taşır; CSV writer
    anahtar birleşimini (ilk-görülme sırası) kullanmalı — sabit _CSV_COLUMNS
    bunları düşürmez."""
    mod = _load_sweep_module()
    wf_rows = [
        {"sl_min_atr_multiple": 0.25, "sl_band_buffer_mult": 0.5,
         "expectancy": 0.40, "trade_count": 20,
         "in_sample_expectancy": 0.40, "oos_expectancy_mean": 0.325,
         "oos_window_count": 2, "overfit": False},
        {"sl_min_atr_multiple": 0.30, "sl_band_buffer_mult": 0.5,
         "expectancy": 0.33, "trade_count": 15,
         "in_sample_expectancy": 0.33, "oos_expectancy_mean": 0.10,
         "oos_window_count": 2, "overfit": True},
    ]
    out = tmp_path / "wf.csv"
    mod.sweep_rows_to_csv(wf_rows, out)
    with out.open(newline="", encoding="utf-8") as fh:
        reader = list(csv.DictReader(fh))
    assert len(reader) == 2
    # walk-forward'a özgü kolonlar CSV'de korunmalı
    assert "oos_expectancy_mean" in reader[0]
    assert "overfit" in reader[0]
    assert reader[0]["oos_expectancy_mean"] == "0.325"
    assert reader[1]["overfit"] == "True"


# ============================================================
# CLI — I-1 (all-failed exit) + I-2 (baseline footgun) code review fix
# ============================================================


def test_cli_all_combos_failed_returns_exit_3(monkeypatch, tmp_path):
    """I-1: tüm kombinasyonlar fail ederse main() exit 3 döner (broken
    harness sessizce exit 0 vermesin)."""
    mod = _load_sweep_module()

    def always_fail(params):
        raise RuntimeError("simulated total harness failure")

    # _make_real_backtest_fn'i her zaman fail eden fonksiyon döndürecek
    monkeypatch.setattr(mod, "_make_real_backtest_fn",
                        lambda **kw: always_fail)
    out = tmp_path / "results.csv"
    rc = mod.main([
        "--sl-min-atr", "0.25,0.5", "--sl-band-buffer", "0.25",
        "--out", str(out), "--workers", "1",
    ])
    assert rc == 3


def test_cli_walk_forward_without_baseline_or_05_returns_exit_2(monkeypatch, tmp_path):
    """I-2: --walk-forward + grid'de 0.5 yok + --baseline-* verilmedi →
    exit 2 (no-op ratchet'i reddet, MiniMax'a yanlış aday gitmesin)."""
    mod = _load_sweep_module()

    def fake_backtest(params):
        return {
            "trade_count": 10, "win_rate": 0.5, "expectancy": 0.3,
            "profit_factor": 1.5, "max_drawdown_pct": 0.1, "sharpe": 1.0,
        }

    monkeypatch.setattr(mod, "_make_real_backtest_fn",
                        lambda **kw: fake_backtest)
    out = tmp_path / "results.csv"
    # Grid'de 0.5 YOK, --baseline-* YOK → I-2 hata
    rc = mod.main([
        "--sl-min-atr", "0.25,0.30", "--sl-band-buffer", "0.25",
        "--out", str(out), "--walk-forward", "--workers", "1",
    ])
    assert rc == 2


def test_cli_walk_forward_with_explicit_baseline_proceeds(monkeypatch, tmp_path):
    """I-2: grid'de 0.5 olmasa da --baseline-* explicit verilirse devam eder."""
    mod = _load_sweep_module()

    def fake_backtest(params):
        return {
            "trade_count": 10, "win_rate": 0.5, "expectancy": 0.3,
            "profit_factor": 1.5, "max_drawdown_pct": 0.1, "sharpe": 1.0,
        }

    def fake_wf(**kw):
        def run_wf(params):
            return [{"test_metrics": {"expectancy": 0.28, "trade_count": 5,
                                      "win_rate": 0.5}}]
        return run_wf

    monkeypatch.setattr(mod, "_make_real_backtest_fn",
                        lambda **kw: fake_backtest)
    monkeypatch.setattr(mod, "_make_real_walk_forward_fn", fake_wf)
    out = tmp_path / "results.csv"
    rc = mod.main([
        "--sl-min-atr", "0.25,0.30", "--sl-band-buffer", "0.25",
        "--out", str(out), "--walk-forward", "--workers", "1",
        "--baseline-expectancy", "0.25",
        "--baseline-max-dd", "0.12",
        "--baseline-trade-count", "3",
    ])
    assert rc == 0


def test_cli_normal_sweep_returns_exit_0(monkeypatch, tmp_path):
    """Sağlıklı sweep (walk-forward yok) → exit 0 + CSV yazılır."""
    mod = _load_sweep_module()

    def fake_backtest(params):
        return {
            "trade_count": 8, "win_rate": 0.55, "expectancy": 0.35,
            "profit_factor": 1.8, "max_drawdown_pct": 0.09, "sharpe": 1.3,
        }

    monkeypatch.setattr(mod, "_make_real_backtest_fn",
                        lambda **kw: fake_backtest)
    out = tmp_path / "results.csv"
    rc = mod.main([
        "--sl-min-atr", "0.25,0.5", "--sl-band-buffer", "0.25",
        "--out", str(out), "--workers", "1",
    ])
    assert rc == 0
    assert out.exists()


# ============================================================
# _slice_m15 — M15 pencere dilimleme (250-bar tuzağı düzeltmesi)
# ============================================================


def _fake_m15_df():
    """100 satırlık deterministik M15 DataFrame (dilimleme testleri için)."""
    import pandas as pd

    return pd.DataFrame({"close": list(range(100))})


def test_m15_window_none_uses_full_dataset():
    """m15_window None → tüm parquet kullanılır (slice yapılmaz)."""
    mod = _load_sweep_module()
    df = _fake_m15_df()
    result = mod._slice_m15(df, m15_offset=0, m15_window=None)
    assert len(result) == len(df) == 100
    assert list(result["close"]) == list(range(100))


def test_m15_window_explicit_slices_correctly():
    """m15_window verilince offset:offset+window dilimi alınır."""
    mod = _load_sweep_module()
    df = _fake_m15_df()
    result = mod._slice_m15(df, m15_offset=10, m15_window=20)
    assert len(result) == 20
    assert list(result["close"]) == list(range(10, 30))


def test_m15_offset_zero_default():
    """offset=0 + window None → baştan tüm dataset (yeni default davranışı)."""
    mod = _load_sweep_module()
    df = _fake_m15_df()
    result = mod._slice_m15(df, m15_offset=0, m15_window=None)
    assert result.iloc[0]["close"] == 0
    assert len(result) == 100


def test_m15_window_none_with_offset_slices_tail():
    """m15_window None ama offset>0 → offset'ten sona kadar."""
    mod = _load_sweep_module()
    df = _fake_m15_df()
    result = mod._slice_m15(df, m15_offset=40, m15_window=None)
    assert len(result) == 60
    assert list(result["close"]) == list(range(40, 100))


def test_m15_window_argparse_default_is_none():
    """--m15-window flag verilmezse args.m15_window None (tuzak kapandı)."""
    mod = _load_sweep_module()
    p = mod._build_arg_parser()
    args = p.parse_args([])
    assert args.m15_window is None


# ============================================================
# run_sweep paralelleştirme (ProcessPoolExecutor) — determinizm kritik
# ============================================================


def _picklable_backtest_fn(params: dict) -> dict:
    """Modül-düzeyi (picklable) deterministik backtest mock.

    ProcessPoolExecutor worker'a pickle edilebilmesi için top-level olmalı —
    closure/lambda pickle edilemez. Metrikler params'tan deterministik türer.
    """
    smam = params["sl_min_atr_multiple"]
    sbbm = params["sl_band_buffer_mult"]
    return {
        "trade_count": int(smam * 100 + sbbm * 10),
        "win_rate": round(smam + sbbm, 6),
        "expectancy": round(smam - sbbm, 6),
        "profit_factor": round(smam * 2 + sbbm, 6),
        "max_drawdown_pct": round(sbbm / 2, 6),
        "sharpe": round(smam * sbbm, 6),
    }


def test_run_sweep_workers_1_equals_serial():
    """max_workers=1 → mevcut seri kod yolu, davranış değişmez."""
    mod = _load_sweep_module()
    grid = mod.build_param_grid([0.3, 0.5], [0.25, 0.5])
    serial = mod.run_sweep(grid, _picklable_backtest_fn)
    workers_1 = mod.run_sweep(grid, _picklable_backtest_fn, max_workers=1)
    assert workers_1 == serial


def test_run_sweep_parallel_result_equals_serial():
    """max_workers=4 sonucu == max_workers=1 sonucu — DETERMİNİZM (en kritik)."""
    mod = _load_sweep_module()
    grid = mod.build_param_grid([0.25, 0.35, 0.45, 0.5], [0.25, 0.375, 0.5])
    serial = mod.run_sweep(grid, _picklable_backtest_fn, max_workers=1)
    parallel = mod.run_sweep(grid, _picklable_backtest_fn, max_workers=4)
    assert parallel == serial


def test_run_sweep_parallel_preserves_grid_order():
    """Paralel sonuç satırları grid combo sırasını korur (tamamlanma sırası değil)."""
    mod = _load_sweep_module()
    grid = mod.build_param_grid([0.25, 0.3, 0.35, 0.4, 0.45, 0.5], [0.25, 0.5])
    parallel = mod.run_sweep(grid, _picklable_backtest_fn, max_workers=4)
    assert len(parallel) == len(grid)
    for combo, row in zip(grid, parallel):
        assert row["sl_min_atr_multiple"] == combo["sl_min_atr_multiple"]
        assert row["sl_band_buffer_mult"] == combo["sl_band_buffer_mult"]


def test_backtest_fn_picklable():
    """_make_real_backtest_fn'in döndürdüğü callable picklable olmalı.

    ProcessPoolExecutor'a geçecek — closure ise pickle patlar. Parquet yoksa
    bile callable kurulabilmeli (parquet yükleme worker'da lazy).
    """
    import pickle

    mod = _load_sweep_module()
    fn = mod._make_real_backtest_fn(
        m15_window=250, m15_offset=0, m15_lookback=140,
    )
    restored = pickle.loads(pickle.dumps(fn))
    assert callable(restored)
    assert restored.m15_offset == 0
    assert restored.m15_window == 250


# ============================================================
# Calibration drawdown_breaker bypass — 2026-05-23 root cause
# ============================================================
# P3 2026-05-22 turunda 8000-bar penceresinin ilk 63 barinda 5 ardisik
# kayipla risk_guard'in drawdown_breaker'i tetiklendi. Geri kalan 7937 bar
# (≈82 gun) olculmedi — CP3 BASARISIZ karari STRATEJI performansini degil
# breaker kilidini olcmus oldu. Kalibrasyon harness'i ham strateji
# performansini olcmek icin production breaker'i bypass etmeli.


def test_calibration_backtest_bypasses_consecutive_loss_breaker(monkeypatch):
    """_RealBacktestFn cfg.max_consecutive_losses'i ham strateji olcumu icin
    pratik olarak etkisiz hale getirmeli (production default 5 dead-lock yarattı).

    Why: P3 2026-05-22 turunda 8000-bar penceresinin ilk 63 barinda 5
    ardisik kayipla devre kesici tetiklendi; geri kalan 7937 bar
    olculmedi. Kalibrasyon raporu strateji-saf metrik vermeliydi.
    """
    mod = _load_sweep_module()

    captured: dict = {}

    def fake_harness_run(ohlcv, cfg, **kw):
        captured["max_consecutive_losses"] = cfg.max_consecutive_losses
        captured["max_drawdown_pct"] = cfg.max_drawdown_pct

        class _FakeResult:
            metrics = {
                "trade_count": 0, "win_rate": 0.0, "expectancy": 0.0,
                "profit_factor": 0.0, "max_drawdown_pct": 0.0, "sharpe": 0.0,
            }

        return _FakeResult()

    # _RealBacktestFn icindeki "from backtest.harness import run as harness_run"
    # import call time'da olur — backtest.harness.run yamasini gorur.
    import backtest.harness as harness_module
    monkeypatch.setattr(harness_module, "run", fake_harness_run)

    fn = mod._RealBacktestFn(m15_window=10, m15_offset=0, m15_lookback=5)
    # Parquet yuklemeyi atla — fake OHLCV
    fn._ohlcv = {"M15": "dummy"}

    fn({"sl_min_atr_multiple": 0.5, "sl_band_buffer_mult": 0.25})

    # Production default 5 dead-lock yaratir; kalibrasyon yuksek esik kullanmali.
    assert captured["max_consecutive_losses"] > 100, (
        f"calibration cfg.max_consecutive_losses="
        f"{captured['max_consecutive_losses']}, "
        "production default 5 dead-lock yaratiyor; > 100 olmali"
    )
    # max_drawdown_pct production 0.10 — kalibrasyonu yutar; 1.0 (%100) etkisiz.
    assert captured["max_drawdown_pct"] >= 1.0, (
        f"calibration cfg.max_drawdown_pct={captured['max_drawdown_pct']}, "
        "production default 0.10 kalibrasyonu yutuyor; >= 1.0 olmali"
    )


def test_calibration_walk_forward_bypasses_consecutive_loss_breaker(monkeypatch):
    """_make_real_walk_forward_fn de breaker'i bypass etmeli — ayni gerekce.

    Walk-forward train/test pencereleri ham strateji performansini
    karsilastirmali; breaker hem train hem test'i kilitleyebilir.
    """
    mod = _load_sweep_module()

    captured: dict = {}

    def fake_walk_forward(ohlcv, cfg, **kw):
        captured["max_consecutive_losses"] = cfg.max_consecutive_losses
        captured["max_drawdown_pct"] = cfg.max_drawdown_pct
        return []  # bos pencere listesi yeterli — gate'i tetiklemez

    import backtest.walk_forward as wf_module
    monkeypatch.setattr(wf_module, "walk_forward", fake_walk_forward)

    # Parquet'lerin var oldugu varsayilir; test sadece cfg override'i kontrol eder.
    # Eger parquet yoksa _make_real_walk_forward_fn FileNotFoundError verir.
    btc_dir = (Path(__file__).resolve().parent.parent / "data" / "btc")
    if not (btc_dir / "BTCUSDT_M15.parquet").exists():
        pytest.skip("data/btc/BTCUSDT_M15.parquet yok — examples/run_btc.py ile uret")

    run_wf = mod._make_real_walk_forward_fn(
        m15_window=10, m15_offset=0, m15_lookback=5,
        train_bars=4, test_bars=2, step_bars=2,
    )
    run_wf({"sl_min_atr_multiple": 0.5, "sl_band_buffer_mult": 0.25})

    assert captured["max_consecutive_losses"] > 100, (
        f"calibration WF cfg.max_consecutive_losses="
        f"{captured['max_consecutive_losses']}, > 100 olmali"
    )
    assert captured["max_drawdown_pct"] >= 1.0, (
        f"calibration WF cfg.max_drawdown_pct="
        f"{captured['max_drawdown_pct']}, >= 1.0 olmali"
    )
