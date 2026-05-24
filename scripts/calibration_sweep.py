"""``calibration_sweep.py`` — SL geometry parametre sweep harness.

SL Geometry Kalibrasyon Handoff (2026-05-20): testnet smoke setup
üretemiyor — `reason=sl_geometry_too_tight` (sl_atr_ratio≈0.27 < eşik 0.5).
Bu harness `sl_min_atr_multiple` × `sl_band_buffer_mult` parametre grid'ini
backtest üzerinde tarar, her kombinasyon için metrik toplar, ratchet
kuralıyla aday kombinasyonları seçer.

Bu harness HİÇBİR parametre değiştirmez / deploy etmez — sadece sweep
verisi üretir. Kalibrasyon kararı (autoresearch / MiniMax) ayrı.

Saf çekirdek (test edilebilir, run_backtest_fn injection):
    build_param_grid(sl_min_atr_multiple, sl_band_buffer_mult) -> grid
    run_sweep(grid, run_backtest_fn) -> rows
    sweep_rows_to_csv(rows, path)
    select_top_candidates(rows, n, baseline_*) -> ratchet-geçen adaylar

CLI (gerçek backtest):
    python scripts/calibration_sweep.py \
        --sl-min-atr 0.25,0.30,0.35,0.40,0.45,0.50 \
        --sl-band-buffer 0.25,0.375,0.50 \
        --out logs/calibration/calibration-results-YYYYMMDD.csv
"""

from __future__ import annotations

import argparse
import csv
import itertools
import os
import sys
import traceback
from datetime import date
from pathlib import Path
from typing import Callable, Optional


# ============================================================
# Saf çekirdek — test edilebilir, backtest'ten bağımsız
# ============================================================

# CSV kolonları — params + metrics + error. Sabit sıra (deterministik output).
_PARAM_KEYS = ("sl_min_atr_multiple", "sl_band_buffer_mult")
_METRIC_KEYS = (
    "trade_count", "win_rate", "expectancy", "profit_factor",
    "max_drawdown_pct", "sharpe",
)
_CSV_COLUMNS = (*_PARAM_KEYS, *_METRIC_KEYS, "error")


def build_param_grid(
    sl_min_atr_multiple: list[float],
    sl_band_buffer_mult: list[float],
) -> list[dict]:
    """İki parametre listesinin kartezyen çarpımı → kombinasyon dict listesi."""
    grid = []
    for smam, sbbm in itertools.product(sl_min_atr_multiple, sl_band_buffer_mult):
        grid.append({
            "sl_min_atr_multiple": smam,
            "sl_band_buffer_mult": sbbm,
        })
    return grid


def _run_one_combo(
    params: dict, run_backtest_fn: Callable[[dict], dict],
) -> dict:
    """Tek bir kombinasyonu çalıştır → satır (params ∪ metrics | params ∪ error).

    Exception sweep'i çökertmez — ``error`` alanına yazılır. Hem seri hem
    paralel kod yolu bu fonksiyonu kullanır (tutarlı satır şeması).
    """
    row = dict(params)
    try:
        metrics = run_backtest_fn(params)
        for k in _METRIC_KEYS:
            row[k] = metrics.get(k)
    except Exception as exc:  # backtest tek kombinasyonda patladı
        row["error"] = f"{type(exc).__name__}: {exc}"
    return row


