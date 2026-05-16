"""Zone detektoru — Efloud Order Block + Breaker Block — Spec §5 (detektor tablosu).

**Efloud OB**: pump/dump oncesi mum + oncesindeki pes pese ayni renkli mumlar.
- Bullish OB (DEMAND): son **bearish** mum + ardindan **istekli bullish breakout**.
  Zone = OB mumunun body'si (BODY anchor) veya wick'i (WICK anchor).
- Bearish OB (SUPPLY): son **bullish** mum + ardindan **istekli bearish breakout**.

Istekli breakout: breakout mumunun body'si, OB mumunun toplam range'inin
``config.ob_breakout_threshold`` (varsayilan 1.5) katindan buyuk/esit.

**Breaker Block / Indecision Candle**: iki ayni yonlu mum arasinda kalan kucuk
ters mum (kararsizlik); her yerde olusabilir, kucuk R. DEMAND/SUPPLY olarak
isaretlenir (ters mumun yonune gore).

OB mumlari swing high/low konumunda aranir (``_swing_utils.find_swings``):
pump oncesi mum bir swing low yakininda, dump oncesi mum bir swing high
yakininda olmali — kurumsal donus noktasi mantigi.

``Zone.status = FRESH`` ve ``Zone.age_bars = 0`` (olusum aninda; orchestrator
ileride gunceller). Tum zaman referanslari ``datetime``.

Saf fonksiyon: ``detect(ohlcv, config, **kwargs) -> list[Zone]``.
"""

from __future__ import annotations

import pandas as pd

from smc_engine.detectors._swing_utils import find_swings
from smc_engine.types import (
    SwingKind,
    TimeFrame,
    Zone,
    ZoneAnchor,
    ZoneKind,
    ZoneStatus,
)

# Asset profili varsayilani: kripto -> BODY, forex -> WICK (Spec §5 detektor
# tablosu). v1 varsayilan BODY; ``config.zone_anchor`` ile override edilebilir.
_DEFAULT_ANCHOR = ZoneAnchor.BODY


def _resolve_anchor(config) -> ZoneAnchor:
    raw = getattr(config, "zone_anchor", None)
    if raw is None:
        return _DEFAULT_ANCHOR
    if isinstance(raw, ZoneAnchor):
        return raw
    # string ("WICK" / "BODY")
    return ZoneAnchor[str(raw).upper()]


def _zone_bounds(o, h, l, c, anchor: ZoneAnchor) -> tuple[float, float]:
    """OB mumundan zone sinirlarini cikar (anchor profiline gore).

    BODY anchor -> body sinirlari (open/close); WICK anchor -> high/low.
    Her durumda top = max, bottom = min.
    """
    if anchor == ZoneAnchor.WICK:
        return float(h), float(l)
    top = max(o, c)
    bottom = min(o, c)
    return float(top), float(bottom)


