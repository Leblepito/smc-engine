"""Backtest paylasimli yardimcilari (U-3).

Daha once `harness.py` ve `walk_forward.py` icinde birebir tekrar eden
`_normalize` fonksiyonu burada konsolide edildi.
"""

from __future__ import annotations

import pandas as pd

from smc_engine.types import TimeFrame


def normalize_ohlcv_by_tf(ohlcv_by_tf: dict) -> dict[TimeFrame, pd.DataFrame]:
    """``{TimeFrame|str: DataFrame}`` -> ``{TimeFrame: DataFrame}``.

    String anahtarlari (orn. ``"M15"``) ``TimeFrame`` enum'una cevirir;
    enum anahtarlari oldugu gibi gecer. Bilinmeyen string anahtar
    ``KeyError`` firlatir (TimeFrame[s] semantigi).
    """
    out: dict[TimeFrame, pd.DataFrame] = {}
    for k, df in ohlcv_by_tf.items():
        tf = k if isinstance(k, TimeFrame) else TimeFrame[str(k)]
        out[tf] = df
    return out