def run_sweep(
    grid: list[dict],
    run_backtest_fn: Callable[[dict], dict],
    max_workers: int = 1,
) -> list[dict]:
    """Grid'in her kombinasyonu için ``run_backtest_fn(params)`` çağır.

    ``run_backtest_fn`` bir metrics dict döndürmeli (trade_count, win_rate,
    expectancy, ...). Bir kombinasyon exception fırlatırsa sweep ÇÖKMEZ —
    o satır ``error`` alanı ile kaydedilir, sweep devam eder.

    ``max_workers``:
      - ``1`` (varsayılan) → seri kod yolu, geriye tam uyumlu.
      - ``>1`` → ``ProcessPoolExecutor`` ile combo'lar paralel. Combo'lar
        bağımsız (her biri taze ``SMCConfig``). DETERMİNİZM: sonuçlar
        tamamlanma sırasında değil, **grid combo sırasında** döndürülür —
        çıktı seri çalıştırmayla byte-identical. ``run_backtest_fn``
        picklable olmalı (paralel modda); ``_RealBacktestFn`` öyledir.

    Returns: her satır = params ∪ metrics (veya params ∪ {error}),
    grid sırasında.
    """
    if max_workers <= 1:
        return [_run_one_combo(params, run_backtest_fn) for params in grid]

    from concurrent.futures import ProcessPoolExecutor

    results: list[Optional[dict]] = [None] * len(grid)
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # Future -> grid index eşlemesi; sonuçları index'e göre yerleştir
        # (as_completed tamamlanma sırası verir → determinizm için index şart).
        future_to_idx = {
            executor.submit(_run_one_combo, params, run_backtest_fn): idx
            for idx, params in enumerate(grid)
        }
        for future in future_to_idx:
            idx = future_to_idx[future]
            results[idx] = future.result()
    return [r for r in results if r is not None]