def detect(ohlcv: pd.DataFrame, config, **kwargs) -> list[Zone]:
    """Efloud OB + Breaker Block zone'larini tespit et.

    Algoritma:
      1. ``find_swings`` ile swing high/low timestamp'leri (config.swing_lookback).
      2. Order Block taramasi — her mum cifti (i = OB adayi, i+1 = breakout):
         - DEMAND OB: mum[i] bearish (close < open), mum[i+1] bullish
           (close > open) ve istekli (breakout body >= threshold x OB range),
           ve mum[i] bir swing LOW konumunda (donus noktasi).
         - SUPPLY OB: mum[i] bullish, mum[i+1] bearish istekli breakout,
           mum[i] bir swing HIGH konumunda.
      3. Breaker Block / Indecision: ardisik uclu (a, b, c) — a ve c ayni
         yonde, b ters yonde ve b'nin body'si a'nin body'sinden kucuk
         (kararsizlik mumu). b DEMAND/SUPPLY zone olur (yonune gore).
      4. Her zone: status=FRESH, age_bars=0, created_at=origin_candle_ts.

    Args:
        ohlcv: ``open/high/low/close`` kolonlu, ``DatetimeIndex``'li df.
        config: ``swing_lookback``, ``ob_breakout_threshold`` ve opsiyonel
            ``zone_anchor`` ozellikleri.
        **kwargs: orchestrator opsiyonel context (Faz 1B'de kullanilmaz).

    Returns:
        Timestamp'e gore artan sirali ``Zone`` listesi.
    """
    n = len(ohlcv)
    if n < 3:
        return []

    lookback = getattr(config, "swing_lookback", 4)
    threshold = getattr(config, "ob_breakout_threshold", 1.5)
    anchor = _resolve_anchor(config)
    tf = getattr(config, "timeframe", None) or TimeFrame.D1

    opens = ohlcv["open"].to_numpy()
    highs = ohlcv["high"].to_numpy()
    lows = ohlcv["low"].to_numpy()
    closes = ohlcv["close"].to_numpy()
    index = ohlcv.index

    swings = find_swings(ohlcv, lookback=lookback)
    swing_low_ts = {s.timestamp for s in swings if s.kind == SwingKind.LOW}
    swing_high_ts = {s.timestamp for s in swings if s.kind == SwingKind.HIGH}

    zones: list[Zone] = []
    seen_origin: set = set()

    # --- 1. Order Block taramasi ---
    for i in range(n - 1):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        ob_range = float(h) - float(l)
        if ob_range <= 0:
            continue

        bo, bc = opens[i + 1], closes[i + 1]
        breakout_body = abs(float(bc) - float(bo))
        if breakout_body < threshold * ob_range:
            continue  # istekli degil

        ob_ts = index[i].to_pydatetime()
        ob_is_bearish = c < o
        ob_is_bullish = c > o
        breakout_is_bullish = bc > bo
        breakout_is_bearish = bc < bo

        kind = None
        # DEMAND: bearish OB + istekli bullish breakout, swing LOW konumunda
        if ob_is_bearish and breakout_is_bullish and ob_ts in swing_low_ts:
            kind = ZoneKind.DEMAND
        # SUPPLY: bullish OB + istekli bearish breakout, swing HIGH konumunda
        elif ob_is_bullish and breakout_is_bearish and ob_ts in swing_high_ts:
            kind = ZoneKind.SUPPLY

        if kind is None:
            continue

        top, bottom = _zone_bounds(o, h, l, c, anchor)
        zones.append(
            Zone(
                kind=kind,
                top=top,
                bottom=bottom,
                timeframe=tf,
                created_at=ob_ts,
                status=ZoneStatus.FRESH,
                origin_candle_ts=ob_ts,
                anchor=anchor,
                age_bars=0,
            )
        )
        seen_origin.add(ob_ts)

    # --- 2. Breaker Block / Indecision Candle taramasi ---
    for i in range(1, n - 1):
        a_o, a_c = opens[i - 1], closes[i - 1]
        b_o, b_h, b_l, b_c = opens[i], highs[i], lows[i], closes[i]
        c_o, c_c = opens[i + 1], closes[i + 1]

        a_body = abs(float(a_c) - float(a_o))
        b_body = abs(float(b_c) - float(b_o))

        a_bullish = a_c > a_o
        b_bullish = b_c > b_o
        c_bullish = c_c > c_o
        a_bearish = a_c < a_o
        b_bearish = b_c < b_o
        c_bearish = c_c < c_o

        # a ve c ayni yonde, b ters yonde + kucuk body (kararsizlik)
        same_dir_up = a_bullish and c_bullish and b_bearish
        same_dir_down = a_bearish and c_bearish and b_bullish
        if not (same_dir_up or same_dir_down):
            continue
        if a_body <= 0 or b_body >= a_body:
            continue  # b yeterince kucuk degil

        b_ts = index[i].to_pydatetime()
        if b_ts in seen_origin:
            continue  # zaten OB olarak isaretlendi

        # kararsizlik mumu cevreleyen trendin yonunde POI:
        # yukari trend ortasinda -> DEMAND, asagi trend ortasinda -> SUPPLY
        kind = ZoneKind.DEMAND if same_dir_up else ZoneKind.SUPPLY
        top, bottom = _zone_bounds(b_o, b_h, b_l, b_c, anchor)
        zones.append(
            Zone(
                kind=kind,
                top=top,
                bottom=bottom,
                timeframe=tf,
                created_at=b_ts,
                status=ZoneStatus.FRESH,
                origin_candle_ts=b_ts,
                anchor=anchor,
                age_bars=0,
            )
        )
        seen_origin.add(b_ts)

    zones.sort(key=lambda z: z.origin_candle_ts)
    return zones
