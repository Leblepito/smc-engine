"""ReconcileLoop — Local state vs Binance drift detection (Spec §4.7).

5A: detect-only. Auto-fix YOK. Her drift → audit + kill_switch.trigger_external.

4 check:
  1) Local PENDING but Binance has no such open order
  2) Local ACTIVE but Binance position qty = 0 (closed externally)
  3) Binance open order not in local state (manual order?)
  4) Local ACTIVE qty != Binance position qty (size mismatch)
"""

from __future__ import annotations

import logging
from typing import Optional

from smc_engine.execution._base import ExecutionAdapter
from smc_engine.execution.audit_log import AuditLog
from smc_engine.execution.kill_switch import KillSwitch
from smc_engine.execution.position_tracker import PositionTracker

logger = logging.getLogger(__name__)


class ReconcileLoop:
    def __init__(
        self,
        order_client: ExecutionAdapter,
        position_tracker: PositionTracker,
        audit_log: AuditLog,
        kill_switch: KillSwitch,
    ) -> None:
        self.order_client = order_client
        self.position_tracker = position_tracker
        self.audit_log = audit_log
        self.kill_switch = kill_switch

    def tick(self) -> None:
        local_pending = self.position_tracker.pending()
        local_active = self.position_tracker.active()

        try:
            binance_orders = self.order_client.get_open_orders()
        except Exception as exc:
            logger.error("reconcile: get_open_orders failed: %s", exc)
            return
        binance_order_ids = {o.order_id for o in binance_orders}

        drifts: list[str] = []

        # Check 1: local PENDING but no Binance order
        for p in local_pending:
            if p.order_id not in binance_order_ids:
                drifts.append(f"PENDING {p.order_id} ({p.symbol}) not in Binance")

        # Check 2 + 4: local ACTIVE vs Binance position
        for a in local_active:
            try:
                pos = self.order_client.get_position(a.symbol)
            except Exception as exc:
                logger.error("reconcile: get_position failed for %s: %s", a.symbol, exc)
                continue
            if pos.qty == 0:
                drifts.append(f"ACTIVE {a.symbol} local_qty={a.qty}, Binance qty=0")
            else:
                # Sign comparison: BUY → positive, SELL → negative
                expected_sign = 1 if a.side == "BUY" else -1
                expected_qty = expected_sign * abs(a.qty)
                if abs(pos.qty - expected_qty) > 1e-9:
                    drifts.append(
                        f"ACTIVE {a.symbol} local_qty={expected_qty} != binance_qty={pos.qty}"
                    )

        # Check 3: Binance order not in local state
        local_order_ids = (
            {p.order_id for p in local_pending}
            | {a.sl_order_id for a in local_active if a.sl_order_id}
            | {a.tp_order_id for a in local_active if a.tp_order_id}
        )
        for o in binance_orders:
            if o.order_id not in local_order_ids:
                drifts.append(
                    f"Binance order {o.order_id} ({o.symbol}) not in local state"
                )

        if drifts:
            self.audit_log.emit("RECONCILE_DRIFT", drifts=drifts)
            self.kill_switch.trigger_external(
                reason="RECONCILE_DRIFT", details=drifts,
            )
