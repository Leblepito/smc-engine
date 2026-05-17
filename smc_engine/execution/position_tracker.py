"""PositionTracker — state machine + atomic persistence (Spec §4.3, §7, §8).

State diagram (Spec §7):
    PENDING ──on_fill──→ ACTIVE
            ──on_timeout──→ ABORTED (TIMEOUT)
            ──on_reject──→ ABORTED (REJECTED)
    ACTIVE  ──on_tp_hit──→ CLOSED_WIN
            ──on_sl_hit──→ CLOSED_LOSS
            ──on_manual_close──→ CLOSED_MANUAL
            ──on_drift──→ CLOSED_DRIFT (+ kill_switch.trigger_external)

Persistence: positions-state.json. Every transition triggers atomic save
(temp file + rename), so a crash mid-write never leaves a half-file.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


# ============================================================
# Enums + types
# ============================================================


class PositionState(Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    CLOSED_WIN = "CLOSED_WIN"
    CLOSED_LOSS = "CLOSED_LOSS"
    CLOSED_MANUAL = "CLOSED_MANUAL"
    CLOSED_DRIFT = "CLOSED_DRIFT"
    ABORTED = "ABORTED"


@dataclass
class TrackedPosition:
    order_id: str
    symbol: str
    side: str  # "BUY" / "SELL"
    qty: float
    entry: float
    sl: float
    tp: float
    placed_at: datetime
    timeout_at: datetime
    signal_at_bar: datetime
    risk_dollar: float
    leverage: int
    state: PositionState = PositionState.PENDING
    # Filled when transitioning PENDING → ACTIVE
    sl_order_id: Optional[str] = None
    tp_order_id: Optional[str] = None
    filled_at: Optional[datetime] = None
    fill_price: Optional[float] = None
    fill_qty: Optional[float] = None
    # Filled on close
    closed_at: Optional[datetime] = None
    exit_price: Optional[float] = None
    pnl_dollar: Optional[float] = None
    # Filled on abort
    abort_reason: Optional[str] = None
    drift_details: Optional[str] = None


# ============================================================
# Exceptions
# ============================================================


class IllegalStateTransition(Exception):
    def __init__(self, order_id: str, current: PositionState, attempted: str) -> None:
        self.order_id = order_id
        self.current = current
        self.attempted = attempted
        super().__init__(f"order {order_id}: cannot {attempted} from state {current.value}")


# ============================================================
# Tracker
# ============================================================


_VERSION = 1


class PositionTracker:
    """In-memory state + JSON persistence."""

    def __init__(self) -> None:
        self._positions: dict[str, TrackedPosition] = {}

    # ---------- queries ----------

    def pending(self) -> list[TrackedPosition]:
        return [p for p in self._positions.values() if p.state is PositionState.PENDING]

    def active(self) -> list[TrackedPosition]:
        return [p for p in self._positions.values() if p.state is PositionState.ACTIVE]

    def closed_or_aborted(self) -> list[TrackedPosition]:
        terminal = (
            PositionState.CLOSED_WIN, PositionState.CLOSED_LOSS,
            PositionState.CLOSED_MANUAL, PositionState.CLOSED_DRIFT,
            PositionState.ABORTED,
        )
        return [p for p in self._positions.values() if p.state in terminal]

    def get(self, order_id: str) -> TrackedPosition:
        if order_id not in self._positions:
            raise KeyError(f"unknown order_id: {order_id}")
        return self._positions[order_id]

    # ---------- mutations ----------

    def add(self, position: TrackedPosition) -> None:
        if position.order_id in self._positions:
            raise ValueError(f"duplicate order_id: {position.order_id}")
        self._positions[position.order_id] = position

    def on_fill(self, order_id: str, *, sl_order_id: str, tp_order_id: str,
                fill_price: float, fill_qty: float) -> None:
        p = self.get(order_id)
        if p.state is not PositionState.PENDING:
            raise IllegalStateTransition(order_id, p.state, "on_fill")
        p.state = PositionState.ACTIVE
        p.sl_order_id = sl_order_id
        p.tp_order_id = tp_order_id
        p.fill_price = fill_price
        p.fill_qty = fill_qty
        p.filled_at = datetime.utcnow()

    def on_timeout(self, order_id: str) -> None:
        p = self.get(order_id)
        if p.state is not PositionState.PENDING:
            raise IllegalStateTransition(order_id, p.state, "on_timeout")
        p.state = PositionState.ABORTED
        p.abort_reason = "TIMEOUT"
        p.closed_at = datetime.utcnow()

    def on_reject(self, order_id: str, reason: str) -> None:
        p = self.get(order_id)
        if p.state is not PositionState.PENDING:
            raise IllegalStateTransition(order_id, p.state, "on_reject")
        p.state = PositionState.ABORTED
        p.abort_reason = reason
        p.closed_at = datetime.utcnow()

    def on_tp_hit(self, order_id: str, *, exit_price: float, pnl_dollar: float) -> None:
        p = self.get(order_id)
        if p.state is not PositionState.ACTIVE:
            raise IllegalStateTransition(order_id, p.state, "on_tp_hit")
        self._close(p, PositionState.CLOSED_WIN, exit_price, pnl_dollar)

    def on_sl_hit(self, order_id: str, *, exit_price: float, pnl_dollar: float) -> None:
        p = self.get(order_id)
        if p.state is not PositionState.ACTIVE:
            raise IllegalStateTransition(order_id, p.state, "on_sl_hit")
        self._close(p, PositionState.CLOSED_LOSS, exit_price, pnl_dollar)

    def on_manual_close(self, order_id: str, *, exit_price: float, pnl_dollar: float) -> None:
        p = self.get(order_id)
        if p.state is not PositionState.ACTIVE:
            raise IllegalStateTransition(order_id, p.state, "on_manual_close")
        self._close(p, PositionState.CLOSED_MANUAL, exit_price, pnl_dollar)

    def on_drift(self, order_id: str, details: str) -> None:
        p = self.get(order_id)
        if p.state is not PositionState.ACTIVE:
            raise IllegalStateTransition(order_id, p.state, "on_drift")
        p.state = PositionState.CLOSED_DRIFT
        p.drift_details = details
        p.closed_at = datetime.utcnow()

    def _close(self, p: TrackedPosition, terminal: PositionState,
               exit_price: float, pnl_dollar: float) -> None:
        p.state = terminal
        p.exit_price = exit_price
        p.pnl_dollar = pnl_dollar
        p.closed_at = datetime.utcnow()

    # ---------- persistence (atomic) ----------

    def save_state(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _VERSION,
            "saved_at": datetime.utcnow().isoformat(),
            "positions": [self._serialize(pos) for pos in self._positions.values()],
        }
        # Atomic: temp file in same dir + os.replace (rename is atomic on same FS)
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=p.parent)
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, sort_keys=True, indent=2)
            os.replace(tmp_path, p)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def load_state(self, path: str | Path) -> None:
        p = Path(path)
        if not p.exists():
            return
        data = json.loads(p.read_text(encoding="utf-8"))
        # Schema version check — incompatible versions raise rather than
        # silently corrupt restart recovery.
        file_version = data.get("version", 0)
        if file_version != _VERSION:
            raise ValueError(
                f"PositionTracker state schema version mismatch: "
                f"file={file_version}, expected={_VERSION}. Migrate manually."
            )
        self._positions = {}
        for entry in data.get("positions", []):
            pos = self._deserialize(entry)
            self._positions[pos.order_id] = pos

    @staticmethod
    def _serialize(p: TrackedPosition) -> dict:
        d = asdict(p)
        d["state"] = p.state.value
        # datetime → isoformat
        for k in ("placed_at", "timeout_at", "signal_at_bar", "filled_at", "closed_at"):
            v = d.get(k)
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        return d

    @staticmethod
    def _deserialize(d: dict) -> TrackedPosition:
        # parse datetimes
        for k in ("placed_at", "timeout_at", "signal_at_bar", "filled_at", "closed_at"):
            v = d.get(k)
            if isinstance(v, str):
                d[k] = datetime.fromisoformat(v)
        d["state"] = PositionState(d.get("state", "PENDING"))
        return TrackedPosition(**d)
