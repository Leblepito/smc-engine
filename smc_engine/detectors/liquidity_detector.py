"""Liquidity detektoru — Sweep / Deviation / SFP — Spec §5 (detektor tablosu).

Likidite olaylari = stop avi / sahte kirilim mantigi.

- **SWEEP**: bir likidite seviyesi (swing high/low veya equal high/low)
  fitille asilir ama mum o seviyenin **ters tarafinda kapatir**. Yukari
  likidite alinirsa ``direction = SHORT`` (yukari likidite tuketildi, asagi
  baski beklenir); asagi likidite alinirsa ``LONG``.

- **DEVIATION**: fiyat bilinen bir seviyenin **ustunde kapatip** sonraki
  mumlarda tekrar **altina doner** (veya simetrik tersi) — Schwager
  "yaniltici kirilim". ``direction``: ustte kapatip kaybetti -> SHORT.

- **SFP (Swing Failure Pattern)**: onceki bir swing'i wick ile asar (likidite
  temizler) ama o swing'in **dogru tarafinda kapatir** -> ``reclaimed = True``.
  Asagi swing temizlenip yukari kapatilirsa ``LONG``.

**significance**: equal high/low gibi coklu-temas / bilinen kurumsal seviye
sweep'leri ``HIGH``; tekil swing sweep'leri ``LOW``.

**known_levels**: OPSIYONEL parametre (Spec §5.1). Verilmezse detektor sadece
``_swing_utils.find_swings`` ciktisi + tespit ettigi equal high/low'lar uzerinde
calisir. Verilirse (orchestrator Range sinirlari + Level fiyatlari gecer) sweep
tespiti bu seviyeleri de kapsar -> daha isabetli.

Saf fonksiyon:
``detect(ohlcv, config, known_levels=None, **kwargs) -> list[LiquidityEvent]``.
"""

from __future__ import annotations

import pandas as pd

from smc_engine.detectors._cluster_utils import cluster_by_price
from smc_engine.detectors._swing_utils import find_swings
from smc_engine.types import (
    Direction,
    LiquidityEvent,
    LiquidityKind,
    Significance,
    SwingKind,
)

# Deviation: kirilim sonrasi geri donusun kac mum icinde olmasi gerektigi.
_DEVIATION_LOOKAHEAD = 3


def _local_extrema(values, kind: str) -> list[tuple[int, float]]:
    """1-pencereli local extrema (pivot) — equal-level tespiti icin.

    ``find_swings`` 4-mum swing'i equal high/low tespiti icin fazla kati: iki
    yakin tepe genelde 4 mum siginmaz. Burada her mumun *yanindaki* mumlara
    gore yerel tepe/dip'i (``>=`` / ``<=``) buluruz — equal high/low havuzlari
    bu yerel uc noktalardan kumelenir.

    Args:
        values: high (kind="high") veya low (kind="low") numpy dizisi.
        kind: "high" -> yerel maks, "low" -> yerel min.

    Returns:
        ``(index, price)`` ciftleri listesi.
    """
    n = len(values)
    out: list[tuple[int, float]] = []
    for i in range(1, n - 1):
        v = float(values[i])
        if kind == "high":
            if v >= float(values[i - 1]) and v >= float(values[i + 1]):
                out.append((i, v))
        else:
            if v <= float(values[i - 1]) and v <= float(values[i + 1]):
                out.append((i, v))
    return out


def _find_equal_levels(
    highs, lows, tolerance: float
) -> tuple[list[tuple[float, int]], list[tuple[float, int]]]:
    """Yerel tepe/dip'ler arasinda equal (coklu-temas) seviyeleri bul.

    Iki yerel uc nokta ayni seviye sayilir: goreli fiyat farki <=
    ``tolerance``. Donen: (equal_high'lar, equal_low'lar) — her oge
    ``(price, formed_idx)``: ``price`` kumenin temsilci fiyati (high'ta max,
    low'da min), ``formed_idx`` kumenin 2. temasinin (seviyenin "olustugu"
    an) bar index'i. Sweep taramasi yalnizca ``formed_idx``'ten sonraki
    barlari dikkate alir (gelecege bakma).
    """
    high_pivots = sorted(_local_extrema(highs, "high"), key=lambda t: t[1])
    low_pivots = sorted(_local_extrema(lows, "low"), key=lambda t: t[1])

    def _cluster(
        pivots: list[tuple[int, float]], pick_high: bool
    ) -> list[tuple[float, int]]:
        # U-11: paylasimli helper'a delege; ``price_of`` tuple'in 2. elemanini
        # cikarir (``(idx, price)``).
        clusters = cluster_by_price(
            pivots, tolerance, price_of=lambda t: t[1]
        )
        out: list[tuple[float, int]] = []
        for c in clusters:
            if len(c) >= 2:
                prices = [p for _, p in c]
                price = max(prices) if pick_high else min(prices)
                idxs = sorted(idx for idx, _ in c)
                formed_idx = idxs[1]  # 2. temas = seviye olustu
                out.append((price, formed_idx))
        return out

    return _cluster(high_pivots, True), _cluster(low_pivots, False)


