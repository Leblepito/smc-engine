"""Range detektoru — RH/RL/EQ + premium/discount — Spec §5 (detektor tablosu).

Range = fiyatin yatay salindigi bolge. Coklu-swing ile dogrulanir: en az 2
swing high yakin bir seviyede (RH) **ve** en az 2 swing low yakin bir seviyede
(RL) olmali. EQ = (RH + RL) / 2. EQ alti = discount, EQ ustu = premium.

v1: dominant HTF range — en genis + en guncel tek range. Tek-yonlu trend
(coklu-swing dogrulamasi saglanmaz) -> bos liste.

Saf fonksiyon: ``detect(ohlcv, config, **kwargs) -> list[Range]``.
``_swing_utils.find_swings`` ile swing'leri bulur.
"""

from __future__ import annotations

import pandas as pd

from smc_engine.detectors._cluster_utils import cluster_by_price
from smc_engine.detectors._swing_utils import find_swings
from smc_engine.types import Range, SwingKind, SwingPoint, TimeFrame


def _cluster(points: list[SwingPoint], tolerance: float) -> list[list[SwingPoint]]:
    """Swing'leri fiyat yakinligina gore kumeler.

    U-11: ``detectors._cluster_utils.cluster_by_price`` paylasimli yardimciya
    delege eder; davranis ayni — ``price_of=lambda s: s.price``.
    """
    return cluster_by_price(points, tolerance, price_of=lambda s: s.price)


def detect(ohlcv: pd.DataFrame, config, **kwargs) -> list[Range]:
    """Dominant HTF range'i tespit et (RH/RL/EQ + premium/discount).

    Algoritma:
      1. ``find_swings`` ile swing high/low'lar (config.swing_lookback).
      2. Swing high'lari ve swing low'lari ayri ayri fiyat yakinligina gore
         kumele (``equal_level_tolerance``).
      3. >=2 uyeli high kumesi VE >=2 uyeli low kumesi gerekir; aksi halde
         range yok (tek-yonlu trend) -> [].
      4. Aday range = (gecerli high kumesi, gecerli low kumesi) cifti.
         RH = high kumesinin en yuksek high'i, RL = low kumesinin en dusuk
         low'u. v1 dominant: en genis (RH-RL) range secilir; esitlikte en
         guncel (formed_at en buyuk).
      5. EQ = (RH+RL)/2; discount = (RL, EQ); premium = (EQ, RH).
      6. formed_at = range'i dogrulayan swing'ler arasinda en gec timestamp.

    Args:
        ohlcv: ``open/high/low/close`` kolonlu, ``DatetimeIndex``'li df.
        config: ``swing_lookback`` ve ``equal_level_tolerance`` ozellikleri.
        **kwargs: orchestrator opsiyonel context (Faz 1A'da kullanilmaz).

    Returns:
        En fazla 1 ``Range`` iceren liste (v1 dominant); range yoksa [].
    """
    lookback = getattr(config, "swing_lookback", 4)
    tolerance = getattr(config, "equal_level_tolerance", 0.001)

    swings = find_swings(ohlcv, lookback=lookback)
    highs = [s for s in swings if s.kind == SwingKind.HIGH]
    lows = [s for s in swings if s.kind == SwingKind.LOW]

    high_clusters = [c for c in _cluster(highs, tolerance) if len(c) >= 2]
    low_clusters = [c for c in _cluster(lows, tolerance) if len(c) >= 2]

    if not high_clusters or not low_clusters:
        return []

    best: tuple | None = None  # (width, formed_at, rh, rl, validating_swings)
    for hc in high_clusters:
        rh = max(s.price for s in hc)
        for lc in low_clusters:
            rl = min(s.price for s in lc)
            if rh <= rl:
                continue
            width = rh - rl
            validating = hc + lc
            formed_at = max(s.timestamp for s in validating)
            cand = (width, formed_at, rh, rl)
            if best is None or (width, formed_at) > (best[0], best[1]):
                best = cand

    if best is None:
        return []

    _, formed_at, rh, rl = best
    eq = (rh + rl) / 2

    return [
        Range(
            high=rh,
            low=rl,
            equilibrium=eq,
            premium_zone=(eq, rh),
            discount_zone=(rl, eq),
            timeframe=getattr(config, "timeframe", None) or TimeFrame.D1,
            formed_at=formed_at,
        )
    ]
