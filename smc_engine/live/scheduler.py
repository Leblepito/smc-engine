"""``LiveScheduler`` — APScheduler ``BackgroundScheduler`` wrap (Spec §3, §6).

M15 kapanışında + ``buffer_seconds`` saniye sonra callback'i tetikler. Tek
sorumluluk: zamanlama. Pipeline'ın kendisi ``LiveRunner``'da.
"""

from __future__ import annotations

from typing import Callable, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger


class LiveScheduler:
    """M15 kapanış + buffer cron — tek job."""

    def __init__(self, buffer_seconds: int = 5) -> None:
        self.buffer_seconds = buffer_seconds
        self.scheduler: BackgroundScheduler = BackgroundScheduler()
        self._callback: Optional[Callable[[], None]] = None
        self._job = None

    def build_trigger(self) -> CronTrigger:
        """M15 kapanışları (dakika 0,15,30,45) + buffer saniyesi."""
        return CronTrigger(minute="0,15,30,45", second=str(self.buffer_seconds))

    def start(self, callback: Callable[[], None]) -> None:
        if self.is_running():
            return
        self._callback = callback
        self._job = self.scheduler.add_job(
            callback,
            trigger=self.build_trigger(),
            id="live_runner_tick",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.start()

    def stop(self) -> None:
        if self.is_running():
            self.scheduler.shutdown(wait=False)

    def is_running(self) -> bool:
        return getattr(self.scheduler, "running", False)

    def trigger_now(self) -> None:
        """Test/smoke desteği: scheduler tick'i beklemeden callback'i hemen çalıştır."""
        if self._callback is None:
            return
        self._callback()
