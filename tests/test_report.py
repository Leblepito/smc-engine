"""Faz 5.5 — backtest/report.py testleri (light)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from smc_engine.types import BacktestResult, Direction, Trade
from backtest.report import (
    ratchet_metric_line,
    summary,
    walk_forward_table,
    write_trades_csv,
)


def _result():
    t0 = datetime(2026, 1, 1)
    trades = [
        Trade(direction=Direction.LONG, entry=100.0, entry_ts=t0,
              exit_price=102.0, exit_ts=t0 + timedelta(hours=4),
              exit_reason="TP1", pnl=200.0, r_multiple=2.0, size=1.0,
              confluence_score=0.8, confluence_factor_count=3),
        Trade(direction=Direction.LONG, entry=100.0, entry_ts=t0,
              exit_price=98.0, exit_ts=t0 + timedelta(hours=2),
              exit_reason="SL", pnl=-100.0, r_multiple=-1.0, size=1.0,
              confluence_score=0.5, confluence_factor_count=2),
    ]
    ec = pd.Series(
        [10_000.0, 10_200.0, 10_100.0],
        index=pd.date_range("2026-01-01", periods=3, freq="15min"),
    )
    metrics = {
        "trade_count": 2, "low_trade_count_warning": True, "win_rate": 0.5,
        "profit_factor": 2.0, "expectancy": 0.5, "sharpe": 1.2, "sortino": 1.5,
        "max_drawdown_pct": 0.05, "max_drawdown_duration": 1, "total_pnl": 100.0,
        "r_multiple_distribution": {"mean": 0.5, "min": -1.0, "max": 2.0,
                                    "std": 2.1},
        "avg_holding_hours": 3.0,
        "confluence_buckets": {"[0.40,0.55)": {"count": 1, "avg_r": -1.0,
                                               "total_pnl": -100.0,
                                               "win_rate": 0.0}},
    }
    return BacktestResult(trades=trades, equity_curve=ec, metrics=metrics)


def test_summary_is_string_with_key_fields():
    s = summary(_result())
    assert "Backtest Raporu" in s
    assert "Sharpe" in s
    assert "RATCHET_METRIC" in s


def test_ratchet_metric_line_grepable():
    line = ratchet_metric_line(_result())
    assert line.startswith("RATCHET_METRIC ")
    assert "sharpe=" in line
    assert "trades=2" in line
    assert "expectancy=" in line
    # tek satir
    assert "\n" not in line


def test_write_trades_csv(tmp_path):
    path = write_trades_csv(_result(), tmp_path / "trades.csv")
    content = (tmp_path / "trades.csv").read_text()
    assert "direction,entry,entry_ts" in content
    lines = content.strip().split("\n")
    assert len(lines) == 3  # header + 2 trades


def test_walk_forward_table_hook():
    s = walk_forward_table()
    assert "Walk-Forward" in s
    assert "HOOK" in s
