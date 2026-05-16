"""Faz 6.3 — Walk-forward dogrulama (anti-overfit, Spec §8.1).

Kayan pencere: train dilimi -> test dilimi -> kaydir. Her pencerede ``harness.run``
hem train hem test M15 dilimi uzerinde calistirilir, ``metrics`` hesaplanir.
Min 3 pencere uretilemiyorsa ``ValueError``.

Look-ahead yok: her pencerede test dilimi train diliminden SONRA gelir ve
ortusmez (``train_end <= test_start``). Pencereler ``step_bars`` kadar kayar.
HTF (D1/H4/H8) baglami her pencerede tam verilir; yalnizca M15 replay dilimi
kayar — bu, gercek-veri HTF baglamini korurken M15 replay maliyetini sinirlar.

Determinizm: ayni ``ohlcv_by_tf`` + ayni pencere parametreleri -> ozdes sonuc
(harness deterministik, pencereleme saf dilimleme).

İmza:
    walk_forward(ohlcv_by_tf, config, *, train_bars, test_bars, step_bars,
                 initial_equity=10_000.0, m15_lookback=None) -> list[dict]
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from smc_engine.types import TimeFrame
from backtest.harness import run as _run

_MIN_WINDOWS = 3


# U-3: _normalize ortak yardimciya tasindi (backtest/_utils.py).
from backtest._utils import normalize_ohlcv_by_tf as _normalize  # noqa: E402


def walk_forward(
    ohlcv_by_tf: dict,
    config,
    *,
    train_bars: int,
    test_bars: int,
    step_bars: int,
    initial_equity: float = 10_000.0,
    m15_lookback: Optional[int] = None,
) -> list[dict]:
    """Kayan pencere walk-forward backtest.

    Pencere ``w`` icin (M15 bar indeksleri):
      train = m15[start : start + train_bars]
      test  = m15[start + train_bars : start + train_bars + test_bars]
      bir sonraki pencere: start += step_bars

    Args:
        ohlcv_by_tf: ``{TimeFrame|str: DataFrame}`` — M15 zorunlu; D1/H4/H8
            HTF baglami her pencereye tam verilir.
        config: ``SMCConfig``.
        train_bars: train diliminin M15 bar sayisi.
        test_bars: test diliminin M15 bar sayisi.
        step_bars: pencereler arasi kayma (M15 bar).
        initial_equity: her pencere icin baslangic equity.
        m15_lookback: ``harness.run``'a gecirilir (per-cagri M15 dilim siniri).

    Returns:
        Pencere sozlukleri listesi. Her sozluk:
          ``train_start``, ``train_end``, ``test_start``, ``test_end`` (Timestamp)
          ``train_metrics``, ``test_metrics`` (``metrics.compute`` ciktisi dict)
          ``train_trades``, ``test_trades`` (int — kolay erisim)

    Raises:
        ValueError: parametreler hatali ya da veri 3 pencereye yetmiyorsa.
    """
    if train_bars < 1 or test_bars < 1 or step_bars < 1:
        raise ValueError("walk_forward: train/test/step_bars >= 1 olmali")

    data = _normalize(ohlcv_by_tf)
    if TimeFrame.M15 not in data:
        raise ValueError("walk_forward: M15 OHLCV zorunlu")
    m15 = data[TimeFrame.M15]
    n = len(m15)

    window_span = train_bars + test_bars
    # Kac pencere sigar?
    if n < window_span:
        raise ValueError(
            f"walk_forward: M15 uzunlugu ({n}) bir pencereye "
            f"({window_span}) yetmiyor"
        )
    n_windows = (n - window_span) // step_bars + 1
    if n_windows < _MIN_WINDOWS:
        raise ValueError(
            f"walk_forward: yalnizca {n_windows} pencere uretilebiliyor; "
            f"min {_MIN_WINDOWS} gerekli. Veri uzat veya pencere/step kucult."
        )

    windows: list[dict] = []
    for w in range(n_windows):
        start = w * step_bars
        train_end_idx = start + train_bars
        test_end_idx = train_end_idx + test_bars

        train_m15 = m15.iloc[start:train_end_idx]
        test_m15 = m15.iloc[train_end_idx:test_end_idx]

        train_ds = dict(data)
        train_ds[TimeFrame.M15] = train_m15
        test_ds = dict(data)
        test_ds[TimeFrame.M15] = test_m15

        train_res = _run(
            train_ds, config,
            initial_equity=initial_equity,
            m15_lookback=m15_lookback,
        )
        test_res = _run(
            test_ds, config,
            initial_equity=initial_equity,
            m15_lookback=m15_lookback,
        )

        windows.append({
            "index": w,
            "train_start": train_m15.index[0],
            "train_end": train_m15.index[-1],
            "test_start": test_m15.index[0],
            "test_end": test_m15.index[-1],
            "train_metrics": train_res.metrics,
            "test_metrics": test_res.metrics,
            "train_trades": train_res.metrics.get("trade_count", 0),
            "test_trades": test_res.metrics.get("trade_count", 0),
        })

    return windows
