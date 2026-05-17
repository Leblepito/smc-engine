"""AuditLog — trades-YYYYMMDD.jsonl daily rotation (Spec §4.5).

Every execution event (ORDER_PLACED, ORDER_FILLED, TP_HIT, SL_HIT,
KILL_SWITCH_TRIGGERED, RECONCILE_DRIFT, ...) lands here. Append-only,
replay-friendly JSONL. Common fields injected by emit(): ts, event,
phase, engine_sha, testnet.

Symmetric with signal_logger.py from sub-proje #2 — same JSONL pattern
so analyze_combined.py can join across both files (signal_at_bar key).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utcnow() -> datetime:
    """Patch-able for tests."""
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


class AuditLog:
    """Daily rotating JSONL writer for execution events."""

    def __init__(
        self,
        log_dir: str,
        engine_sha: str,
        testnet: bool,
        phase: str = "5A",
        stdout=None,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.engine_sha = engine_sha
        self.testnet = testnet
        self.phase = phase
        self._stdout = stdout if stdout is not None else sys.stdout

    def _current_path(self, now: datetime) -> Path:
        return self.log_dir / f"trades-{now.strftime('%Y%m%d')}.jsonl"

    def emit(self, event: str, **fields: Any) -> None:
        """Write one JSONL event line. Common fields auto-injected."""
        now = _utcnow()
        envelope = {
            "ts": now.replace(microsecond=0).isoformat(),
            "event": event,
            "phase": self.phase,
            "engine_sha": self.engine_sha,
            "testnet": self.testnet,
            **fields,
        }
        line = json.dumps(envelope, ensure_ascii=False, sort_keys=True, default=str)

        path = self._current_path(now)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

        # stdout — short summary for operator
        try:
            self._stdout.write(line + "\n")
            self._stdout.flush()
        except Exception:
            pass

    def close(self) -> None:  # symmetry with signal_logger
        return
