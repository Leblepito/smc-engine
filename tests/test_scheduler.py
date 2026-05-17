"""LiveScheduler testleri — APScheduler wrap, M15:05 cron tetikleme (Spec §3, §6)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from smc_engine.live.scheduler import LiveScheduler


def test_scheduler_cron_runs_at_m15_close_plus_buffer():
    """``second`` = buffer; ``minute`` = '0,15,30,45' M15 kapanışları."""
    sched = LiveScheduler(buffer_seconds=5)
    trigger = sched.build_trigger()
    # APScheduler CronTrigger.fields → name/expression iç gösterimi
    fields = {f.name: str(f) for f in trigger.fields}
    # M15 kapanış cron'u: minute 0,15,30,45 — second buffer
    assert "0,15,30,45" in fields["minute"]
    assert fields["second"] == "5"


def test_scheduler_buffer_default_is_5_seconds():
    sched = LiveScheduler()
    trigger = sched.build_trigger()
    fields = {f.name: str(f) for f in trigger.fields}
    assert fields["second"] == "5"


def test_scheduler_start_attaches_callback_and_starts():
    sched = LiveScheduler(buffer_seconds=5)
    callback = MagicMock()
    sched.start(callback)
    try:
        # APScheduler runs in background; just verify start state
        assert sched.is_running()
        # Job kaydı yapılmış olmalı
        jobs = sched.scheduler.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].func is callback
    finally:
        sched.stop()


def test_scheduler_stop_idempotent():
    sched = LiveScheduler(buffer_seconds=5)
    # Hiç start edilmemişken stop fırlatmamalı
    sched.stop()  # no-op
    sched.start(lambda: None)
    sched.stop()
    # Tekrar stop hatasız
    sched.stop()


def test_scheduler_manual_trigger_executes_callback_immediately():
    """Test desteği: scheduler tick'i beklemeden callback'i tetikle (smoke için)."""
    sched = LiveScheduler(buffer_seconds=5)
    callback = MagicMock()
    sched.start(callback)
    try:
        sched.trigger_now()
        # Eş zamanlı çağrı (testte) — callback bir kez çağrılmış olmalı
        callback.assert_called_once()
    finally:
        sched.stop()
