"""LiveRunner testleri — FakeAdapter ile end-to-end mock (Spec §3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from smc_engine.config import SMCConfig
from smc_engine.integrations._base import SymbolMeta
from smc_engine.live.runner import LiveRunner
from smc_engine.types import TimeFrame


# ---------------- FakeAdapter ----------------


def _synth_ohlcv(tf_minutes: int, bars: int, end_ts: datetime) -> pd.DataFrame:
    """Sentetik düz OHLCV — analyze çağrılabilir olsun yeterli."""
    idx = pd.date_range(end=end_ts, periods=bars, freq=f"{tf_minutes}min")
    return pd.DataFrame(
        {
            "open": [100.0] * bars,
            "high": [110.0] * bars,
            "low": [90.0] * bars,
            "close": [105.0] * bars,
            "volume": [1.0] * bars,
        },
        index=idx,
    )


class FakeAdapter:
    """Test adapter — fetch_ohlcv için sentetik veri, diğer fetch'ler stub."""

    def __init__(self, end_ts: datetime, fail_on_call: bool = False) -> None:
        self.end_ts = end_ts
        self.fail_on_call = fail_on_call
        self.fetch_calls: list[tuple[str, TimeFrame, int]] = []

    def fetch_ohlcv(self, symbol, timeframe, lookback_bars):
        self.fetch_calls.append((symbol, timeframe, lookback_bars))
        if self.fail_on_call:
            raise RuntimeError("simulated adapter failure")
        tf_minutes = {
            TimeFrame.M15: 15, TimeFrame.H1: 60, TimeFrame.H4: 240,
            TimeFrame.H8: 480, TimeFrame.D1: 1440,
        }[timeframe]
        return _synth_ohlcv(tf_minutes, lookback_bars, self.end_ts)

    def fetch_funding_rate(self, symbol): return 0.0
    def fetch_open_interest(self, symbol): return 0.0
    def fetch_symbol_info(self, symbol):
        return SymbolMeta(symbol, 0.1, 0.001, 0.001, 1, 3)
    def close(self): pass


# ---------------- runner pipeline tetikleme ----------------


def test_runner_tick_calls_orchestrator_setup_builder_risk_guard(monkeypatch):
    """Tick → orchestrator.analyze → setup_builder.build → risk_guard.validate."""
    end_ts = datetime(2026, 5, 16, 14, 45)
    adapter = FakeAdapter(end_ts=end_ts)
    cfg = SMCConfig()

    mock_analyze = MagicMock(return_value="picture_obj")
    mock_build = MagicMock(return_value=None)  # None → setup_builder bir setup üretmedi
    monkeypatch.setattr("smc_engine.live.runner.orchestrator_analyze", mock_analyze)
    monkeypatch.setattr("smc_engine.live.runner.build_setup", mock_build)

    logger = MagicMock()
    runner = LiveRunner(adapter=adapter, config=cfg, signal_logger=logger)
    runner.run_once("BTCUSDT", now=end_ts + timedelta(seconds=5))

    mock_analyze.assert_called_once()
    mock_build.assert_called_once_with("picture_obj", cfg)
    # setup None → risk_guard çağrılmaz; logger da emit yapmaz
    logger.emit.assert_not_called()


def test_runner_emits_validated_setup_when_pipeline_returns_one(monkeypatch):
    end_ts = datetime(2026, 5, 16, 14, 45)
    adapter = FakeAdapter(end_ts=end_ts)
    cfg = SMCConfig()

    fake_setup = MagicMock(name="setup")
    fake_validated = MagicMock(name="validated")
    monkeypatch.setattr("smc_engine.live.runner.orchestrator_analyze", MagicMock(return_value="picture"))
    monkeypatch.setattr("smc_engine.live.runner.build_setup", MagicMock(return_value=fake_setup))
    monkeypatch.setattr("smc_engine.live.runner.risk_guard_validate", MagicMock(return_value=fake_validated))

    logger = MagicMock()
    runner = LiveRunner(adapter=adapter, config=cfg, signal_logger=logger)
    runner.run_once("BTCUSDT", now=end_ts + timedelta(seconds=5))

    logger.emit.assert_called_once()
    payload = logger.emit.call_args[0][0]
    assert payload is fake_validated