def sweep_rows_to_csv(rows: list[dict], path, columns: Optional[tuple] = None) -> None:
    """Satırları CSV'ye yaz. Sabit kolon sırası — deterministik.

    ``columns`` verilmezse: sweep satırları için ``_CSV_COLUMNS``. Walk-forward
    sonuçları gibi farklı şema için, rows'taki TÜM anahtarların birleşimi
    (deterministik sıra: ilk görülme sırası) kullanılır.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if columns is None:
        if rows and any(k not in _CSV_COLUMNS for r in rows for k in r):
            # Sweep dışı şema (walk-forward) — anahtar birleşimi, ilk-görülme sırası
            seen: list[str] = []
            for r in rows:
                for k in r:
                    if k not in seen:
                        seen.append(k)
            columns = tuple(seen)
        else:
            columns = _CSV_COLUMNS
    with p.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns))
        writer.writeheader()
        for row in rows:
            # Eksik kolonları boş bırak (errored satırda metrikler yok)
            writer.writerow({col: row.get(col, "") for col in columns})


def run_walk_forward_validation(
    candidates: list[dict],
    walk_forward_fn: Callable[[dict], list[dict]],
    *,
    overfit_ratio: float = 0.60,
) -> list[dict]:
    """En iyi adayları out-of-sample walk-forward ile doğrula — overfit ele.

    ``walk_forward_fn(params)`` pencere dict listesi döndürmeli (her dict'te
    ``test_metrics`` — backtest.walk_forward çıktısı). Her aday için OOS
    (test) metriklerinin ortalaması alınır.

    Overfit kuralı: OOS expectancy ortalaması in-sample expectancy'nin
    ``overfit_ratio`` katından düşükse → ``overfit=True``. (In-sample'da
    parlak görünüp test'te çöken kombinasyonu işaretler.)

    OOS profit_factor (``oos_profit_factor_mean``): expectancy birkaç
    dar-SL outlier'ına esir olabilir (P2 teşhisi 2026-05-20) — OOS
    profit_factor bağımsız teyit metriğidir; overfit bayrağının
    expectancy-tabanlı zayıflığını telafi eder. Bayrak hesabına GİRMEZ,
    yalnızca raporlanır (aday seçim kararı OOS pf'i ayrıca değerlendirir).

    Returns: her aday için in_sample_expectancy + oos_expectancy_mean +
    oos_profit_factor_mean + oos_window_count + overfit bayrağı.
    """
    results: list[dict] = []
    for cand in candidates:
        in_sample_exp = cand.get("expectancy", 0.0)
        try:
            windows = walk_forward_fn(cand)
        except Exception as exc:
            results.append({
                **cand,
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        test_metrics = [w["test_metrics"] for w in windows if "test_metrics" in w]
        oos_exps = [tm.get("expectancy", 0.0) for tm in test_metrics]
        oos_pfs = [tm.get("profit_factor", 0.0) for tm in test_metrics]
        oos_mean = sum(oos_exps) / len(oos_exps) if oos_exps else 0.0
        oos_pf_mean = sum(oos_pfs) / len(oos_pfs) if oos_pfs else 0.0
        overfit = oos_mean < in_sample_exp * overfit_ratio
        results.append({
            **cand,
            "in_sample_expectancy": in_sample_exp,
            "oos_expectancy_mean": oos_mean,
            "oos_profit_factor_mean": oos_pf_mean,
            "oos_window_count": len(oos_exps),
            "overfit": overfit,
        })
    return results


def select_top_candidates(
    rows: list[dict],
    n: int,
    *,
    baseline_expectancy: float,
    baseline_max_dd: float,
    baseline_trade_count: int,
    max_dd_tolerance: float = 1.20,
) -> list[dict]:
    """Ratchet kuralı — bir kombinasyon ANCAK üçü birden sağlanırsa kabul:

      1. expectancy >= baseline_expectancy (kârlılık düşmedi)
      2. trade_count > baseline_trade_count (setup akışı açıldı — hedef)
      3. max_drawdown_pct <= baseline_max_dd * max_dd_tolerance (risk patlamadı)

    error işaretli satırlar ratchet'e girmeden elenir. Kabul edilenler
    expectancy'ye göre (yüksek önce) sıralanır, ilk ``n`` döndürülür.
    """
    accepted = []
    for row in rows:
        if row.get("error"):
            continue
        expectancy = row.get("expectancy")
        trade_count = row.get("trade_count")
        max_dd = row.get("max_drawdown_pct")
        if expectancy is None or trade_count is None or max_dd is None:
            continue
        if expectancy < baseline_expectancy:
            continue
        if trade_count <= baseline_trade_count:
            continue
        if max_dd > baseline_max_dd * max_dd_tolerance:
            continue
        accepted.append(row)
    # expectancy yüksek önce; eşitlikte trade_count yüksek önce
    accepted.sort(key=lambda r: (-r["expectancy"], -r["trade_count"]))
    return accepted[:n]


# ============================================================
# CLI — gerçek backtest (data/btc parquet)
# ============================================================


def _parse_float_list(s: str) -> list[float]:
    """'0.25,0.30,0.50' → [0.25, 0.30, 0.50]."""
    return [float(x.strip()) for x in s.split(",") if x.strip()]


def _slice_m15(m15_full, m15_offset: int, m15_window: Optional[int]):
    """M15 DataFrame'i offset/window'a gore dilimle.

    m15_window None ise offset'ten sona kadar tum veri kullanilir (varsayilan).
    Aksi halde [offset:offset+window] dilimi alinir.
    """
    if m15_window is None:
        return m15_full.iloc[m15_offset:]
    return m15_full.iloc[m15_offset:m15_offset + m15_window]


def _btc_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "btc"


def _assert_btc_parquet_exists() -> None:
    """D1/H4/H1/M15 parquet'lerinin varlığını doğrula; eksikse FileNotFoundError."""
    btc_dir = _btc_dir()
    for tf in ("D1", "H4", "H1", "M15"):
        if not (btc_dir / f"BTCUSDT_{tf}.parquet").exists():
            raise FileNotFoundError(
                f"data/btc/BTCUSDT_{tf}.parquet yok — examples/run_btc.py ile üret"
            )


def _load_btc_ohlcv(m15_window: Optional[int], m15_offset: int) -> dict:
    """data/btc parquet'ten OHLCV sozlugu yukle (D1/H4/H8/M15).

    M15 _slice_m15 ile dilimlenir. Parquet eksikse FileNotFoundError.
    """
    from smc_engine.types import TimeFrame
    from data.fetch import load_parquet
    from data.resample import resample_ohlcv

    _assert_btc_parquet_exists()
    btc_dir = _btc_dir()
    d1 = load_parquet(str(btc_dir / "BTCUSDT_D1.parquet"))
    h4 = load_parquet(str(btc_dir / "BTCUSDT_H4.parquet"))
    h1 = load_parquet(str(btc_dir / "BTCUSDT_H1.parquet"))
    h8 = resample_ohlcv(h1, "H8")
    m15_full = load_parquet(str(btc_dir / "BTCUSDT_M15.parquet"))
    m15_slice = _slice_m15(m15_full, m15_offset, m15_window)
    return {
        TimeFrame.D1: d1, TimeFrame.H4: h4,
        TimeFrame.H8: h8, TimeFrame.M15: m15_slice,
    }


class _RealBacktestFn:
    """Picklable backtest callable — closure yerine sınıf (ProcessPoolExecutor).

    Yalnızca skaler pencere parametrelerini taşır; OHLCV parquet ilk çağrıda
    LAZY yüklenir (her worker süreci kendi verisini yükler — shared memory
    gereksiz). __getstate__ yüklü DataFrame'leri pickle dışında tutar.
    """

    def __init__(self, m15_window: Optional[int], m15_offset: int,
                 m15_lookback: int):
        self.m15_window = m15_window
        self.m15_offset = m15_offset
        self.m15_lookback = m15_lookback
        self._ohlcv = None

    def __getstate__(self) -> dict:
        # Yüklü OHLCV'yi pickle ETME — worker kendi yükler (lazy).
        return {
            "m15_window": self.m15_window,
            "m15_offset": self.m15_offset,
            "m15_lookback": self.m15_lookback,
            "_ohlcv": None,
        }

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)

    def __call__(self, params: dict) -> dict:
        from smc_engine.config import SMCConfig
        from backtest.harness import run as harness_run

        if self._ohlcv is None:
            self._ohlcv = _load_btc_ohlcv(self.m15_window, self.m15_offset)
        cfg = SMCConfig()
        cfg.sl_min_atr_multiple = params["sl_min_atr_multiple"]
        cfg.sl_band_buffer_mult = params["sl_band_buffer_mult"]
        # ATR regime filter overrides (Spec §13.2, 2026-05-23) — opsiyonel
        if "atr_percentile_threshold" in params:
            cfg.atr_percentile_threshold = params["atr_percentile_threshold"]
        if "atr_regime_filter_enabled" in params:
            cfg.atr_regime_filter_enabled = params["atr_regime_filter_enabled"]
        # D1 EMA bias overrides (Spec §13.x, 2026-05-24) — opsiyonel
        if "bias_use_d1_ema_trend" in params:
            cfg.bias_use_d1_ema_trend = params["bias_use_d1_ema_trend"]
        if "bias_d1_ema_period" in params:
            cfg.bias_d1_ema_period = params["bias_d1_ema_period"]
        # Kalibrasyon ham strateji performansini olcer — production'in
        # drawdown_breaker'i (max_consecutive_losses=5 + max_drawdown_pct=0.10)
        # olcumu yutmamali. 2026-05-22 P3 turunda ilk 63 barda 5 ardisik
        # kayipla devre kesici kilitlendi, geri kalan 7937 bar olculmedi.
        # Production gate'leri burada acikca devre disi (kalibrasyon raporu
        # = strateji matematigi, risk_guard kisitlari altinda degil).
        cfg.max_consecutive_losses = 10**9
        cfg.max_drawdown_pct = 1.0
        result = harness_run(
            self._ohlcv, cfg, initial_equity=10_000.0,
            m15_lookback=self.m15_lookback,
        )
        return result.metrics


def _make_real_backtest_fn(
    m15_window: Optional[int], m15_offset: int, m15_lookback: int,
):
    """Picklable backtest callable üret (``_RealBacktestFn``).

    Parquet ilk çağrıda lazy yüklenir; bu nedenle callable, parquet yokken
    de kurulabilir (ProcessPoolExecutor'a güvenle pickle edilir). Eksik
    parquet ilk ``__call__``'da ``FileNotFoundError`` fırlatır.
    """
    return _RealBacktestFn(m15_window, m15_offset, m15_lookback)


def _make_real_walk_forward_fn(
    m15_window: Optional[int], m15_offset: int, m15_lookback: int,
    train_bars: int, test_bars: int, step_bars: int,
):
    """data/btc parquet'ten OHLCV yükleyip walk_forward çağıran fonksiyon üret.

    Aday parametreleri SMCConfig'e set eder; out-of-sample doğrulama için
    backtest.walk_forward kullanır.
    """
    from smc_engine.config import SMCConfig
    from smc_engine.types import TimeFrame
    from backtest.walk_forward import walk_forward
    from data.fetch import load_parquet
    from data.resample import resample_ohlcv

    btc_dir = Path(__file__).resolve().parent.parent / "data" / "btc"
    d1 = load_parquet(str(btc_dir / "BTCUSDT_D1.parquet"))
    h4 = load_parquet(str(btc_dir / "BTCUSDT_H4.parquet"))
    h1 = load_parquet(str(btc_dir / "BTCUSDT_H1.parquet"))
    h8 = resample_ohlcv(h1, "H8")
    m15_full = load_parquet(str(btc_dir / "BTCUSDT_M15.parquet"))
    m15_slice = _slice_m15(m15_full, m15_offset, m15_window)

    def run_wf(params: dict) -> list[dict]:
        cfg = SMCConfig()
        cfg.sl_min_atr_multiple = params["sl_min_atr_multiple"]
        cfg.sl_band_buffer_mult = params["sl_band_buffer_mult"]
        # ATR regime filter overrides (Spec §13.2, 2026-05-23) — opsiyonel
        if "atr_percentile_threshold" in params:
            cfg.atr_percentile_threshold = params["atr_percentile_threshold"]
        if "atr_regime_filter_enabled" in params:
            cfg.atr_regime_filter_enabled = params["atr_regime_filter_enabled"]
        # D1 EMA bias overrides (Spec §13.x, 2026-05-24) — opsiyonel
        if "bias_use_d1_ema_trend" in params:
            cfg.bias_use_d1_ema_trend = params["bias_use_d1_ema_trend"]
        if "bias_d1_ema_period" in params:
            cfg.bias_d1_ema_period = params["bias_d1_ema_period"]
        # Sweep'le ayni gerekce: drawdown_breaker WF olcumunu de yutar
        # (her train/test fold'unda 5 ardisik kayipla kilit). Strateji-saf
        # WF metrigi icin production gate'leri devre disi.
        cfg.max_consecutive_losses = 10**9
        cfg.max_drawdown_pct = 1.0
        ohlcv = {
            TimeFrame.D1: d1, TimeFrame.H4: h4,
            TimeFrame.H8: h8, TimeFrame.M15: m15_slice,
        }
        return walk_forward(
            ohlcv, cfg,
            train_bars=train_bars, test_bars=test_bars, step_bars=step_bars,
            m15_lookback=m15_lookback,
        )

    return run_wf


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="SL geometry parametre sweep - backtest grid."
    )
    p.add_argument("--sl-min-atr", default="0.25,0.30,0.35,0.40,0.45,0.50",
                   help="sl_min_atr_multiple values (comma-separated).")
    p.add_argument("--sl-band-buffer", default="0.25,0.375,0.50",
                   help="sl_band_buffer_mult values (comma-separated).")
    p.add_argument("--m15-window", type=int, default=None,
                   help="M15 replay window size. Omitted (default) = full "
                        "parquet from offset to end. Set only to cap runtime.")
    p.add_argument("--m15-offset", type=int, default=0,
                   help="M15 window start offset (default 0 = dataset start).")
    p.add_argument("--m15-lookback", type=int, default=140,
                   help="harness.run per-call M15 slice limit.")
    p.add_argument("--workers", type=int, default=os.cpu_count() or 1,
                   help="Parallel combo workers (ProcessPoolExecutor). "
                        "1 = serial. Default = CPU count.")
    p.add_argument("--out", default=None,
                   help="CSV output path (default: logs/calibration/"
                        "calibration-results-YYYYMMDD.csv).")
    # Walk-forward (handoff step 5.3) — post-sweep OOS validation of top candidates.
    p.add_argument("--walk-forward", action="store_true",
                   help="After sweep, validate ratchet-passing candidates "
                        "via walk_forward (out-of-sample, overfit elimination).")
    p.add_argument("--wf-train-bars", type=int, default=120,
                   help="walk_forward train window M15 bar count.")
    p.add_argument("--wf-test-bars", type=int, default=40,
                   help="walk_forward test window M15 bar count.")
    p.add_argument("--wf-step-bars", type=int, default=40,
                   help="walk_forward step between windows.")
    p.add_argument("--wf-overfit-ratio", type=float, default=0.60,
                   help="Overfit flag threshold: OOS expectancy below "
                        "in_sample * ratio is flagged overfit (default 0.60).")
    p.add_argument("--baseline-expectancy", type=float, default=None,
                   help="Ratchet baseline expectancy (if omitted, taken from "
                        "the sl_min_atr=0.5 sweep row).")
    p.add_argument("--baseline-max-dd", type=float, default=None,
                   help="Ratchet baseline max_drawdown_pct.")
    # Volatility regime filter (Spec §13.2, 2026-05-23)
    p.add_argument("--atr-percentile-threshold", default=None,
                   help="ATR percentile veto threshold (sweep grid, "
                        "comma-separated). Verilmezse SMCConfig default "
                        "(0.80) tek kombo kullanilir.")
    p.add_argument("--atr-regime-disabled", action="store_true",
                   help="Volatility regime gate'i KAPALI olarak calistir "
                        "(baseline karsilastirmasi icin).")
    p.add_argument("--bias-d1-ema-disabled", action="store_true",
                   help="bias_use_d1_ema_trend=False override "
                        "(regresyon hata ayiklama icin).")
    p.add_argument("--bias-d1-ema-period", type=int, default=None,
                   help="bias_d1_ema_period override.")
    p.add_argument("--baseline-trade-count", type=int, default=None,
                   help="Ratchet baseline trade_count.")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    p = _build_arg_parser()
    args = p.parse_args(argv)

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
            if args.bias_d1_ema_disabled:
                cell["bias_use_d1_ema_trend"] = False
            if args.bias_d1_ema_period is not None:
                cell["bias_d1_ema_period"] = args.bias_d1_ema_period
            new_grid.append(cell)
    grid = new_grid
    print(f"[calibration_sweep] grid: {len(grid)} combinations")

    # Parquet varlığını sweep ÖNCESİ doğrula — eksikse exit 2 (data hatası,
    # kalibrasyon sonucu değil). Backtest fn lazy yüklediği için bu kontrol
    # ayrı; aksi halde tüm combo'lar FileNotFoundError ile patlayıp I-1
    # exit 3'e düşerdi (yanlış teşhis: harness bug'ı sanılır).
    try:
        _assert_btc_parquet_exists()
    except FileNotFoundError as exc:
        print(f"[calibration_sweep] ERROR: {exc}", file=sys.stderr)
        return 2

    backtest_fn = _make_real_backtest_fn(
        m15_window=args.m15_window,
        m15_offset=args.m15_offset,
        m15_lookback=args.m15_lookback,
    )

    rows = run_sweep(grid, backtest_fn, max_workers=args.workers)

    out_path = args.out or (
        f"logs/calibration/calibration-results-{date.today().isoformat()}.csv"
    )
    sweep_rows_to_csv(rows, out_path)
    print(f"[calibration_sweep] {len(rows)} rows -> {out_path}")

    errored = [r for r in rows if r.get("error")]
    if errored:
        print(f"[calibration_sweep] WARNING: {len(errored)} combinations errored")
    # I-1 code review: TÜM kombinasyonlar fail ettiyse bu bir kalibrasyon
    # sonucu değil — harness/data bug'ı. Soft warning + exit 0 yerine fail-loud.
    if errored and len(errored) == len(rows):
        print(
            f"[calibration_sweep] FATAL: all {len(rows)} combinations failed "
            f"(harness/data bug, NOT a calibration result):",
            file=sys.stderr,
        )
        print(f"  {errored[0]['error']}", file=sys.stderr)
        return 3

    if args.walk_forward:
        # I-2 code review: --walk-forward gerçek bir baseline ister. Grid'de
        # 0.5 (production değeri) yoksa VE --baseline-* verilmediyse, ratchet
        # trivial no-op'a düşer (exp>=0 & dd<=1.2 her zaman doğru) → MiniMax'a
        # yanlış-doğrulanmış aday listesi gider. Bunu engelle: error.
        explicit_baseline = (
            args.baseline_expectancy is not None
            and args.baseline_max_dd is not None
            and args.baseline_trade_count is not None
        )
        baseline_row = next(
            (r for r in rows
             if r.get("sl_min_atr_multiple") == 0.5 and not r.get("error")),
            None,
        )
        if not explicit_baseline and baseline_row is None:
            print(
                "[calibration_sweep] ERROR: --walk-forward needs a real "
                "baseline. Either include 0.5 in --sl-min-atr (production "
                "value) OR pass all three --baseline-expectancy / "
                "--baseline-max-dd / --baseline-trade-count. Refusing to run "
                "a no-op ratchet that would falsely validate every combo.",
                file=sys.stderr,
            )
            return 2
        base_exp = args.baseline_expectancy if args.baseline_expectancy is not None else (
            baseline_row.get("expectancy", 0.0) if baseline_row else 0.0
        )
        base_dd = args.baseline_max_dd if args.baseline_max_dd is not None else (
            baseline_row.get("max_drawdown_pct", 1.0) if baseline_row else 1.0
        )
        base_tc = args.baseline_trade_count if args.baseline_trade_count is not None else (
            baseline_row.get("trade_count", 0) if baseline_row else 0
        )
        candidates = select_top_candidates(
            rows, n=3, baseline_expectancy=base_exp,
            baseline_max_dd=base_dd, baseline_trade_count=base_tc,
        )
        print(f"[calibration_sweep] ratchet-passing candidates: {len(candidates)} "
              f"(baseline: exp={base_exp:.4f} dd={base_dd:.4f} tc={base_tc})")
        if candidates:
            wf_fn = _make_real_walk_forward_fn(
                m15_window=args.m15_window, m15_offset=args.m15_offset,
                m15_lookback=args.m15_lookback,
                train_bars=args.wf_train_bars, test_bars=args.wf_test_bars,
                step_bars=args.wf_step_bars,
            )
            wf_results = run_walk_forward_validation(
                candidates, wf_fn, overfit_ratio=args.wf_overfit_ratio,
            )
            wf_out = out_path.replace(".csv", "-walkforward.csv")
            sweep_rows_to_csv(wf_results, wf_out)
            print(f"[calibration_sweep] walk-forward -> {wf_out}")
            for r in wf_results:
                tag = "OVERFIT" if r.get("overfit") else "ok"
                print(f"  smam={r.get('sl_min_atr_multiple')} "
                      f"sbbm={r.get('sl_band_buffer_mult')} "
                      f"in_sample={r.get('in_sample_expectancy')} "
                      f"oos_mean={r.get('oos_expectancy_mean')} [{tag}]")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