def detect(
    ohlcv: pd.DataFrame, config, known_levels=None, **kwargs
) -> list[LiquidityEvent]:
    """Sweep / Deviation / SFP likidite olaylarini tespit et.

    Algoritma:
      1. ``find_swings`` -> swing high/low'lar.
      2. Equal high/low kumeleri (``equal_level_tolerance``) -> yuksek-onem
         likidite havuzlari.
      3. SWEEP taramasi: her mum, o ana kadar olusmus bir likidite
         seviyesini (swing high/low + equal level + known_levels) fitille
         asip ters tarafta kapatirsa -> SWEEP. Equal level / known_level
         sweep'i significance=HIGH, tekil swing sweep'i significance=LOW.
      4. SFP taramasi: mum onceki bir swing low'u wick ile asar ama USTUNDE
         kapatir -> SFP, direction=LONG, reclaimed=True (simetrik: swing high
         icin SHORT). SFP ayni zamanda "reclaimed sweep" oldugundan ayri tip.
      5. DEVIATION taramasi: known_levels uzerinde — mum bir seviyenin
         ustunde kapatir, sonraki ``_DEVIATION_LOOKAHEAD`` mum icinde fiyat
         tekrar altina doner -> DEVIATION (simetrik tersi de).

    NOT (U-12): DEVIATION look-ahead penceresi sabit (3 bar). Bu nedenle
    ``ohlcv`` diliminin son ``_DEVIATION_LOOKAHEAD`` bari icinde olusan bir
    breakout, sonraki bar(lar) bilinmedigi icin DEVIATION olarak tespit
    EDILEMEZ. Walk-forward / harness'ta ardisik pencerelerle calisilir; bir
    pencerenin son barlarinin DEVIATION'i bir sonraki pencerede yakalanir
    (gerekli ise — orchestrator analiz noktasi her bar yeniden hesaplandigi
    icin pratikte yalnizca "acik DEVIATION" durumu kor noktada kalir).

    Args:
        ohlcv: ``open/high/low/close`` kolonlu, ``DatetimeIndex``'li df.
        config: ``swing_lookback``, ``equal_level_tolerance`` ozellikleri.
        known_levels: OPSIYONEL float fiyat listesi (orchestrator gecer).
        **kwargs: diger opsiyonel context.

    Returns:
        Timestamp'e gore artan sirali ``LiquidityEvent`` listesi.
    """
    n = len(ohlcv)
    if n < 3:
        return []

    lookback = getattr(config, "swing_lookback", 4)
    tolerance = getattr(config, "equal_level_tolerance", 0.001)

    opens = ohlcv["open"].to_numpy()
    highs = ohlcv["high"].to_numpy()
    lows = ohlcv["low"].to_numpy()
    closes = ohlcv["close"].to_numpy()
    index = ohlcv.index

    swings = find_swings(ohlcv, lookback=lookback)
    swing_highs = [s for s in swings if s.kind == SwingKind.HIGH]
    swing_lows = [s for s in swings if s.kind == SwingKind.LOW]

    eq_highs, eq_lows = _find_equal_levels(highs, lows, tolerance)

    known = [float(k) for k in known_levels] if known_levels else []

    events: list[LiquidityEvent] = []
    # Ayni (kind, timestamp, direction) uclusunu birden fazla eklememek icin.
    seen: set = set()

    def _add(ev: LiquidityEvent) -> None:
        key = (ev.kind, ev.candle_ts, ev.direction)
        if key not in seen:
            seen.add(key)
            events.append(ev)

    def _eq_or_known(price: float) -> bool:
        """Verilen fiyat bir equal-level veya known_level'e denk mi (HIGH onem)."""
        for ep, _ in eq_highs:
            if abs(price - ep) / max(abs(ep), 1e-9) <= tolerance:
                return True
        for ep, _ in eq_lows:
            if abs(price - ep) / max(abs(ep), 1e-9) <= tolerance:
                return True
        for k in known:
            if abs(price - k) / max(abs(k), 1e-9) <= tolerance:
                return True
        return False

    for i in range(n):
        ts = index[i].to_pydatetime()
        o, h, l, c = (
            float(opens[i]),
            float(highs[i]),
            float(lows[i]),
            float(closes[i]),
        )

        # O ana kadar olusmus swing'ler (gelecege bakma yok).
        prior_highs = [sw for sw in swing_highs if sw.timestamp < ts]
        prior_lows = [sw for sw in swing_lows if sw.timestamp < ts]
        # O ana kadar olusmus equal-level'lar (formed_idx < i).
        active_eq_highs = [ep for ep, fidx in eq_highs if fidx < i]
        active_eq_lows = [ep for ep, fidx in eq_lows if fidx < i]

        # Bu barda sweep'lenebilecek YUKARI likidite seviyeleri:
        #   swing high'lar + equal high'lar + known_levels.
        up_levels = (
            [sw.price for sw in prior_highs] + active_eq_highs + known
        )
        down_levels = (
            [sw.price for sw in prior_lows] + active_eq_lows + known
        )

        # --- SFP: bir likidite seviyesini wick ile as, DOGRU tarafta kapat ---
        # Yukari swing temizlenip ALTINDA kapatilirsa -> SFP SHORT.
        for lvl in sorted(up_levels, reverse=True):
            if h > lvl and c < lvl:
                _add(
                    LiquidityEvent(
                        kind=LiquidityKind.SFP,
                        swept_price=float(lvl),
                        direction=Direction.SHORT,
                        candle_ts=ts,
                        reclaimed=True,
                        significance=(
                            Significance.HIGH
                            if _eq_or_known(lvl)
                            else Significance.LOW
                        ),
                    )
                )
                break
        # Asagi swing temizlenip USTUNDE kapatilirsa -> SFP LONG.
        for lvl in sorted(down_levels):
            if l < lvl and c > lvl:
                _add(
                    LiquidityEvent(
                        kind=LiquidityKind.SFP,
                        swept_price=float(lvl),
                        direction=Direction.LONG,
                        candle_ts=ts,
                        reclaimed=True,
                        significance=(
                            Significance.HIGH
                            if _eq_or_known(lvl)
                            else Significance.LOW
                        ),
                    )
                )
                break

        # --- SWEEP: seviyeyi fitille as, TERS tarafta kapat (reclaimed=False) ---
        # Yukari sweep: high seviyeyi gecer, close seviyenin ALTINDA kapatir.
        for lvl in sorted(up_levels, reverse=True):
            if h > lvl and c < lvl:
                _add(
                    LiquidityEvent(
                        kind=LiquidityKind.SWEEP,
                        swept_price=float(lvl),
                        direction=Direction.SHORT,
                        candle_ts=ts,
                        reclaimed=False,
                        significance=(
                            Significance.HIGH
                            if _eq_or_known(lvl)
                            else Significance.LOW
                        ),
                    )
                )
                break
        # Asagi sweep: low seviyeyi gecer, close seviyenin USTUNDE kapatir.
        for lvl in sorted(down_levels):
            if l < lvl and c > lvl:
                _add(
                    LiquidityEvent(
                        kind=LiquidityKind.SWEEP,
                        swept_price=float(lvl),
                        direction=Direction.LONG,
                        candle_ts=ts,
                        reclaimed=False,
                        significance=(
                            Significance.HIGH
                            if _eq_or_known(lvl)
                            else Significance.LOW
                        ),
                    )
                )
                break

        # --- DEVIATION: known_level ustunde kapat, sonra altina geri don ---
        for k in known:
            # Bu mum seviyenin USTUNDE kapatti mi (yaniltici yukari kirilim)?
            if c > k and (o <= k or l <= k):
                for j in range(i + 1, min(i + 1 + _DEVIATION_LOOKAHEAD, n)):
                    if float(closes[j]) < k:
                        _add(
                            LiquidityEvent(
                                kind=LiquidityKind.DEVIATION,
                                swept_price=float(k),
                                direction=Direction.SHORT,
                                candle_ts=index[j].to_pydatetime(),
                                reclaimed=False,
                                significance=Significance.HIGH,
                            )
                        )
                        break
            # Bu mum seviyenin ALTINDA kapatti mi (yaniltici asagi kirilim)?
            if c < k and (o >= k or h >= k):
                for j in range(i + 1, min(i + 1 + _DEVIATION_LOOKAHEAD, n)):
                    if float(closes[j]) > k:
                        _add(
                            LiquidityEvent(
                                kind=LiquidityKind.DEVIATION,
                                swept_price=float(k),
                                direction=Direction.LONG,
                                candle_ts=index[j].to_pydatetime(),
                                reclaimed=False,
                                significance=Significance.HIGH,
                            )
                        )
                        break

    events.sort(key=lambda e: e.candle_ts)
    return events
