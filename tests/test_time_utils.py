"""time_utils.py testleri — bilinen tarihler, seans, funding, hafta sonu."""

from datetime import datetime, timezone

import pandas as pd
import pytest

from smc_engine.time_utils import (
    FUNDING_WINDOWS_UTC,
    SESSIONS,
    day_open,
    is_in_session,
    is_near_funding,
    is_weekend,
    monday_high_low,
    month_open,
    week_open,
)

UTC = timezone.utc


# ---------------- is_weekend ----------------


def test_is_weekend():
    # 2026-05-16 Cumartesi, 2026-05-17 Pazar
    assert is_weekend(datetime(2026, 5, 16, 12, tzinfo=UTC)) is True
    assert is_weekend(datetime(2026, 5, 17, 12, tzinfo=UTC)) is True
    # 2026-05-15 Cuma, 2026-05-18 Pazartesi
    assert is_weekend(datetime(2026, 5, 15, 12, tzinfo=UTC)) is False
    assert is_weekend(datetime(2026, 5, 18, 12, tzinfo=UTC)) is False


def test_is_weekend_naive_treated_as_utc():
    assert is_weekend(datetime(2026, 5, 16, 12)) is True


# ---------------- is_in_session ----------------


def test_is_in_session_london():
    # London 07:00-16:00 UTC
    assert is_in_session(datetime(2026, 5, 15, 10, tzinfo=UTC), "london") is True
    assert is_in_session(datetime(2026, 5, 15, 6, tzinfo=UTC), "london") is False
    assert is_in_session(datetime(2026, 5, 15, 16, tzinfo=UTC), "london") is False
    assert is_in_session(datetime(2026, 5, 15, 7, tzinfo=UTC), "london") is True


def test_is_in_session_newyork():
    # NewYork 13:00-22:00 UTC
    assert is_in_session(datetime(2026, 5, 15, 15, tzinfo=UTC), "newyork") is True
    assert is_in_session(datetime(2026, 5, 15, 12, tzinfo=UTC), "newyork") is False


def test_is_in_session_sydney_wraps_midnight():
    # Sydney 21:00-06:00 UTC — gece yarısını aşar
    assert is_in_session(datetime(2026, 5, 15, 23, tzinfo=UTC), "sydney") is True
    assert is_in_session(datetime(2026, 5, 15, 3, tzinfo=UTC), "sydney") is True
    assert is_in_session(datetime(2026, 5, 15, 12, tzinfo=UTC), "sydney") is False
    assert is_in_session(datetime(2026, 5, 15, 6, tzinfo=UTC), "sydney") is False
    assert is_in_session(datetime(2026, 5, 15, 21, tzinfo=UTC), "sydney") is True


def test_is_in_session_unknown_raises():
    with pytest.raises(ValueError):
        is_in_session(datetime(2026, 5, 15, 12, tzinfo=UTC), "frankfurt")


def test_sessions_table():
    assert SESSIONS == {
        "sydney": (21, 6),
        "tokyo": (0, 9),
        "london": (7, 16),
        "newyork": (13, 22),
    }


# ---------------- is_near_funding ----------------


def test_funding_windows_table():
    assert FUNDING_WINDOWS_UTC == [0, 8, 16]


def test_is_near_funding_at_window():
    # tam funding saatleri
    assert is_near_funding(datetime(2026, 5, 15, 8, 0, tzinfo=UTC)) is True
    assert is_near_funding(datetime(2026, 5, 15, 16, 0, tzinfo=UTC)) is True
    assert is_near_funding(datetime(2026, 5, 15, 0, 0, tzinfo=UTC)) is True


def test_is_near_funding_within_buffer():
    # 08:00 ±30dk
    assert is_near_funding(datetime(2026, 5, 15, 7, 35, tzinfo=UTC)) is True
    assert is_near_funding(datetime(2026, 5, 15, 8, 25, tzinfo=UTC)) is True
    # 00:00 sarması — 23:45 bir önceki günün... aslında 00:00'a 15dk
    assert is_near_funding(datetime(2026, 5, 15, 23, 45, tzinfo=UTC)) is True


