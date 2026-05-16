"""Level detektoru — kurumsal referans seviyeleri + funding window — Spec §5.

Smart money takvim-bazli acilislarda islem alir. Bu detektor OHLCV'den su
kurumsal seviyeleri cikarir:

- **YO** (Year Open): icinde bulunulan takvim yilinin ilk mumunun open'i.
- **MO** (Month Open): her ayin ilk is gununun open'i (``time_utils.month_open``).
- **PMO** (Previous Month Open): bir onceki ayin MO'su.
- **WO** (Week Open): her forex hafta acilisinin (Pazar 21:00 UTC,
  ``time_utils.week_open``) o ana en yakin mumunun open'i.
- **PWO** (Previous Week Open): bir onceki WO.
- **DO** (Day Open): her takvim gununun ilk mumunun open'i (intraday TF'lerde
  anlamli; D1'de her mum zaten bir gun).
- **MONDAY_H / MONDAY_L**: her haftanin Pazartesi mum(lar)inin high/low'u.
- **OLD_ATH / PREV_ATH**: tarihsel en yuksek (running max high) — guncel
  running ATH ``OLD_ATH``, bir onceki ATH platosu ``PREV_ATH``.

``valid_from`` seviyenin olustugu an, ``valid_until`` bir sonraki ayni-tip
seviyenin olusumu (yoksa ``None`` — hala gecerli).

``time_utils.py`` takvim hesaplarini saglar (UTC). Funding window'lari
``time_utils.FUNDING_WINDOWS_UTC``'de tanimli; bu detektor funding zamanlarini
ayri Level uretmez (risk_guard ``is_near_funding`` ile dogrudan kontrol eder),
ama intraday TF'lerde DO seviyeleri funding-hizali gunluk referanslar saglar.

Saf fonksiyon: ``detect(ohlcv, config, **kwargs) -> list[Level]``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from smc_engine import time_utils
from smc_engine.types import Level, LevelKind, TimeFrame

UTC = timezone.utc


def _to_dt(ts) -> datetime:
    """pandas Timestamp -> python datetime (tz korunur, naive ise oldugu gibi)."""
    return ts.to_pydatetime()


def _is_intraday(index: pd.DatetimeIndex) -> bool:
    """Index'in TF'i gun-altinda mi (ardisik mumlar arasi < 1 gun)?

    U-14: TF cikarimi once ``pd.infer_freq`` ile denenir (duzenli index'lerde
    daha saglam: outlier-tolerant). Basarisizsa eski yontem (index[1]-index[0])
    fallback olarak kalir.
    """
    if len(index) < 2:
        return False
    try:
        freq = pd.infer_freq(index)
    except (TypeError, ValueError):
        freq = None
    if freq is not None:
        try:
            offset = pd.tseries.frequencies.to_offset(freq)
            return offset is not None and offset.nanos < pd.Timedelta(days=1).value
        except (ValueError, AttributeError):
            pass  # bazi offset'ler (ay/yil) nanos vermez -> fallback
    delta = index[1] - index[0]
    return delta < pd.Timedelta(days=1)


def _close_valid_windows(levels: list[Level]) -> list[Level]:
    """Ayni-tip ardisik seviyelerin ``valid_until``'unu bir sonrakinin
    ``valid_from``'una ayarla (son seviye ``None`` = hala gecerli)."""
    by_kind: dict[LevelKind, list[Level]] = {}
    for lv in levels:
        by_kind.setdefault(lv.kind, []).append(lv)

    out: list[Level] = []
    for kind, group in by_kind.items():
        group.sort(key=lambda x: x.valid_from)
        for i, lv in enumerate(group):
            valid_until = (
                group[i + 1].valid_from if i + 1 < len(group) else None
            )
            out.append(
                Level(
                    kind=lv.kind,
                    price=lv.price,
                    timeframe=lv.timeframe,
                    valid_from=lv.valid_from,
                    valid_until=valid_until,
                )
            )
    out.sort(key=lambda x: (x.valid_from, x.kind.value))
    return out


