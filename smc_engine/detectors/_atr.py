"""Paylasilan ATR (Average True Range) yardimcisi — Plan Faz 3 tasarim karari #1.

``imbalance_detector`` ve ``setup_builder`` ayni ATR mantigini kullanir (DRY).
True Range = max(high-low, |high-prev_close|, |low-prev_close|).
ATR = True Range'in basit ortalamasi (Wilder yumusatmasi degil — deterministik,
sentetik fixture'larla test edilebilir basit ortalama).

``atr_period`` config'de yok — varsayilan 14, cagiran ``getattr(config,
"atr_period", 14)`` ile opsiyonel okur.

Saf, deterministik: ayni DataFrame -> ayni sonuc.
"""

from __future__ import annotations

import pandas as pd

ATR_DEFAULT_PERIOD = 14


def true_range(ohlcv: pd.DataFrame) -> pd.Series:
    """Her bar icin True Range serisi.

    Ilk bar (onceki kapanis yok) icin TR = high - low.
    Donen seri ``ohlcv`` ile ayni index'e sahiptir.
    """
    high = ohlcv["high"]
    low = ohlcv["low"]
    prev_close = ohlcv["close"].shift(1)
    hl = high - low
    hc = (high - prev_close).abs()
    lc = (low - prev_close).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    # Ilk bar: prev_close NaN -> hc/lc NaN -> max yine hl dondurur (skipna).
    tr.iloc[0] = float(hl.iloc[0])
    return tr


def atr_series(ohlcv: pd.DataFrame, period: int = ATR_DEFAULT_PERIOD) -> pd.Series:
    """ATR serisi — True Range'in ``period``'luk kayan basit ortalamasi.

    Kayan pencere dolmadan once (``min_periods=1``) mevcut tum barlarin
    ortalamasini verir — kisa veri setlerinde de kullanilabilir.
    """
    tr = true_range(ohlcv)
    return tr.rolling(window=period, min_periods=1).mean()


def atr(ohlcv: pd.DataFrame, period: int = ATR_DEFAULT_PERIOD) -> float:
    """Son bar icin tek skaler ATR degeri.

    Veri <2 bar ise 0.0 doner (True Range tanimsiz). Aksi halde
    ``atr_series(period).iloc[-1]`` ile birebir ayni deger doner.

    Ö-8: Onceki implementasyon ilk bari (TR=hl, onceki kapanis yok) atliyordu
    ve atr_series'ten farkli sonuc veriyordu. Tek-kaynak ilkesi: skaler ATR
    artik atr_series uzerinden hesaplanir — ayni df icin tutarli sonuc
    (orchestrator, imbalance_detector, setup_builder hepsi ayni ATR'yi gorur).
    """
    n = len(ohlcv)
    if n < 2:
        return 0.0
    series = atr_series(ohlcv, period)
    if len(series) == 0:
        return 0.0
    last = series.iloc[-1]
    if last != last:  # NaN
        return 0.0
    return float(last)
