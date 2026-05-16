"""Zaman / seans / funding yardımcıları — Spec §4.1.

Tüm dahili zaman damgaları **UTC**. Forex seans saatleri ve kripto funding
periyotları burada. Naive datetime'lar UTC kabul edilir.
"""

from __future__ import annotations

import calendar
from datetime import datetime, time, timedelta, timezone

import pandas as pd

UTC = timezone.utc

# Forex seans tanımları (UTC) — (başlangıç_saati, bitiş_saati)
SESSIONS: dict[str, tuple[int, int]] = {
    "sydney": (21, 6),   # gece yarısını aşar
    "tokyo": (0, 9),
    "london": (7, 16),
    "newyork": (13, 22),
}

# Kripto funding periyotları (Binance default) — her 8 saatte bir.
FUNDING_WINDOWS_UTC: list[int] = [0, 8, 16]
FUNDING_BUFFER_MINUTES: int = 30


def _as_utc(dt: datetime) -> datetime:
    """Naive datetime'ı UTC kabul et; aware ise UTC'ye çevir."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def is_weekend(dt: datetime) -> bool:
    """Cumartesi (5) veya Pazar (6) → True."""
    return _as_utc(dt).weekday() >= 5


def is_in_session(dt: datetime, session: str) -> bool:
    """Verilen UTC zamanı bir forex seansının içinde mi.

    Gece yarısını aşan seanslar (örn. sydney 21→06) doğru ele alınır.
    Bilinmeyen seans adı → ValueError.
    """
    if session not in SESSIONS:
        raise ValueError(f"bilinmeyen seans: {session!r}")
    start, end = SESSIONS[session]
    hour = _as_utc(dt).hour
    if start < end:
        return start <= hour < end
    # gece yarısını aşar
    return hour >= start or hour < end


def is_near_funding(dt: datetime, buffer_minutes: int = FUNDING_BUFFER_MINUTES) -> bool:
    """Zaman bir funding window'una ±buffer_minutes içinde mi.

    Funding window'ları gün içinde FUNDING_WINDOWS_UTC saatlerinde (0, 8, 16).
    Gün başını / sonunu aşan tamponlar için 24:00 ≡ 00:00 sarması da kontrol edilir.
    """
    d = _as_utc(dt)
    minute_of_day = d.hour * 60 + d.minute
    for fw_hour in FUNDING_WINDOWS_UTC:
        fw_minute = fw_hour * 60
        for anchor in (fw_minute, fw_minute - 1440, fw_minute + 1440):
            if abs(minute_of_day - anchor) <= buffer_minutes:
                return True
    return False


def day_open(dt: datetime) -> datetime:
    """Verilen zamanın gününün 00:00 UTC açılışı."""
    d = _as_utc(dt)
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def week_open(dt: datetime) -> datetime:
    """Forex hafta açılışı — Pazar 21:00 UTC.

    Verilen zamandan önceki (veya ona eşit) en son Pazar 21:00 UTC döner.

    NOT (U-13): Iki farkli hafta-basi konvansiyonu projede kullaniliyor:
      - ``week_open`` (BU FONKSIYON): **Forex** standardi — Pazar 21:00 UTC
        (NY kapanisi ~ Sydney acilisi). ``level_detector`` WO/PWO seviyelerinde
        bu fonksiyonu kullanir; ``risk_guard`` forex hafta-sonu gate'i de.
      - Takvim haftasi (Pazartesi 00:00 UTC) — ``level_detector`` MONDAY_H/L
        seviyeleri ``ts.weekday() == 0`` (Pazartesi) ile filtreler; bu
        fonksiyonu kullanmaz. Iki konvansiyon kasitli olarak ayri tutulur.
    """
    d = _as_utc(dt)
    # Geçerli gün gününün 21:00'i
    candidate = datetime(d.year, d.month, d.day, 21, 0, tzinfo=UTC)
    # Pazar = weekday() 6
    days_since_sunday = (d.weekday() - 6) % 7  # Pazar:0, Pzt:1, ... Cmt:6
    sunday = candidate - timedelta(days=days_since_sunday)
    if sunday > d:
        sunday -= timedelta(days=7)
    return sunday


def month_open(dt: datetime) -> datetime:
    """Ay açılışı — ayın ilk iş gününün (Pzt-Cum) 00:00 UTC'si."""
    d = _as_utc(dt)
    first = datetime(d.year, d.month, 1, tzinfo=UTC)
    # ilk iş gününe ilerle
    while first.weekday() >= 5:  # Cmt/Pzr atla
        first += timedelta(days=1)
    return first


def monday_high_low(ohlcv: pd.DataFrame, dt: datetime) -> tuple[float, float]:
    """``dt``'nin bulunduğu haftanın Pazartesi (UTC) high/low'u.

    ``ohlcv``: DatetimeIndex'li OHLCV DataFrame ('high', 'low' kolonları).
    O haftaya ait Pazartesi mumu yoksa ValueError.
    """
    d = _as_utc(dt)
    days_since_monday = d.weekday()  # Pzt:0
    monday = datetime(d.year, d.month, d.day, tzinfo=UTC) - timedelta(
        days=days_since_monday
    )
    next_day = monday + timedelta(days=1)

    idx = ohlcv.index
    # index tz-naive olabilir → UTC kabul ederek karşılaştır
    if idx.tz is None:
        lo = monday.replace(tzinfo=None)
        hi = next_day.replace(tzinfo=None)
    else:
        lo, hi = monday, next_day

    mask = (idx >= lo) & (idx < hi)
    week_slice = ohlcv.loc[mask]
    if week_slice.empty:
        raise ValueError(f"{monday.date()} için Pazartesi mumu bulunamadı")
    return float(week_slice["high"].max()), float(week_slice["low"].min())
