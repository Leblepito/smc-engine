"""R2a — Test bosluk #5: limit_retest + maliyetli config determinizmi."""
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
    m15_slice = m15.iloc[6000:6250]
    return {TimeFrame.D1: d1, TimeFrame.H4: h4, TimeFrame.H8: h8,
            TimeFrame.M15: m15_slice}


def test_determinism_limit_retest_with_costs(_btc_dataset):
    """Test bosluk #5: limit_retest fill modeli + maliyetli config -> 
    ayni input iki cagri ozdes sonuc."""
    cfg = SMCConfig(
        fill_model="limit_retest", limit_retest_bars=5,
        spread=0.10, slippage_pct=0.001, commission_pct=0.0004,
    )
    r1 = run(_btc_dataset, cfg, initial_equity=10_000.0, m15_lookback=140)
    r2 = run(_btc_dataset, cfg, initial_equity=10_000.0, m15_lookback=140)
    # Equity curve birebir
    pd.testing.assert_series_equal(r1.equity_curve, r2.equity_curve)
    # Trade'ler ozdes
    assert len(r1.trades) == len(r2.trades)
    for t1, t2 in zip(r1.trades, r2.trades):
        assert t1.entry == t2.entry
        assert t1.entry_ts == t2.entry_ts
        assert t1.exit_price == t2.exit_price
        assert t1.exit_ts == t2.exit_ts
        assert t1.pnl == t2.pnl
        assert t1.r_multiple == t2.r_multiple
    # Metrikler ozdes
    assert r1.metrics["sharpe"] == r2.metrics["sharpe"]
    assert r1.metrics["trade_count"] == r2.metrics["trade_count"]


def test_determinism_next_open_zero_vs_costly_differ(_btc_dataset):
    """Test bosluk #5: next_open ile maliyet 0 vs maliyetli SONUC FARKLI
    olmali (cost gercek etki yapiyor; karsi-saniye olarak sessiz cost-vuru
    ortaya cikar)."""
    cfg_zero = SMCConfig(fill_model="next_open",
                         spread=0.0, slippage_pct=0.0, commission_pct=0.0)
    cfg_cost = SMCConfig(fill_model="next_open",
                         spread=0.10, slippage_pct=0.001, commission_pct=0.0004)
    r0 = run(_btc_dataset, cfg_zero, initial_equity=10_000.0, m15_lookback=140)
    rc = run(_btc_dataset, cfg_cost, initial_equity=10_000.0, m15_lookback=140)
    if len(r0.trades) >= 1 and len(rc.trades) >= 1:
        # En az bir trade icin PnL farkli olmali (maliyet kesintisi).
        pnl0 = sum(t.pnl for t in r0.trades)
        pnlc = sum(t.pnl for t in rc.trades)
        assert pnl0 != pnlc, f"Maliyetli ile maliyetsiz ayni PnL: {pnl0}"
