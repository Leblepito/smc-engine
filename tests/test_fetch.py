"""data/fetch.py testleri -- network YOK; mock exchange ile dependency injection.

Gercek CCXT API cagrisi yapilmaz. fetch_ohlcv'a fake bir exchange nesnesi
gecirilir; bu, fetch_ohlcv'in sayfalama + kirpma + parquet mantigini network
olmadan dogrular.
"""

import pandas as pd
import pytest

from data.fetch import (
    OHLCV_COLS,
    fetch_ohlcv,
    load_parquet,
    ohlcv_rows_to_df,
    save_parquet,
)


class FakeExchange:
    """CCXT-uyumlu minimal mock -- fetch_ohlcv'in cagirdigi tek metot."""

    def __init__(self, rows):
        # rows: tum [ts_ms, o, h, l, c, v] satirlari
        self._rows = sorted(rows, key=lambda r: r[0])
        self.calls = []

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=1000):
        self.calls.append((symbol, timeframe, since, limit))
        sel = [r for r in self._rows if since is None or r[0] >= since]
        return sel[:limit]


def _make_rows(start_iso, n, tf_ms=3_600_000):
    """n adet ardisik H1 OHLCV satiri uret (CCXT ham format)."""
    start_ms = int(pd.Timestamp(start_iso, tz="UTC").timestamp() * 1000)
    rows = []
    for i in range(n):
        ts = start_ms + i * tf_ms
        o = 100.0 + i
        rows.append([ts, o, o + 5, o - 3, o + 1, 10.0])
    return rows


# ---------------- ohlcv_rows_to_df ----------------


def test_ohlcv_rows_to_df_schema():
    rows = _make_rows("2026-01-01", 5)
    df = ohlcv_rows_to_df(rows)
    assert list(df.columns) == OHLCV_COLS
    assert isinstance(df.index, pd.DatetimeIndex)
    assert len(df) == 5
    assert df["open"].iloc[0] == 100.0


def test_ohlcv_rows_to_df_dedup():
    rows = _make_rows("2026-01-01", 3)
    rows.append(rows[1])  # tekrar eden timestamp
    df = ohlcv_rows_to_df(rows)
    assert len(df) == 3  # tekrar atildi


# ---------------- fetch_ohlcv (mock exchange) ----------------


def test_fetch_ohlcv_basic():
    rows = _make_rows("2026-01-01", 168)  # 1 hafta H1
    ex = FakeExchange(rows)
    df = fetch_ohlcv(
        "BTC/USDT", "1h",
        since="2026-01-01", until="2026-01-08",
        exchange=ex,
    )
    assert isinstance(df.index, pd.DatetimeIndex)
    assert list(df.columns) == OHLCV_COLS
    assert len(df) == 168
    # mock cagrildi
    assert len(ex.calls) >= 1


def test_fetch_ohlcv_pagination():
    # 250 satir, limit=100 -> birden fazla sayfa
    rows = _make_rows("2026-01-01", 250)
    ex = FakeExchange(rows)
    df = fetch_ohlcv(
        "BTC/USDT", "1h",
        since="2026-01-01", until="2026-01-12",
        exchange=ex, limit=100,
    )
    assert len(df) == 250
    assert len(ex.calls) >= 3  # 100 + 100 + 50


def test_fetch_ohlcv_clips_range():
    # 200 satir cek ama until ile 50 satire kirp
    rows = _make_rows("2026-01-01", 200)
    ex = FakeExchange(rows)
    until = pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=50)
    df = fetch_ohlcv(
        "BTC/USDT", "1h",
        since="2026-01-01", until=until.isoformat(),
        exchange=ex,
    )
    assert len(df) == 50  # [since, until) yarim-acik aralik


def test_fetch_ohlcv_empty():
    ex = FakeExchange([])
    df = fetch_ohlcv(
        "BTC/USDT", "1h",
        since="2026-01-01", until="2026-01-02",
        exchange=ex,
    )
    assert len(df) == 0
    assert list(df.columns) == OHLCV_COLS


# ---------------- save / load parquet ----------------


def test_parquet_roundtrip(tmp_path):
    rows = _make_rows("2026-01-01", 24)
    ex = FakeExchange(rows)
    out = tmp_path / "btc_h1.parquet"
    df = fetch_ohlcv(
        "BTC/USDT", "1h",
        since="2026-01-01", until="2026-01-02",
        exchange=ex, out_path=out,
    )
    assert out.exists()
    loaded = load_parquet(out)
    assert isinstance(loaded.index, pd.DatetimeIndex)
    assert list(loaded.columns) == OHLCV_COLS
    pd.testing.assert_frame_equal(df, loaded)


def test_save_load_parquet_direct(tmp_path):
    rows = _make_rows("2026-01-01", 10)
    df = ohlcv_rows_to_df(rows)
    p = tmp_path / "nested" / "data.parquet"
    save_parquet(df, p)  # nested dizin otomatik olusur
    assert p.exists()
    loaded = load_parquet(p)
    pd.testing.assert_frame_equal(df, loaded)


def test_load_parquet_missing_column_raises(tmp_path):
    rows = _make_rows("2026-01-01", 5)
    df = ohlcv_rows_to_df(rows).drop(columns=["volume"])
    p = tmp_path / "bad.parquet"
    df.to_parquet(p, engine="pyarrow")
    with pytest.raises(ValueError):
        load_parquet(p)
