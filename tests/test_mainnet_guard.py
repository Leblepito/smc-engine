"""MainnetGuard testleri — 3 katmanlÄ± mainnet kapÄ±sÄ± (Spec §4.4, §11).

KRİTİK — 5A testnet smoke ÖNCESİ tüm testler %100 yeÅil olmalı.

3 katman:
  1) env: SMC_ALLOW_LIVE=1
  2) config: execution_live_enabled=True
  3) startup: 5sn delay + WARNING log

Tüm 3 katman geçmezse TESTNET zorlanır.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from smc_engine.config import SMCConfig
from smc_engine.execution.mainnet_guard import MainnetGuard, MainnetMode


# ============================================================
# check() 4 kombinasyon
# ============================================================


def test_env_no_config_no_returns_testnet(monkeypatch, caplog):
    monkeypatch.delenv("SMC_ALLOW_LIVE", raising=False)
    cfg = SMCConfig()
    cfg.execution_live_enabled = False
    with patch("smc_engine.execution.mainnet_guard._SLEEP", lambda s: None):
        mode = MainnetGuard.check(cfg)
    assert mode is MainnetMode.TESTNET


def test_env_no_config_yes_returns_testnet(monkeypatch):
    monkeypatch.delenv("SMC_ALLOW_LIVE", raising=False)
    cfg = SMCConfig()
    cfg.execution_live_enabled = True
    with patch("smc_engine.execution.mainnet_guard._SLEEP", lambda s: None):
        mode = MainnetGuard.check(cfg)
    assert mode is MainnetMode.TESTNET


def test_env_yes_config_no_returns_testnet_with_warning(monkeypatch, caplog):
    monkeypatch.setenv("SMC_ALLOW_LIVE", "1")
    cfg = SMCConfig()
    cfg.execution_live_enabled = False
    with patch("smc_engine.execution.mainnet_guard._SLEEP", lambda s: None), \
         caplog.at_level(logging.WARNING):
        mode = MainnetGuard.check(cfg)
    assert mode is MainnetMode.TESTNET
    assert any("config" in r.message.lower() for r in caplog.records)


def test_env_yes_config_yes_returns_mainnet_with_delay(monkeypatch, caplog):
    monkeypatch.setenv("SMC_ALLOW_LIVE", "1")
    cfg = SMCConfig()
    cfg.execution_live_enabled = True
    sleep_calls: list[float] = []

    def fake_sleep(s):
        sleep_calls.append(s)

    with patch("smc_engine.execution.mainnet_guard._SLEEP", fake_sleep), \
         caplog.at_level(logging.CRITICAL):
        mode = MainnetGuard.check(cfg)
    assert mode is MainnetMode.MAINNET
    # 5sn delay
    assert any(s >= 5.0 for s in sleep_calls), f"expected 5sn delay, got {sleep_calls}"
    # CRITICAL warning logged
    assert any("MAINNET" in r.message for r in caplog.records)


# ============================================================
# is_approved() — convenience
# ============================================================


def test_is_approved_env_no_config_no_false(monkeypatch):
    monkeypatch.delenv("SMC_ALLOW_LIVE", raising=False)
    cfg = SMCConfig()
    cfg.execution_live_enabled = False
    with patch("smc_engine.execution.mainnet_guard._SLEEP", lambda s: None):
        assert MainnetGuard.is_approved(cfg) is False


def test_is_approved_env_yes_config_yes_true(monkeypatch):
    monkeypatch.setenv("SMC_ALLOW_LIVE", "1")
    cfg = SMCConfig()
    cfg.execution_live_enabled = True
    with patch("smc_engine.execution.mainnet_guard._SLEEP", lambda s: None):
        assert MainnetGuard.is_approved(cfg) is True


# ============================================================
# Env var edge cases
# ============================================================


def test_env_value_other_than_1_is_no(monkeypatch):
    """SMC_ALLOW_LIVE=0, =true, =yes, empty → all reject."""
    cfg = SMCConfig()
    cfg.execution_live_enabled = True

    for val in ("0", "true", "yes", "", "false"):
        monkeypatch.setenv("SMC_ALLOW_LIVE", val)
        with patch("smc_engine.execution.mainnet_guard._SLEEP", lambda s: None):
            mode = MainnetGuard.check(cfg)
        assert mode is MainnetMode.TESTNET, f"value={val!r} should reject"


def test_5sec_delay_actually_5_seconds(monkeypatch):
    """Plan says minimum 5sn; verify exact value passed to sleep."""
    monkeypatch.setenv("SMC_ALLOW_LIVE", "1")
    cfg = SMCConfig()
    cfg.execution_live_enabled = True
    seen: list[float] = []
    with patch("smc_engine.execution.mainnet_guard._SLEEP",
               lambda s: seen.append(s)):
        MainnetGuard.check(cfg)
    assert seen == [5.0]
