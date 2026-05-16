"""R2a — Test bosluk #3: look-ahead test trade-uretebilen fixture (BTC).

Onceki test_no_lookahead_setup_fills_next_bar (test_harness.py) trade
uretmeyen sentetik veri ile "vacuously true" sayilabiliyordu. Bu test
gercek BTC ile trade uretildiginde de look-ahead disiplininin korundugunu
dogrular.
"""
from __future__ import annotations
import os
import pandas as pd
import pytest
from smc_engine.config import SMCConfig
from smc_engine.types import TimeFrame
from backtest.harness import run


_BTC_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "btc")


def _btc_available() -> bool:
    return all(
        os.path.exists(os.path.join(_BTC_DIR, f"BTCUSDT_{tf}.parquet"))
        for tf in ("D1", "H4", "H1", "M15")
    )


pytestmark = pytest.mark.skipif(
    not _btc_available(),
    reason="data/btc/*.parquet yok",
)


@pytest.fixture(scope="module")
def _btc_dataset():
    from data.fetch import load_parquet
    from data.resample import resample_ohlcv
    d1 = load_parquet(os.path.join(_BTC_DIR, "BTCUSDT_D1.parquet"))
    h4 = load_parquet(os.path.join(_BTC_DIR, "BTCUSDT_H4.parquet"))
    h1 = load_parquet(os.path.join(_BTC_DIR, "BTCUSDT_H1.parquet"))
    m15 = load_parquet(os.path.join(_BTC_DIR, "BTCUSDT_M15.parquet"))
    h8 = resample_ohlcv(h1, "H8")
    m15_slice = m15.iloc[6000:6300]
    return ({TimeFrame.D1: d1, TimeFrame.H4: h4, TimeFrame.H8: h8,
             TimeFrame.M15: m15_slice}, m15_slice)


def test_lookahead_trades_entry_after_first_bar(_btc_dataset):
    """Test bosluk #3: trade UREDIGINDE entry_ts ilk M15 barindan sonra."""
    data, m15 = _btc_dataset
    cfg = SMCConfig()
    res = run(data, cfg, initial_equity=10_000.0, m15_lookback=140)
    # Trade uretilmis olmali (BTC fixture).
    assert len(res.trades) >= 1, \
        "BTC fixture trade uretmiyor — gap #3 fixture/anti-vacuous"
    first_ts = m15.index[0]
    for t in res.trades:
        # next_open fill: entry t+1 olmali, t olamaz.
        assert pd.Timestamp(t.entry_ts) > first_ts, \
            f"Entry first bar veya oncesinde: {t.entry_ts}"


def test_lookahead_trade_entry_strictly_after_setup_bar(_btc_dataset):
    """Test bosluk #3: trade fixture'iyla, entry_ts >= ikinci M15 bari
    olmali (next_open fill = t+1)."""
    data, m15 = _btc_dataset
    cfg = SMCConfig()
    res = run(data, cfg, initial_equity=10_000.0, m15_lookback=140)
    assert len(res.trades) >= 1
    second_ts = m15.index[1]
    for t in res.trades:
        # next_open: en erken 2. bar acilisinda fill.
        assert pd.Timestamp(t.entry_ts) >= second_ts


@pytest.fixture(scope="module")
def _btc_m15_full():
    from data.fetch import load_parquet
    return load_parquet(os.path.join(_BTC_DIR, "BTCUSDT_M15.parquet"))


def test_lookahead_short_vs_long_window_identical_prefix(_btc_dataset, _btc_m15_full):
    """Test bosluk #3: ayni veri kisa+uzun pencere, ilk pencere ozdes
    sonuc — gelecek barlar sizmaz."""
    short = _btc_m15_full.iloc[6000:6150]
    longer = _btc_m15_full.iloc[6000:6200]
    data_short = dict(_btc_dataset[0]); data_short[TimeFrame.M15] = short
    data_long = dict(_btc_dataset[0]); data_long[TimeFrame.M15] = longer
    cfg = SMCConfig()
    r_s = run(data_short, cfg, initial_equity=10_000.0, m15_lookback=60)
    r_l = run(data_long, cfg, initial_equity=10_000.0, m15_lookback=60)
    pd.testing.assert_series_equal(
        r_s.equity_curve, r_l.equity_curve.iloc[:len(short)]
    )
    assert len(r_s.trades) <= len(r_l.trades)
    for ts, tl in zip(r_s.trades, r_l.trades):
        assert ts.entry == tl.entry
        assert ts.exit_price == tl.exit_price
