"""OHLCV resampling -- Spec R1 \u00a73 (data/resample.py).

Cogu borsa H8 mum sunmadigi icin H1 -> H8 / H4 toplama burada yapilir.
OHLC kurallari: open=ilk, close=son, high=max, low=min, volume=toplam.
Tum TF'lerin kapanis zamanlarini hizalamak icin pandas resample kullanilir.
"""

from __future__ import annotations

import pandas as pd

# Hedef TF string -> pandas resample frekansi.
_TF_TO_FREQ = {
    "M15": "15min",
    "H1": "1h",
    "H4": "4h",
    "H8": "8h",
    "D1": "1D",
    # alias / kisa formlar
    "15m": "15min",
    "1h": "1h",
    "4h": "4h",
    "8h": "8h",
    "1d": "1D",
}

_OHLC_AGG = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
}

REQUIRED_COLS = ("open", "high", "low", "close", "volume")


def resample_ohlcv(df: pd.DataFrame, target_tf: str) -> pd.DataFrame:
    """Bir OHLCV DataFrame'ini daha yuksek bir TF'ye toplar.

    df: DatetimeIndex'li, open/high/low/close/volume kolonlu DataFrame.
    target_tf: hedef zaman dilimi ("H8", "H4", "D1" ...).

    Donen DataFrame: hedef TF'de, ayni kolon semasi, eksik (tum-NaN) periyotlar
    atilmis. Open=ilk, Close=son, High=max, Low=min, Volume=toplam.
    """
    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError("resample_ohlcv: df.index pd.DatetimeIndex olmali")
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"resample_ohlcv: eksik kolon(lar): {missing}")
    if target_tf not in _TF_TO_FREQ:
        raise ValueError(f"resample_ohlcv: desteklenmeyen target_tf: {target_tf!r}")

    freq = _TF_TO_FREQ[target_tf]
    out = df.resample(freq, label="left", closed="left").agg(_OHLC_AGG)
    # Tamamen bos periyotlari at (ornegin veri bosluklari).
    out = out.dropna(how="all")
    return out[list(REQUIRED_COLS)]