def test_runner_htf_cache_reused_across_ticks_for_same_symbol(monkeypatch):
    """HTF cache runner ömrü boyu RAM'de — aynı sembol için aynı dict instance."""
    end_ts = datetime(2026, 5, 16, 14, 45)
    adapter = FakeAdapter(end_ts=end_ts)
    cfg = SMCConfig()

    mock_analyze = MagicMock(return_value="picture")
    monkeypatch.setattr("smc_engine.live.runner.orchestrator_analyze", mock_analyze)
    monkeypatch.setattr("smc_engine.live.runner.build_setup", MagicMock(return_value=None))

    logger = MagicMock()
    runner = LiveRunner(adapter=adapter, config=cfg, signal_logger=logger)
    runner.run_once("BTCUSDT", now=end_ts + timedelta(seconds=5))
    runner.run_once("BTCUSDT", now=end_ts + timedelta(minutes=15, seconds=5))

    cache_args = [call.kwargs.get("cache") for call in mock_analyze.call_args_list]
    assert cache_args[0] is cache_args[1]
    assert isinstance(cache_args[0], dict)


def test_runner_htf_cache_isolated_across_symbols(monkeypatch):
    """Cross-symbol kontaminasyon önleme: BTC ve ETH ayrı cache dict'leri.

    Orchestrator cache key = (tf, ts) — sembolden bağımsız. Tek dict
    paylaşılırsa BTC'nin D1 snapshot'ı ETH analizine sızar.
    """
    end_ts = datetime(2026, 5, 16, 14, 45)
    adapter = FakeAdapter(end_ts=end_ts)
    cfg = SMCConfig()

    mock_analyze = MagicMock(return_value="picture")
    monkeypatch.setattr("smc_engine.live.runner.orchestrator_analyze", mock_analyze)
    monkeypatch.setattr("smc_engine.live.runner.build_setup", MagicMock(return_value=None))

    runner = LiveRunner(adapter=adapter, config=cfg, signal_logger=MagicMock())
    runner.run_once("BTCUSDT", now=end_ts + timedelta(seconds=5))
    runner.run_once("ETHUSDT", now=end_ts + timedelta(seconds=5))

    cache_args = [call.kwargs.get("cache") for call in mock_analyze.call_args_list]
    assert cache_args[0] is not cache_args[1]


def test_runner_adapter_error_logs_and_does_not_crash(monkeypatch, caplog):
    """Adapter hatası → log error, sonraki tick'i bekle (crash etme)."""
    end_ts = datetime(2026, 5, 16, 14, 45)
    adapter = FakeAdapter(end_ts=end_ts, fail_on_call=True)
    cfg = SMCConfig()
    logger = MagicMock()
    runner = LiveRunner(adapter=adapter, config=cfg, signal_logger=logger)
    # Crash etmemeli
    runner.run_once("BTCUSDT", now=end_ts + timedelta(seconds=5))
    logger.emit.assert_not_called()


def test_runner_multi_symbol_pipelines_independent(monkeypatch):
    """Sembol başına ayrı analyze çağrısı + ayrı HTF cache."""
    end_ts = datetime(2026, 5, 16, 14, 45)
    adapter = FakeAdapter(end_ts=end_ts)
    cfg = SMCConfig()

    mock_analyze = MagicMock(return_value="picture")
    monkeypatch.setattr("smc_engine.live.runner.orchestrator_analyze", mock_analyze)
    monkeypatch.setattr("smc_engine.live.runner.build_setup", MagicMock(return_value=None))

    logger = MagicMock()
    runner = LiveRunner(adapter=adapter, config=cfg, signal_logger=logger)
    runner.run_tick(["BTCUSDT", "ETHUSDT"], now=end_ts + timedelta(seconds=5))
    # her sembol için analyze çağrılmış olmalı
    assert mock_analyze.call_count == 2


def test_runner_no_execution_hook_when_order_manager_none(monkeypatch):
    """order_manager=None default → backward compat sub-proje #2 behaviour."""
    end_ts = datetime(2026, 5, 16, 14, 45)
    adapter = FakeAdapter(end_ts=end_ts)
    cfg = SMCConfig()
    fake_setup = MagicMock(name="setup")
    fake_validated = MagicMock(name="validated", spec=[])  # not a ValidatedSetup
    monkeypatch.setattr("smc_engine.live.runner.orchestrator_analyze", MagicMock(return_value="picture"))
    monkeypatch.setattr("smc_engine.live.runner.build_setup", MagicMock(return_value=fake_setup))
    monkeypatch.setattr("smc_engine.live.runner.risk_guard_validate", MagicMock(return_value=fake_validated))

    logger_mock = MagicMock()
    runner = LiveRunner(adapter=adapter, config=cfg, signal_logger=logger_mock)
    runner.run_once("BTCUSDT", now=end_ts + timedelta(seconds=5))
    logger_mock.emit.assert_called_once()
    # order_manager None → emit only, no process_setup attempt
    assert runner.order_manager is None


