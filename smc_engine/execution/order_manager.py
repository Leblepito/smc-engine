"""OrderManager — ValidatedSetup → real order pipeline (Spec §4.2, §6).

Sorumluluk:
- Kill switch check
- Position sizing (calc_position_size)
- place_order LIMIT (entry)
- On fill: place SL (STOP_MARKET) + TP (LIMIT), mark_active
- Timeout watcher: 60dk fill yok → cancel + abort
- Fill polling: PENDING → ACTIVE, ACTIVE position kapandı mı kontrol
- AuditLog her aÅamada

OrderManager state-less; tüm state PositionTracker'da.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from smc_engine.config import SMCConfig
from smc_engine.execution._base import (
    Account,
    ExecutionAdapter,
    OrderRequest,
    OrderResponse,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from smc_engine.execution.audit_log import AuditLog
from smc_engine.execution.kill_switch import KillSwitch, TradeResult
from smc_engine.execution.position_sizing import (
    InsufficientMargin,
    InvalidStopLoss,
    OrderSizeBelowMinimum,
    calc_position_size,
)
from smc_engine.execution.position_tracker import PositionTracker, TrackedPosition
from smc_engine.integrations.binance.order_client import BinanceOrderError
from smc_engine.types import Direction, ValidatedSetup

logger = logging.getLogger(__name__)


class ProcessResult(Enum):
    PLACED = "PLACED"
    SKIPPED_KILL_SWITCH = "SKIPPED_KILL_SWITCH"
    SIZING_FAILED = "SIZING_FAILED"
    REJECTED = "REJECTED"


class OrderManager:
    def __init__(
        self,
        order_client: ExecutionAdapter,
        position_tracker: PositionTracker,
        audit_log: AuditLog,
        kill_switch: KillSwitch,
        config: SMCConfig,
        symbol_to_btc: Optional[str] = None,  # placeholder for per-symbol meta lookup
    ) -> None:
        self.order_client = order_client
        self.position_tracker = position_tracker
        self.audit_log = audit_log
        self.kill_switch = kill_switch
        self.config = config

    # ============================================================
    # Setup processing
    # ============================================================

    def process_setup(
        self,
        validated: ValidatedSetup,
        symbol: str,
        at_bar: datetime,
    ) -> ProcessResult:
        # 1. Kill switch check
        if self.kill_switch.is_triggered():
            self.audit_log.emit(
                "SETUP_SKIPPED_KILL_SWITCH",
                symbol=symbol, at_bar=at_bar.isoformat(),
                kill_switch_reasons=self.kill_switch._state.triggered_reasons,  # noqa: SLF001
            )
            return ProcessResult.SKIPPED_KILL_SWITCH

        # 2. Position sizing
        try:
            symbol_meta = self.order_client.get_symbol_meta(symbol)
            account = self.order_client.get_account()
            # min_notional exchange'den geliyorsa onu kullan, yoksa 5.0 safe default.
            mn = symbol_meta.min_notional if symbol_meta.min_notional > 0 else 5.0
            size = calc_position_size(
                risk_dollar=self.config.execution_risk_per_trade_dollar,
                entry=validated.setup.entry,
                sl=validated.setup.sl,
                leverage=self.config.execution_leverage,
                symbol_meta=symbol_meta,
                account=account,
                min_notional=mn,
            )
        except (OrderSizeBelowMinimum, InsufficientMargin, InvalidStopLoss) as exc:
            self.audit_log.emit(
                "SETUP_SIZING_FAILED",
                symbol=symbol, at_bar=at_bar.isoformat(),
                error=type(exc).__name__, message=str(exc),
            )
            return ProcessResult.SIZING_FAILED

        # 3. Place LIMIT order
        side = OrderSide.BUY if validated.setup.direction is Direction.LONG else OrderSide.SELL
        # Bug A: HEDGE mode → positionSide LONG/SHORT zorunlu (-4061 önler).
        # ONE_WAY → None (Binance default BOTH).
        position_side = self._resolve_position_side(validated.setup.direction)
        req = OrderRequest(
            symbol=symbol, side=side, type=OrderType.LIMIT,
            qty=size, price=validated.setup.entry,
            time_in_force=TimeInForce.GTC,
            position_side=position_side,
        )
        try:
            resp = self.order_client.place_order(req)
        except BinanceOrderError as exc:
            self.audit_log.emit(
                "ORDER_REJECTED",
                symbol=symbol, at_bar=at_bar.isoformat(),
                error_code=exc.code, message=exc.message,
            )
            if exc.kill_switch_signal:
                self.kill_switch.trigger_external(
                    reason=f"BINANCE_ERROR_{exc.code}", details=[exc.message],
                )
            return ProcessResult.REJECTED

        # 4. Track + audit
        timeout_at = at_bar + timedelta(minutes=self.config.execution_order_timeout_minutes)
        tracked = TrackedPosition(
            order_id=resp.order_id, symbol=symbol, side=side.value,
            qty=size, entry=validated.setup.entry,
            sl=validated.setup.sl, tp=validated.setup.tp[0],  # 5A: tek TP
            placed_at=at_bar, timeout_at=timeout_at,
            signal_at_bar=at_bar,
            risk_dollar=self.config.execution_risk_per_trade_dollar,
            leverage=self.config.execution_leverage,
        )
        self.position_tracker.add(tracked)
        self.audit_log.emit(
            "ORDER_PLACED",
            order_id=resp.order_id, symbol=symbol,
            side=side.value, qty=size, price=validated.setup.entry,
            sl=validated.setup.sl, tp=validated.setup.tp[0],
            at_bar=at_bar.isoformat(),
            confluence_score=validated.setup.confluence_score,
        )
        return ProcessResult.PLACED

    # ============================================================
    # Timeout watcher
    # ============================================================

    def tick_timeout_watcher(self, now: Optional[datetime] = None) -> None:
        if now is None:
            now = datetime.utcnow()
        for p in list(self.position_tracker.pending()):
            if now < p.timeout_at:
                continue
            try:
                self.order_client.cancel_order(p.symbol, p.order_id)
            except BinanceOrderError as exc:
                self.audit_log.emit(
                    "ORDER_CANCEL_FAILED",
                    order_id=p.order_id, error_code=exc.code, message=exc.message,
                )
                # Even if cancel fails, mark aborted (reconcile will catch drift)
            self.audit_log.emit(
                "ORDER_TIMEOUT", order_id=p.order_id, symbol=p.symbol,
                duration_minutes=self.config.execution_order_timeout_minutes,
            )
            self.position_tracker.on_timeout(p.order_id)

    # ============================================================
    # Fill polling
    # ============================================================

    def tick_fill_polling(self) -> None:
        # PENDING → ACTIVE
        for p in list(self.position_tracker.pending()):
            status = self.order_client.get_order(p.symbol, p.order_id)
            if status.status is OrderStatus.FILLED:
                self._on_fill(p, status)

        # ACTIVE → CLOSED (TP/SL hit)
        for p in list(self.position_tracker.active()):
            position = self.order_client.get_position(p.symbol)
            if position.qty == 0:
                self._on_position_close(p)

    def _on_fill(self, pending: TrackedPosition, fill: OrderResponse) -> None:
        opposite_side = OrderSide.SELL if pending.side == "BUY" else OrderSide.BUY
        # HEDGE mode: exit order'lar aynı positionSide ile gönderilir (LONG
        # pozisyonu SELL ile kapatır ama positionSide hala 'LONG' — yoksa -4061).
        if self.config.execution_position_mode == "HEDGE":
            position_side = "LONG" if pending.side == "BUY" else "SHORT"
        else:
            position_side = None

        # SL order (STOP_MARKET)
        sl_req = OrderRequest(
            symbol=pending.symbol, side=opposite_side, type=OrderType.STOP_MARKET,
            qty=pending.qty, stop_price=pending.sl,
            position_side=position_side,
        )
        try:
            sl_resp = self.order_client.place_order(sl_req)
        except BinanceOrderError as exc:
            self.audit_log.emit(
                "SL_ORDER_FAILED", order_id=pending.order_id,
                error_code=exc.code, message=exc.message,
            )
            return

        # TP order (LIMIT)
        tp_req = OrderRequest(
            symbol=pending.symbol, side=opposite_side, type=OrderType.LIMIT,
            qty=pending.qty, price=pending.tp, time_in_force=TimeInForce.GTC,
            position_side=position_side,
        )
        try:
            tp_resp = self.order_client.place_order(tp_req)
        except BinanceOrderError as exc:
            self.audit_log.emit(
                "TP_ORDER_FAILED", order_id=pending.order_id,
                error_code=exc.code, message=exc.message,
            )
            return

        self.position_tracker.on_fill(
            pending.order_id, sl_order_id=sl_resp.order_id,
            tp_order_id=tp_resp.order_id,
            fill_price=fill.fill_price, fill_qty=fill.fill_qty,
        )
        self.audit_log.emit(
            "ORDER_FILLED",
            order_id=pending.order_id, symbol=pending.symbol,
            fill_price=fill.fill_price, fill_qty=fill.fill_qty,
            slippage=fill.fill_price - pending.entry,
            sl_order_id=sl_resp.order_id, tp_order_id=tp_resp.order_id,
        )

    def _on_position_close(self, active: TrackedPosition) -> None:
        """Position kapanmıÅ — TP mi SL mi hit'i check et."""
        if active.tp_order_id is None or active.sl_order_id is None:
            return
        tp_status = self.order_client.get_order(active.symbol, active.tp_order_id)
        sl_status = self.order_client.get_order(active.symbol, active.sl_order_id)

        # PnL hesabı (5A: tek-TP, basit)
        side_sign = 1 if active.side == "BUY" else -1
        if tp_status.status is OrderStatus.FILLED:
            exit_price = tp_status.fill_price
            pnl = side_sign * (exit_price - active.fill_price) * active.qty
            self.position_tracker.on_tp_hit(
                active.order_id, exit_price=exit_price, pnl_dollar=pnl,
            )
            self.audit_log.emit(
                "TP_HIT", order_id=active.order_id, symbol=active.symbol,
                exit_price=exit_price, pnl_dollar=pnl,
            )
            # cancel the SL order (no longer needed)
            try:
                self.order_client.cancel_order(active.symbol, active.sl_order_id)
            except BinanceOrderError:
                pass
            self._notify_kill_switch(active.order_id, pnl)

        elif sl_status.status is OrderStatus.FILLED:
            exit_price = sl_status.fill_price
            pnl = side_sign * (exit_price - active.fill_price) * active.qty
            self.position_tracker.on_sl_hit(
                active.order_id, exit_price=exit_price, pnl_dollar=pnl,
            )
            self.audit_log.emit(
                "SL_HIT", order_id=active.order_id, symbol=active.symbol,
                exit_price=exit_price, pnl_dollar=pnl,
            )
            try:
                self.order_client.cancel_order(active.symbol, active.tp_order_id)
            except BinanceOrderError:
                pass
            self._notify_kill_switch(active.order_id, pnl)
        # else: position closed but neither TP nor SL filled → manual / drift
        # Reconcile loop will handle.

    def _resolve_position_side(self, direction: Direction) -> Optional[str]:
        """HEDGE mode → 'LONG'/'SHORT'; ONE_WAY → None.

        Bug A (2026-05-18): testnet/HEDGE hesaplarında positionSide zorunlu
        (Binance -4061 önler). ONE_WAY hesaplarında positionSide gönderilmez
        (varsayılan BOTH).
        """
        if self.config.execution_position_mode != "HEDGE":
            return None
        return "LONG" if direction is Direction.LONG else "SHORT"

    def _notify_kill_switch(self, order_id: str, pnl: float) -> None:
        try:
            account = self.order_client.get_account()
        except Exception as exc:
            logger.warning("kill_switch check skipped (account fetch failed): %s", exc)
            return
        result = TradeResult(order_id=order_id, pnl_dollar=pnl)
        self.kill_switch.check_after_trade(result, account)
