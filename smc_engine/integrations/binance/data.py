"""Binance REST helper'ları — raw kline/OI/funding payload'larını DataFrame'e dönüştürür.

Saf yardımcı fonksiyonlar; ağ çağrısı YAPMAZ — input ham listeler/dict'ler.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from smc_engine.types import TimeFrame


# TimeFrame -> Binance interval string
TF_TO_BINANCE_INTERVAL: dict[TimeFrame, str] = {
    TimeFrame.M15: "15m",
    TimeFrame.H1: "1h",
    TimeFrame.H4: "4h",
    TimeFrame.H8: "8h",
    TimeFrame.D1: "1d",
}


def tf_to_binance_interval(tf: TimeFrame) -> str:
    if tf not in TF_TO_BINANCE_INTERVAL:
        raise ValueError(f"Desteklenmeyen TimeFrame: {tf}")
    return TF_TO_BINANCE_INTERVAL[tf]


def klines_to_dataframe(raw_rows: list, include_forming: bool = False) -> pd.DataFrame:
    """python-binance futures_klines çıktısını OHLCV DataFrame'e dönüştürür.

    Spec §3 look-ahead garantisi: ``include_forming=False`` (default) →
    ``close_time`` şu andan büyük olan satır (forming bar) atılır.
    """
    if not raw_rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]).astype(float)

    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    open_times: list[datetime] = []
    rows: list[tuple[float, float, float, float, float]] = []
    for r in raw_rows:
        close_time_ms = int(r[6])
        if not include_forming and close_time_ms > now_ms:
            continue
        open_time_ms = int(r[0])
        open_times.append(datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc).replace(tzinfo=None))
        rows.append((float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))

    df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex(open_times, name="timestamp"))
    return df


def funding_payload_to_float(payload: list) -> float:
    """``futures_funding_rate`` çıktısı [{symbol, fundingRate, fundingTime}, ...]; en güncel float."""
    if not payload:
        return 0.0
    return float(payload[-1]["fundingRate"])


def open_interest_payload_to_float(payload: dict) -> float:
    """``futures_open_interest`` çıktısı {symbol, openInterest, ...}; float'a çevir."""
    if not payload or "openInterest" not in payload:
        return 0.0
    return float(payload["openInterest"])
