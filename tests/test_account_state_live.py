"""Live AccountState builder testleri — log-only mod statik snapshot (Spec §8)."""

from __future__ import annotations

from smc_engine.config import SMCConfig
from smc_engine.live.account_state import build_static_account_state
from smc_engine.types import AccountState


def test_build_static_account_state_uses_config_equity():
    cfg = SMCConfig()
    cfg.live_account_equity = 25_000.0
    state = build_static_account_state(cfg)
    assert isinstance(state, AccountState)
    assert state.equity == 25_000.0


def test_build_static_account_state_log_only_invariants():
    """Log-only mod: open_position False, ardışık zarar 0, DD 0.0."""
    cfg = SMCConfig()
    state = build_static_account_state(cfg)
    assert state.open_position is False
    assert state.consecutive_losses == 0
    assert state.max_drawdown_pct == 0.0
    assert state.recent_results is None