def test_runner_execution_hook_called_when_validated_setup(monkeypatch):
    """order_manager set + result is ValidatedSetup → process_setup çağrılır."""
    from smc_engine.types import ValidatedSetup
    end_ts = datetime(2026, 5, 16, 14, 45)
    adapter = FakeAdapter(end_ts=end_ts)
    cfg = SMCConfig()
    # Real-ish ValidatedSetup (spec/isinstance check)
    fake_setup_obj = MagicMock(name="setup")
    fake_validated = MagicMock(spec=ValidatedSetup)
    fake_validated.setup = fake_setup_obj
    monkeypatch.setattr("smc_engine.live.runner.orchestrator_analyze", MagicMock(return_value="pic"))
    monkeypatch.setattr("smc_engine.live.runner.build_setup", MagicMock(return_value=fake_setup_obj))
    monkeypatch.setattr("smc_engine.live.runner.risk_guard_validate", MagicMock(return_value=fake_validated))

    logger_mock = MagicMock()
    om = MagicMock()
    runner = LiveRunner(adapter=adapter, config=cfg, signal_logger=logger_mock, order_manager=om)
    runner.run_once("BTCUSDT", now=end_ts + timedelta(seconds=5))

    logger_mock.emit.assert_called_once_with(fake_validated)
    om.process_setup.assert_called_once()
    call_kwargs = om.process_setup.call_args.kwargs
    assert call_kwargs["symbol"] == "BTCUSDT"
    assert "at_bar" in call_kwargs


def test_runner_execution_hook_skipped_for_rejection(monkeypatch):
    """Result is Rejection (not ValidatedSetup) → only signal_logger, no order_manager."""
    from smc_engine.types import Rejection
    end_ts = datetime(2026, 5, 16, 14, 45)
    adapter = FakeAdapter(end_ts=end_ts)
    cfg = SMCConfig()
    fake_rej = MagicMock(spec=Rejection)
    monkeypatch.setattr("smc_engine.live.runner.orchestrator_analyze", MagicMock(return_value="pic"))
    monkeypatch.setattr("smc_engine.live.runner.build_setup", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr("smc_engine.live.runner.risk_guard_validate", MagicMock(return_value=fake_rej))

    logger_mock = MagicMock()
    om = MagicMock()
    runner = LiveRunner(adapter=adapter, config=cfg, signal_logger=logger_mock, order_manager=om)
    runner.run_once("BTCUSDT", now=end_ts + timedelta(seconds=5))

    logger_mock.emit.assert_called_once_with(fake_rej)
    om.process_setup.assert_not_called()  # rejection → execution skipped


def test_runner_last_closed_m15_truncates_to_quarter_hour():
    """Spec §3 look-ahead: at_bar = en son kapanmış M15 bar'ın open_time'ı.

    Orchestrator ``df.index <= at_bar`` filtresi uygular; bar'lar open_time ile
    indekslenir. Forming bar'ın open_time'ı dahil edilirse look-ahead sızar.
    Bu yüzden at_bar = current_open_time - 15min (henüz kapanmamış bar
    open_time'ını DIŞLAR).
    """
    runner = LiveRunner(adapter=FakeAdapter(datetime(2026, 5, 16)), config=SMCConfig(), signal_logger=MagicMock())

    # 14:50:05 → şu anki bar [14:45,15:00) hâlâ açık → at_bar = 14:30
    at = runner._last_closed_m15(datetime(2026, 5, 16, 14, 50, 5))
    assert at == datetime(2026, 5, 16, 14, 30)

    # 14:45:00 → şu anki bar [14:45,15:00) yeni açıldı → at_bar = 14:30
    at2 = runner._last_closed_m15(datetime(2026, 5, 16, 14, 45, 0))
    assert at2 == datetime(2026, 5, 16, 14, 30)

    # 15:00:05 → şu anki bar [15:00,15:15) açık → at_bar = 14:45
    at3 = runner._last_closed_m15(datetime(2026, 5, 16, 15, 0, 5))
    assert at3 == datetime(2026, 5, 16, 14, 45)
