"""OHLCV veri cekme + parquet -- Spec R1 \u00a73 (data/fetch.py).

fetch_ohlcv: CCXT ile bir borsadan OHLCV ceker, DatetimeIndex'li DataFrame'e
cevirir, istege bagli parquet'e yazar.
load_parquet: parquet okur, DatetimeIndex'i geri kurar.

Tasarim notu (Spec): veri kaynagi STATIK export -- bir kez cek, parquet'e yaz,
sonra offline deterministik calis. fetch_ohlcv'a opsiyonel exchange nesnesi
gecirilebilir; bu dependency injection sayesinde network olmadan (mock ile)
test edilebilir.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pandas as pd

OHLCV_COLS = ["open", "high", "low", "close", "volume"]

# CCXT raw OHLCV satiri: [timestamp_ms, open, high, low, close, volume]


def _build_exchange(exchange_name: str) -> Any:
    """CCXT exchange nesnesi olustur. Import burada -- network/ccxt yoksa
    fetch_ohlcv'i exchange= ile cagiranlar etkilenmez."""
    import ccxt  # local import

    if not hasattr(ccxt, exchange_name):
        raise ValueError(f"bilinmeyen borsa: {exchange_name!r}")
    klass = getattr(ccxt, exchange_name)
    return klass({"enableRateLimit": True})


def _to_ms(ts: str) -> int:
    """ISO tarih string -> epoch milisaniye (UTC)."""
    return int(pd.Timestamp(ts, tz="UTC").timestamp() * 1000)


def ohlcv_rows_to_df(rows: list[list]) -> pd.DataFrame:
    """CCXT ham OHLCV satir listesini DatetimeIndex'li DataFrame'e cevirir."""
    df = pd.DataFrame(rows, columns=["timestamp", *OHLCV_COLS])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp").sort_index()
    # tekrar eden timestamp'leri at (CCXT sayfalama ortusmesi)
    df = df[~df.index.duplicated(keep="first")]
    return df[OHLCV_COLS].astype(float)


def fetch_ohlcv(
    symbol: str,
    timeframe: str,
    since: str,
    until: str,
    exchange: str | Any = "binance",
    out_path: Optional[str | Path] = None,
    limit: int = 1000,
) -> pd.DataFrame:
    """Bir borsadan OHLCV ceker.

    symbol: "BTC/USDT" gibi.
    timeframe: CCXT TF string ("1h", "4h", "1d" ...).
    since/until: ISO tarih string (UTC kabul edilir).
    exchange: borsa adi (str) VEYA hazir bir CCXT-uyumlu nesne (test/mock icin).
    out_path: verilirse sonuc parquet'e yazilir.

    Donen: [since, until) araliginda DatetimeIndex'li OHLCV DataFrame.
    """
    ex = exchange if not isinstance(exchange, str) else _build_exchange(exchange)

    since_ms = _to_ms(since)
    until_ms = _to_ms(until)

    all_rows: list[list] = []
    cursor = since_ms
    tf_ms = _timeframe_ms(timeframe)

    while cursor < until_ms:
        batch = ex.fetch_ohlcv(symbol, timeframe, since=cursor, limit=limit)
        if not batch:
            break
        all_rows.extend(batch)
        last_ts = batch[-1][0]
        next_cursor = last_ts + tf_ms
        if next_cursor <= cursor:
            break  # ilerleme yok -> sonsuz donguyu engelle
        cursor = next_cursor
        if len(batch) < limit:
            break  # borsa daha fazla veri dondurmedi

    df = ohlcv_rows_to_df(all_rows)
    # [since, until) araligina kirp
    df = df[(df.index >= pd.Timestamp(since, tz="UTC")) &
            (df.index < pd.Timestamp(until, tz="UTC"))]

    if out_path is not None:
        save_parquet(df, out_path)

    return df


# SMC sozlesmesi (TimeFrame enum) buyuk-harf TF kullanir: M15, H1, H4, H8, D1.
# CCXT lowercase ister: 15m, 1h, 4h, 1d. Bu yardimcida ikisini de kabul et:
# girdiyi once lower()'a indirip M/H/D/W bas-harfi -> CCXT formatina cevir.
_SMC_TO_CCXT = {"m": "m", "h": "h", "d": "d", "w": "w"}


def _timeframe_ms(timeframe: str) -> int:
    """TF string -> milisaniye.

    Kabul edilen formatlar (case-insensitive):
      - CCXT lowercase: 15m, 1h, 4h, 1d, 1w.
      - SMC enum-style: M15, H1, H4, H8, D1 — sayi/birim
        ters siralanmis; bu yardimci ikisini de normalize eder.
    """
    tf = timeframe.strip()
    if not tf:
        raise ValueError(f"desteklenmeyen timeframe: {timeframe!r}")
    factors = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
    # SMC enum-style: harf basta (ornek M15, H4, D1).
    first = tf[0].lower()
    last = tf[-1].lower()
    if first in factors and tf[1:].isdigit():
        qty = int(tf[1:])
        unit = first
    elif last in factors and tf[:-1].isdigit():
        qty = int(tf[:-1])
        unit = last
    else:
        raise ValueError(f"desteklenmeyen timeframe: {timeframe!r}")
    return qty * factors[unit]


def save_parquet(df: pd.DataFrame, path: str | Path) -> None:
    """OHLCV DataFrame'ini parquet'e yazar (DatetimeIndex korunur)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, engine="pyarrow")


def load_parquet(path: str | Path) -> pd.DataFrame:
    """Parquet okur, DatetimeIndex'i ayarlar, kolon semasini dogrular."""
    df = pd.read_parquet(path, engine="pyarrow")
    if not isinstance(df.index, pd.DatetimeIndex):
        # index parquet'te kolon olarak saklanmis olabilir
        for cand in ("timestamp", "index", "date"):
            if cand in df.columns:
                df = df.set_index(cand)
                break
        df.index = pd.to_datetime(df.index, utc=True)
    df = df.sort_index()
    missing = [c for c in OHLCV_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"load_parquet: eksik kolon(lar): {missing}")
    return df[OHLCV_COLS]
