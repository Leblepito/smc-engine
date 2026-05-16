"""R2a — Test bosluk #2: walk_forward icerigi (test_trades >= 1) ve overfit
sinyali.

Trade-uretebilen fixture icin gercek BTC verisini kullaniyoruz (sentetik
veride harness uzun stretch'lerde 0 trade uretebiliyor — KR-1 sonrasi bile).
"""
from __future__ import annotations
import os
import pandas as pd
import pytest
from smc_engine.config import SMCConfig
from smc_engine.types import TimeFrame
from backtest.walk_forward import walk_forward


_BTC_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "btc")


def _btc_available() -> bool:
    return all(
        os.path.exists(os.path.join(_BTC_DIR, f"BTCUSDT_{tf}.parquet"))
        for tf in ("D1", "H4", "H1", "M15")
    )


pytestmark = pytest.mark.skipif(
    not _btc_available(),
    reason="data/btc/*.parquet yok — examples/run_btc.py ile uret",
)


def _load_dataset(m15_window, offset=6000):
    """HTF (D1/H4/H8) + sinirli M15 penceresi."""
    from data.fetch import load_parquet
    from data.resample import resample_ohlcv
    d1 = load_parquet(os.path.join(_BTC_DIR, "BTCUSDT_D1.parquet"))
    h4 = load_parquet(os.path.join(_BTC_DIR, "BTCUSDT_H4.parquet"))
    h1 = load_parquet(os.path.join(_BTC_DIR, "BTCUSDT_H1.parquet"))
    m15 = load_parquet(os.path.join(_BTC_DIR, "BTCUSDT_M15.parquet"))
    h8 = resample_ohlcv(h1, "H8")
    m15_slice = m15.iloc[offset:offset + m15_window]
    return {
        TimeFrame.D1: d1, TimeFrame.H4: h4, TimeFrame.H8: h8,
        TimeFrame.M15: m15_slice,
    }


@pytest.fixture(scope="module")
def _btc_walkforward_windows():
    """Tek pahalı walk_forward calismasi — birden cok test paylasir."""
    cfg = SMCConfig()
    ds = _load_dataset(m15_window=360, offset=6000)
    return walk_forward(ds, cfg, train_bars=120, test_bars=60,
                        step_bars=60, m15_lookback=90)


def test_walk_forward_window_dict_has_test_trades_field(_btc_walkforward_windows):
    """Test bosluk #2: her pencere test_trades alanini int ile dolu icermeli."""
    for w in _btc_walkforward_windows:
        assert "test_trades" in w
        assert isinstance(w["test_trades"], int)
        assert w["test_trades"] >= 0


def test_walk_forward_total_trades_at_least_one(_btc_walkforward_windows):
    """Test bosluk #2: trade-uretebilen fixture (BTC) -> en az 1 toplam trade.
    
    Bos pencereler "vacuous gate" — yeni assertion onler.
    """
    total = sum(w["test_trades"] + w["train_trades"]
                for w in _btc_walkforward_windows)
    assert total >= 1, f"Walk-forward hicbir trade uretmedi: {total}"


def test_walk_forward_overfit_signal_fields(_btc_walkforward_windows):
    """Test bosluk #2: train ve test metrics raporlanir (overfit tespiti
    icin gerekli ham bilgi)."""
    for w in _btc_walkforward_windows:
        assert "sharpe" in w["train_metrics"]
        assert "sharpe" in w["test_metrics"]
        assert isinstance(w["train_metrics"]["sharpe"], float)
        assert isinstance(w["test_metrics"]["sharpe"], float)