def detect(ohlcv: pd.DataFrame, config, **kwargs) -> list[Level]:
    """Kurumsal referans seviyelerini tespit et.

    Algoritma:
      1. YO: index'teki en erken yilin ilk mumunun open'i.
      2. MO/DO: her takvim ay / gun degisiminde o periyodun ilk mumunun open'i.
         PMO = bir onceki MO.
      3. WO: ``time_utils.week_open`` her mum icin hesaplanir; yeni bir hafta
         acilisi gorulurse o haftanin ilk mumunun open'i WO olur. PWO = onceki.
      4. MONDAY_H/L: Pazartesi (UTC weekday 0) mumlarinin high/low'u (intraday
         TF'de ayni gunun tum mumlari toplulastirilir).
      5. OLD_ATH/PREV_ATH: running max high; her yeni ATH bir OLD_ATH, bir
         onceki ATH platosu PREV_ATH.
      6. ``valid_until`` ayni-tip bir sonraki seviyenin ``valid_from``'u.

    Args:
        ohlcv: ``open/high/low/close`` kolonlu, ``DatetimeIndex``'li df.
        config: config nesnesi (opsiyonel ``timeframe``).
        **kwargs: orchestrator opsiyonel context (Faz 1B'de kullanilmaz).

    Returns:
        ``valid_from`` + ``kind``'a gore sirali ``Level`` listesi.
    """
    n = len(ohlcv)
    if n == 0:
        return []

    tf = getattr(config, "timeframe", None) or (
        TimeFrame.H4 if _is_intraday(ohlcv.index) else TimeFrame.D1
    )

    opens = ohlcv["open"].to_numpy()
    highs = ohlcv["high"].to_numpy()
    lows = ohlcv["low"].to_numpy()
    index = ohlcv.index

    raw: list[Level] = []

    # --- 1. YO — ilk mumun open'i (fixture'lar tek yillik pencere) ---
    raw.append(
        Level(
            kind=LevelKind.YO,
            price=float(opens[0]),
            timeframe=tf,
            valid_from=_to_dt(index[0]),
            valid_until=None,
        )
    )

    # --- 2-3. MO / DO / WO — periyot degisimleri ---
    prev_month_key = None
    prev_day_key = None
    prev_week_open = None

    for i in range(n):
        ts = index[i]
        dt = _to_dt(ts)

        month_key = (ts.year, ts.month)
        if month_key != prev_month_key:
            raw.append(
                Level(
                    kind=LevelKind.MO,
                    price=float(opens[i]),
                    timeframe=tf,
                    valid_from=dt,
                    valid_until=None,
                )
            )
            prev_month_key = month_key

        day_key = (ts.year, ts.month, ts.day)
        if day_key != prev_day_key:
            raw.append(
                Level(
                    kind=LevelKind.DO,
                    price=float(opens[i]),
                    timeframe=tf,
                    valid_from=dt,
                    valid_until=None,
                )
            )
            prev_day_key = day_key

        wo = time_utils.week_open(dt)
        # week_open tz-aware (UTC) doner; index naive olabilir — karsilastirma
        # icin naive'e indir.
        wo_cmp = wo.replace(tzinfo=None) if dt.tzinfo is None else wo
        if prev_week_open is None or wo_cmp != prev_week_open:
            raw.append(
                Level(
                    kind=LevelKind.WO,
                    price=float(opens[i]),
                    timeframe=tf,
                    valid_from=dt,
                    valid_until=None,
                )
            )
            prev_week_open = wo_cmp

    # --- PMO / PWO — onceki ay / hafta acilislari ---
    mos = sorted(
        (lv for lv in raw if lv.kind == LevelKind.MO), key=lambda x: x.valid_from
    )
    for i in range(1, len(mos)):
        raw.append(
            Level(
                kind=LevelKind.PMO,
                price=mos[i - 1].price,
                timeframe=tf,
                valid_from=mos[i].valid_from,
                valid_until=None,
            )
        )
    wos = sorted(
        (lv for lv in raw if lv.kind == LevelKind.WO), key=lambda x: x.valid_from
    )
    for i in range(1, len(wos)):
        raw.append(
            Level(
                kind=LevelKind.PWO,
                price=wos[i - 1].price,
                timeframe=tf,
                valid_from=wos[i].valid_from,
                valid_until=None,
            )
        )

    # --- 4. MONDAY_H / MONDAY_L — Pazartesi mumlari (UTC weekday 0) ---
    monday_groups: dict[tuple, list[int]] = {}
    for i in range(n):
        ts = index[i]
        if ts.weekday() == 0:  # Pazartesi
            wk = (ts.year, ts.isocalendar()[1])
            monday_groups.setdefault(wk, []).append(i)
    for wk, idxs in monday_groups.items():
        hi = max(float(highs[j]) for j in idxs)
        lo = min(float(lows[j]) for j in idxs)
        vf = _to_dt(index[idxs[0]])
        raw.append(
            Level(
                kind=LevelKind.MONDAY_H,
                price=hi,
                timeframe=tf,
                valid_from=vf,
                valid_until=None,
            )
        )
        raw.append(
            Level(
                kind=LevelKind.MONDAY_L,
                price=lo,
                timeframe=tf,
                valid_from=vf,
                valid_until=None,
            )
        )

    # --- 5. OLD_ATH / PREV_ATH — running max high ---
    running_ath = None
    prev_ath = None
    for i in range(n):
        h = float(highs[i])
        if running_ath is None or h > running_ath:
            if running_ath is not None:
                prev_ath = running_ath
            running_ath = h
            raw.append(
                Level(
                    kind=LevelKind.OLD_ATH,
                    price=running_ath,
                    timeframe=tf,
                    valid_from=_to_dt(index[i]),
                    valid_until=None,
                )
            )
            if prev_ath is not None:
                raw.append(
                    Level(
                        kind=LevelKind.PREV_ATH,
                        price=prev_ath,
                        timeframe=tf,
                        valid_from=_to_dt(index[i]),
                        valid_until=None,
                    )
                )

    return _close_valid_windows(raw)
