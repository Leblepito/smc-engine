"""SMC Engine uctan uca calistirma — Plan Faz 6, task 6.1.

Akis (Spec §8 + §8.1):
    veri yukle (gercek BTC parquet, yoksa CCXT'den cek)
      -> H8'i H1'den resample et
      -> ohlcv_by_tf kur (D1 + H4 + H8 + M15)
      -> harness.run() ile sinirli M15 penceresinde backtest
      -> walk_forward() ile kayan pencere dogrulama (>=3 pencere)
      -> report ile chat ozeti + walk-forward tablosu + ratchet metrik satiri

VERI KAYNAGI: GERCEK BTC/USDT OHLCV (Binance, CCXT ile cekildi).
  data/btc/BTCUSDT_{D1,H4,H1,M15}.parquet — 2024-04-01 .. 2025-05-01 (~13 ay).
  Parquet yoksa script CCXT ile yeniden ceker (ag gerekir). Sentetik veri
  KULLANILMAZ — bu calistirma gercek veri uzerindedir.

HARNESS PERFORMANS SINIRI (onemli):
  Faz 5 harness'i her M15 barinda orchestrator'i M15 dilimi uzerinde yeniden
  calistirir; structure/liquidity detektorleri dilim-uzunluguna gore maliyetli.
  Bu yuzden 37920 barlik tam M15 serisini uctan uca replay etmek pratik degil.
  Cozum (Faz 6, geriye-uyumlu): 13 aylik HTF baglami (D1/H4/H8) TAM korunur,
  ancak M15 REPLAY'i sinirli bir pencereye kesilir ve ``m15_lookback`` ile
  per-cagri maliyeti baglanir. README "Bilinen Sinirlar" bolumune bak.

Calistirma:
    cd smc-engine && python3 examples/run_btc.py
Cikti: backtest ozeti + walk-forward tablosu + RATCHET_METRIC satiri.
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd

from smc_engine.config import SMCConfig
from smc_engine.types import TimeFrame
from data.fetch import fetch_ohlcv, load_parquet, save_parquet
from data.resample import resample_ohlcv
from backtest.harness import run as run_backtest
from backtest.walk_forward import walk_forward
from backtest import report
from backtest.metrics import bootstrap_trade_sharpe_ci

# --- veri parametreleri ------------------------------------------------
_BTC_DIR = os.path.join(_ROOT, "data", "btc")
_SYMBOL = "BTC/USDT"
_SINCE = "2024-04-01"
_UNTIL = "2025-05-01"
_TF_FILES = {"D1": "1d", "H4": "4h", "H1": "1h", "M15": "15m"}

# --- backtest pencere parametreleri (harness perf siniri — yukari bak) --
# E2E backtest: M15 replay'i sinirli ama gercek-veri penceresi.
_BACKTEST_M15_OFFSET = 6000     # 13 aylik serinin "ortasindan" bir pencere
_BACKTEST_M15_WINDOW = 300      # ~3.1 gunluk M15 replay (harness perf siniri)
_BACKTEST_M15_LOOKBACK = 140    # per-cagri orchestrator M15 dilim siniri

# Walk-forward: kayan pencere (M15 bar cinsinden). 3mo/1mo (Spec §8.1) M15'te
# ~8640/2880 bara denk gelir; harness perf siniri nedeniyle test-edilebilir
# pencerelere olceklendi — orantilar (train ~2x test) korunur.
_WF_TRAIN_BARS = 120
_WF_TEST_BARS = 60
_WF_STEP_BARS = 60
_WF_M15_LOOKBACK = 100
_WF_M15_SPAN = 360              # walk-forward icin ayrilan M15 dilimi uzunlugu
_WF_M15_OFFSET = 6000


def _load_or_fetch() -> dict[str, pd.DataFrame]:
    """Gercek BTC parquet'lerini yukle; yoksa CCXT ile cek ve kaydet."""
    have_all = all(
        os.path.exists(os.path.join(_BTC_DIR, f"BTCUSDT_{tf}.parquet"))
        for tf in _TF_FILES
    )
    out: dict[str, pd.DataFrame] = {}
    if have_all:
        print(f"  veri kaynagi    : GERCEK BTC parquet ({_BTC_DIR})")
        for tf in _TF_FILES:
            out[tf] = load_parquet(os.path.join(_BTC_DIR, f"BTCUSDT_{tf}.parquet"))
    else:
        print(f"  veri kaynagi    : GERCEK BTC — CCXT ile cekiliyor ({_SINCE}..{_UNTIL})")
        os.makedirs(_BTC_DIR, exist_ok=True)
        for tf, ccxt_tf in _TF_FILES.items():
            df = fetch_ohlcv(_SYMBOL, ccxt_tf, _SINCE, _UNTIL)
            save_parquet(df, os.path.join(_BTC_DIR, f"BTCUSDT_{tf}.parquet"))
            out[tf] = df
    return out