def test_is_near_funding_outside_buffer():
    assert is_near_funding(datetime(2026, 5, 15, 4, 0, tzinfo=UTC)) is False
    assert is_near_funding(datetime(2026, 5, 15, 12, 0, tzinfo=UTC)) is False
    assert is_near_funding(datetime(2026, 5, 15, 8, 45, tzinfo=UTC)) is False


def test_is_near_funding_custom_buffer():
    # 60dk buffer ile 08:45 artık içeride
    assert is_near_funding(datetime(2026, 5, 15, 8, 45, tzinfo=UTC), buffer_minutes=60) is True


# ---------------- day_open ----------------


def test_day_open():
    d = datetime(2026, 5, 15, 14, 33, 12, tzinfo=UTC)
    assert day_open(d) == datetime(2026, 5, 15, 0, 0, tzinfo=UTC)


# ---------------- week_open (Pazar 21:00 UTC) ----------------


def test_week_open_midweek():
    # 2026-05-15 Cuma → en son Pazar 2026-05-10, 21:00 UTC
    d = datetime(2026, 5, 15, 14, 0, tzinfo=UTC)
    assert week_open(d) == datetime(2026, 5, 10, 21, 0, tzinfo=UTC)


def test_week_open_sunday_before_2100():
    # Pazar 2026-05-17 20:00 → bir önceki Pazar 2026-05-10 21:00
    d = datetime(2026, 5, 17, 20, 0, tzinfo=UTC)
    assert week_open(d) == datetime(2026, 5, 10, 21, 0, tzinfo=UTC)


def test_week_open_sunday_after_2100():
    # Pazar 2026-05-17 22:00 → aynı gün 21:00
    d = datetime(2026, 5, 17, 22, 0, tzinfo=UTC)
    assert week_open(d) == datetime(2026, 5, 17, 21, 0, tzinfo=UTC)


# ---------------- month_open (ilk iş günü 00:00 UTC) ----------------


def test_month_open_first_is_weekday():
    # 2026-05-01 Cuma → ilk iş günü 1 Mayıs
    d = datetime(2026, 5, 20, 10, tzinfo=UTC)
    assert month_open(d) == datetime(2026, 5, 1, 0, 0, tzinfo=UTC)


def test_month_open_first_is_weekend():
    # 2026-02-01 Pazar → ilk iş günü 2026-02-02 Pazartesi
    d = datetime(2026, 2, 15, 10, tzinfo=UTC)
    assert month_open(d) == datetime(2026, 2, 2, 0, 0, tzinfo=UTC)


def test_month_open_first_is_saturday():
    # 2026-08-01 Cumartesi → ilk iş günü 2026-08-03 Pazartesi
    d = datetime(2026, 8, 15, 10, tzinfo=UTC)
    assert month_open(d) == datetime(2026, 8, 3, 0, 0, tzinfo=UTC)


# ---------------- monday_high_low ----------------


def _ohlcv_week():
    # 2026-05-11 Pazartesi'den itibaren 5 günlük D1 mum
    idx = pd.date_range("2026-05-11", periods=5, freq="D")
    df = pd.DataFrame(
        {
            "open": [100, 101, 102, 103, 104],
            "high": [110, 108, 109, 107, 106],  # Pazartesi high = 110
            "low": [95, 96, 97, 98, 99],         # Pazartesi low = 95
            "close": [105, 104, 106, 105, 103],
            "volume": [1, 1, 1, 1, 1],
        },
        index=idx,
    )
    return df


def test_monday_high_low():
    df = _ohlcv_week()
    # Çarşamba 2026-05-13 sorgusu → o haftanın Pazartesi'si 2026-05-11
    hi, lo = monday_high_low(df, datetime(2026, 5, 13, 12, tzinfo=UTC))
    assert hi == 110.0
    assert lo == 95.0


def test_monday_high_low_missing_raises():
    df = _ohlcv_week()
    # 2026-06 — veride yok
    with pytest.raises(ValueError):
        monday_high_low(df, datetime(2026, 6, 10, 12, tzinfo=UTC))
