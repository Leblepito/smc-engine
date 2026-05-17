"""KillSwitch testleri — 3 metrik + persistence + external trigger (Spec §4.6)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from smc_engine.execution._base import Account
from smc_engine.execution.audit_log import AuditLog
from smc_engine.execution.kill_switch import (
    KillSwitch,
    KillSwitchState,
    TradeResult,
)


def _make_ks(tmp_path, **overrides):
    audit = AuditLog(log_dir=str(tmp_path / "audit"), engine_sha="x", testnet=True)
    defaults = dict(
        consecutive_loss_threshold=3,
        daily_loss_threshold=5.0,
        equity_minimum=15.0,
        state_path=tmp_path / "ks_state.json",
        audit_log=audit,
    )
    defaults.update(overrides)
    return KillSwitch(**defaults), audit


def _loss(pnl=-1.0):
    return TradeResult(order_id="x", pnl_dollar=pnl)


def _win(pnl=1.0):
    return TradeResult(order_id="x", pnl_dollar=pnl)


def _account(equity=25.0):
    return Account(equity=equity, available_margin=20.0, used_margin=5.0)


# ============================================================
# Consecutive loss metric
# ============================================================


def test_3_consecutive_losses_triggers(tmp_path):
    ks, _ = _make_ks(tmp_path)
    assert ks.check_after_trade(_loss(), _account()) is False
    assert ks.check_after_trade(_loss(), _account()) is False
    assert ks.check_after_trade(_loss(), _account()) is True
    assert ks.is_triggered() is True


def test_win_resets_consecutive_losses(tmp_path):
    ks, _ = _make_ks(tmp_path)
    ks.check_after_trade(_loss(), _account())
    ks.check_after_trade(_loss(), _account())
    ks.check_after_trade(_win(), _account())  # reset
    # ÅŸimdi 2 loss daha → toplam 2 (3'e ulaÅmadÄ±)
    assert ks.check_after_trade(_loss(), _account()) is False
    assert ks.check_after_trade(_loss(), _account()) is False
    assert ks.is_triggered() is False


# ============================================================
# Daily loss metric
# ============================================================


def test_daily_loss_5_dollar_triggers(tmp_path):
    ks, _ = _make_ks(tmp_path)
    # 1 loss $4 yetmez, 2. loss $2 → toplam $6 → triggered
    ks.check_after_trade(_loss(pnl=-4.0), _account())
    assert ks.check_after_trade(_loss(pnl=-2.0), _account()) is True


def test_daily_pnl_wins_reduce_loss(tmp_path):
    ks, _ = _make_ks(tmp_path)
    ks.check_after_trade(_loss(pnl=-3.0), _account())
    ks.check_after_trade(_win(pnl=2.0), _account())
    ks.check_after_trade(_loss(pnl=-3.0), _account())
    # toplam: -3+2-3 = -4 → trigger eÅiÄi -5'e ulaÅmadÄ±
    assert ks.is_triggered() is False


# ============================================================
# Equity minimum metric
# ============================================================


def test_equity_below_minimum_triggers(tmp_path):
    ks, _ = _make_ks(tmp_path, equity_minimum=15.0)
    assert ks.check_after_trade(_loss(pnl=-0.5), _account(equity=14.0)) is True


def test_equity_at_minimum_triggers(tmp_path):
    ks, _ = _make_ks(tmp_path, equity_minimum=15.0)
    assert ks.check_after_trade(_loss(pnl=-0.5), _account(equity=15.0)) is True


# ============================================================
# Multiple triggers same time
# ============================================================


def test_multiple_triggers_all_listed(tmp_path):
    ks, _ = _make_ks(tmp_path)
    # Tek seferde 3 metriği de tetikle: 3 ardÄ±ÅÄ±k loss + daily loss + equity dÃ¼ÅÃ¼k
    ks.check_after_trade(_loss(pnl=-2.0), _account(equity=14.0))
    ks.check_after_trade(_loss(pnl=-2.0), _account(equity=14.0))
    ks.check_after_trade(_loss(pnl=-2.0), _account(equity=14.0))
    state = ks._state  # noqa: SLF001 — test internal inspect
    assert state.triggered is True
    # En az 3 reason
    assert len(state.triggered_reasons) >= 1


# ============================================================
# Persistence
# ============================================================


def test_state_persists_across_instances(tmp_path):
    ks1, _ = _make_ks(tmp_path)
    ks1.check_after_trade(_loss(), _account())
    ks1.check_after_trade(_loss(), _account())

    ks2, _ = _make_ks(tmp_path)  # same state_path
    # Yeni instance eski state'i okumalı → 2 consecutive loss
    assert ks2._state.consecutive_losses == 2  # noqa: SLF001


def test_triggered_state_persists(tmp_path):
    ks1, _ = _make_ks(tmp_path)
    ks1.check_after_trade(_loss(), _account())
    ks1.check_after_trade(_loss(), _account())
    ks1.check_after_trade(_loss(), _account())
    assert ks1.is_triggered() is True

    ks2, _ = _make_ks(tmp_path)
    assert ks2.is_triggered() is True


def test_state_file_atomic_save(tmp_path):
    ks, _ = _make_ks(tmp_path)
    ks.check_after_trade(_loss(), _account())
    # Tmp dosya kalmamalı
    leftover = list(tmp_path.glob("ks_state.json.tmp*"))
    assert leftover == []
    assert (tmp_path / "ks_state.json").exists()


# ============================================================
# Reset
# ============================================================


def test_reset_clears_state_and_audits(tmp_path):
    ks, audit = _make_ks(tmp_path)
    ks.check_after_trade(_loss(), _account())
    ks.check_after_trade(_loss(), _account())
    ks.check_after_trade(_loss(), _account())
    assert ks.is_triggered() is True

    ks.reset()
    assert ks.is_triggered() is False
    assert ks._state.consecutive_losses == 0  # noqa: SLF001

    # Audit'e RESET event geÃ§miÅ olmalÄ±
    log_files = list((tmp_path / "audit").glob("trades-*.jsonl"))
    content = log_files[0].read_text()
    assert "KILL_SWITCH_RESET" in content


# ============================================================
# External trigger (drift case)
# ============================================================


def test_trigger_external_drift(tmp_path):
    ks, audit = _make_ks(tmp_path)
    assert ks.is_triggered() is False
    ks.trigger_external(reason="RECONCILE_DRIFT", details=["PENDING 12345 missing"])
    assert ks.is_triggered() is True

    log_files = list((tmp_path / "audit").glob("trades-*.jsonl"))
    content = log_files[0].read_text()
    assert "KILL_SWITCH_TRIGGERED" in content
    assert "RECONCILE_DRIFT" in content