def main() -> int:
    print("=== SMC Engine — Gercek BTC Uctan Uca Calistirma (Faz 6) ===")
    config = SMCConfig()

    # --- 1) veri yukle ---
    raw = _load_or_fetch()
    d1, h4, h1, m15 = raw["D1"], raw["H4"], raw["H1"], raw["M15"]

    # --- 2) H8'i H1'den resample et (cogu borsa H8 sunmaz) ---
    h8 = resample_ohlcv(h1, "H8")
    print(
        f"  bar sayilari    : D1={len(d1)} H4={len(h4)} "
        f"H8={len(h8)} (H1->resample) M15={len(m15)}"
    )
    print(f"  tarih araligi   : {m15.index[0]} .. {m15.index[-1]}")

    # --- 3) ohlcv_by_tf kur (hizali dict[TimeFrame, DataFrame]) ---
    # HTF (D1/H4/H8) tam; M15 sinirli replay penceresi.
    m15_bt = m15.iloc[_BACKTEST_M15_OFFSET:_BACKTEST_M15_OFFSET + _BACKTEST_M15_WINDOW]
    ohlcv_by_tf = {
        TimeFrame.D1: d1,
        TimeFrame.H4: h4,
        TimeFrame.H8: h8,
        TimeFrame.M15: m15_bt,
    }

    # --- 4) harness.run() — sinirli M15 penceresinde backtest ---
    print(
        f"  backtest        : M15 replay [{_BACKTEST_M15_OFFSET}:"
        f"{_BACKTEST_M15_OFFSET + _BACKTEST_M15_WINDOW}] "
        f"({len(m15_bt)} bar), m15_lookback={_BACKTEST_M15_LOOKBACK}"
    )
    result = run_backtest(
        ohlcv_by_tf, config,
        initial_equity=10_000.0,
        m15_lookback=_BACKTEST_M15_LOOKBACK,
    )

    # --- 5) walk-forward dogrulama (>=3 pencere) ---
    m15_wf = m15.iloc[_WF_M15_OFFSET:_WF_M15_OFFSET + _WF_M15_SPAN]
    wf_data = {
        TimeFrame.D1: d1,
        TimeFrame.H4: h4,
        TimeFrame.H8: h8,
        TimeFrame.M15: m15_wf,
    }
    windows = walk_forward(
        wf_data, config,
        train_bars=_WF_TRAIN_BARS,
        test_bars=_WF_TEST_BARS,
        step_bars=_WF_STEP_BARS,
        m15_lookback=_WF_M15_LOOKBACK,
    )
    print(f"  walk-forward    : {len(windows)} pencere uretildi")

    # --- 6) rapor: chat ozeti + walk-forward tablosu + ratchet satiri ---
    print()
    print(report.summary(result, config, windows=windows))
    print()

    # --- bootstrap Sharpe %95 CI (anti-overfit, Spec §8.1) ---
    lo, hi = bootstrap_trade_sharpe_ci(result.trades, n_samples=1000, ci=0.95)
    print(
        f"=== Bootstrap trade-bazli Sharpe %95 CI (n=1000) ===\n"
        f"  alt sinir={lo:.4f}  ust sinir={hi:.4f}  "
        f"(trade sayisi={len(result.trades)})"
    )
    if len(result.trades) < 30:
        print("  [UYARI] <30 trade — CI ve Sharpe yaniltici; ratchet min-trade gate gerekir.")
    if lo <= 0.0:
        # NOTE (KR-3): bu CI **trade-bazli** Sharpe'in dagiliminin alt
        # sinirini gosterir; metrics.compute()'in raporladigi equity-curve
        # yillik Sharpe ile birebir ayni metrik DEGIL. Ratchet gate hangi
        # Sharpe'i kullanacak — TBD (Spec §12 gate karari, kullanici onayli).
        print("  [GATE] Trade-bazli Sharpe CI alt siniri <= 0 — sifirdan ayirt edilemez (ratchet reddetmeli).")

    # --- trades.csv yaz ---
    csv_path = os.path.join(_ROOT, "examples", "btc_trades.csv")
    report.write_trades_csv(result, csv_path)
    print(f"\n  trades.csv      : {csv_path}")

    print("\nRUN_BTC OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
