"""Paylaşılan swing tespiti — Spec §5 "4-mum swing kuralı".

``range_detector`` ve ``structure_detector`` aynı swing mantığını kullanır (DRY).
Saf fonksiyon: OHLCV DataFrame girer, ``SwingPoint`` listesi çıkar.
Tüm zaman referansları DataFrame'in ``DatetimeIndex``'inden gelen ``datetime``.
"""

from __future__ import annotations

import pandas as pd

from smc_engine.types import SwingKind, SwingPoint


def find_swings(ohlcv: pd.DataFrame, lookback: int = 4) -> list[SwingPoint]:
    """4-mum (``lookback``) swing kuralıyla swing high/low'ları bul.

    Bir mum **swing high**'tır: high'ı, ``lookback`` mum öncesindeki ve
    ``lookback`` mum sonrasındaki tüm mumların high'larından **kesin büyük** se.
    **Swing low** simetrik (low'lar üzerinden, kesin küçük).

    İki yanında ``lookback`` kadar mum bulunmayan kenar mumlar swing olamaz
    (geleceğe bakmamak için sağ taraf da dolu olmalı — çağıran kapanmış
    pencere verir).

    Args:
        ohlcv: ``open/high/low/close/volume`` kolonlu, ``DatetimeIndex``'li df.
        lookback: her yanda kontrol edilecek mum sayısı (varsayılan 4).

    Returns:
        Timestamp'e göre artan sıralı ``SwingPoint`` listesi. Bir mum hem
        swing high hem swing low olabilir (ikisi de eklenir).
    """
    n = len(ohlcv)
    if n < 2 * lookback + 1:
        return []

    highs = ohlcv["high"].to_numpy()
    lows = ohlcv["low"].to_numpy()
    index = ohlcv.index

    swings: list[SwingPoint] = []

    for i in range(lookback, n - lookback):
        # KR-2: swing'in TEYIT BARI -- sag-lookback dolduktan sonraki bar.
        # Real-time bir replay'de swing ancak burada bilinebilir. Detektorler
        # ``confirm_timestamp <= ts`` filtresi ile look-ahead'i önler.
        confirm_ts = index[i + lookback].to_pydatetime()

        left_h = highs[i - lookback:i]
        right_h = highs[i + 1:i + 1 + lookback]
        if (highs[i] > left_h).all() and (highs[i] > right_h).all():
            swings.append(
                SwingPoint(
                    timestamp=index[i].to_pydatetime(),
                    price=float(highs[i]),
                    kind=SwingKind.HIGH,
                    confirm_timestamp=confirm_ts,
                )
            )

        left_l = lows[i - lookback:i]
        right_l = lows[i + 1:i + 1 + lookback]
        if (lows[i] < left_l).all() and (lows[i] < right_l).all():
            swings.append(
                SwingPoint(
                    timestamp=index[i].to_pydatetime(),
                    price=float(lows[i]),
                    kind=SwingKind.LOW,
                    confirm_timestamp=confirm_ts,
                )
            )

    swings.sort(key=lambda s: s.timestamp)
    return swings
