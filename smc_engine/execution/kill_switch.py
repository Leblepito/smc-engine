"""KillSwitch — 3 metrik, ilk tetikleyen kazanır (Spec §4.6).

Metrikler:
  1) consecutive_losses ≥ threshold (win → reset to 0)
  2) daily_pnl ≤ -threshold_dollar (gün bazında kümülatif PnL)
  3) account.equity ≤ equity_minimum

External trigger: ReconcileLoop drift bulunca trigger_external(reason=...)
çağrısı ile manuel reset şartı ile kilitler.

State persistence: atomic temp+rename → ks_state.json.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from smc_engine.execution._base import Account


@dataclass
class TradeResult:
    """One trade closed — feed to check_after_trade."""

    order_id: str
    pnl_dollar: float

    @property
    def is_loss(self) -> bool:
        return self.pnl_dollar < 0


@dataclass
class KillSwitchState:
    consecutive_losses: int = 0
    daily_pnl: float = 0.0
    triggered: bool = False
    triggered_at: Optional[str] = None
    triggered_reasons: list = field(default_factory=list)


_VERSION = 1


class KillSwitch:
    def __init__(
        self,
        consecutive_loss_threshold: int,
        daily_loss_threshold: float,
        equity_minimum: float,
        state_path: str | Path,
        audit_log,  # AuditLog (duck-typed)
    ) -> None:
        self.consecutive_loss_threshold = consecutive_loss_threshold
        self.daily_loss_threshold = daily_loss_threshold
        self.equity_minimum = equity_minimum
        self.state_path = Path(state_path)
        self.audit_log = audit_log
        self._state = self._load_state()

    # ---------------- state I/O ----------------

    def _load_state(self) -> KillSwitchState:
        if not self.state_path.exists():
            return KillSwitchState()
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        file_version = data.get("version", 0)
        if file_version != _VERSION:
            raise ValueError(
                f"KillSwitch state schema version mismatch: "
                f"file={file_version}, expected={_VERSION}. Migrate manually."
            )
        s = data.get("state", {})
        return KillSwitchState(
            consecutive_losses=int(s.get("consecutive_losses", 0)),
            daily_pnl=float(s.get("daily_pnl", 0.0)),
            triggered=bool(s.get("triggered", False)),
            triggered_at=s.get("triggered_at"),
            triggered_reasons=list(s.get("triggered_reasons", [])),
        )

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _VERSION,
            "saved_at": datetime.utcnow().isoformat(),
            "state": asdict(self._state),
        }
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=self.state_path.name + ".", suffix=".tmp",
            dir=self.state_path.parent,
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, sort_keys=True, indent=2)
            os.replace(tmp_path, self.state_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ---------------- queries ----------------

    def is_triggered(self) -> bool:
        return self._state.triggered

    # ---------------- mutations ----------------

    def check_after_trade(self, trade: TradeResult, account: Account) -> bool:
        """Update counters from this trade, then check all 3 metrics.
        Returns True if the kill switch (already or newly) is triggered."""
        if trade.is_loss:
            self._state.consecutive_losses += 1
        else:
            self._state.consecutive_losses = 0  # win → reset
        self._state.daily_pnl += trade.pnl_dollar

        reasons: list[str] = []
        if self._state.consecutive_losses >= self.consecutive_loss_threshold:
            reasons.append(f"consecutive_losses={self._state.consecutive_losses}")
        if self._state.daily_pnl <= -self.daily_loss_threshold:
            reasons.append(f"daily_pnl={self._state.daily_pnl:.2f}")
        if account.equity <= self.equity_minimum:
            reasons.append(f"equity={account.equity:.2f}")

        if reasons and not self._state.triggered:
            self._fire(reasons)

        self._save_state()
        return self._state.triggered

    def trigger_external(self, *, reason: str, details=None) -> None:
        """ReconcileLoop drift, manual call, etc."""
        if details is None:
            details = []
        full_reason = f"{reason}: {details}" if details else reason
        self._fire([full_reason])
        self._save_state()

    def _fire(self, reasons: list[str]) -> None:
        self._state.triggered = True
        self._state.triggered_at = datetime.utcnow().isoformat()
        self._state.triggered_reasons = reasons
        self.audit_log.emit("KILL_SWITCH_TRIGGERED", reasons=reasons)

    def reset(self) -> None:
        """Manual reset — operator çalıştırır (script wrapper var)."""
        was_triggered = self._state.triggered
        self._state = KillSwitchState()
        self._save_state()
        if was_triggered:
            self.audit_log.emit("KILL_SWITCH_RESET")
