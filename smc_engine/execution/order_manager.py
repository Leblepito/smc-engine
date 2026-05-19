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
    SKIPPED_PRICE_PASSED = "SKIPPED_PRICE_PASSED"  # Bug E-B
    SKIPPED_MAX_CONCURRENT = "SKIPPED_MAX_CONCURRENT"  # İş 1
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
        # Kill switch check
        if self.kill_switch.is_triggered():
            self.audit_log.emit(
                "SETUP_SKIPPED_KILL_SWITCH",
                symbol=symbol, at_bar=at_bar.isoformat(),
                kill_switch_reasons=self.kill_switch._state.triggered_reasons,  # noqa: SLF001
            )
            return ProcessResult.SKIPPED_KILL_SWITCH

        # Concurrent-position guard (max_concurrent_positions; per-tracker).
        # İş 1 (2026-05-19): OrderManager tek tracker'a sahip — multi-symbol
        # kullanımında her sembolün manager'ı kendi tracker'ına sahip
        # (per-symbol init, bkz. examples/run_live.py). Cap PENDING+ACTIVE
        # toplamını sınırlar. <=0 → kapalı.
        cap = self.config.execution_max_concurrent_positions
        if cap > 0:
            pending_ids = [p.order_id for p in self.position_tracker.pending()]
            active_ids = [p.order_id for p in self.position_tracker.active()]
            current = len(pending_ids) + len(active_ids)
            if current >= cap:
                # M-1 code review: blocking order_id'leri forensics için audit'le.
                # Skipped setup → blocking order_id zinciri (ORDER_PLACED →
                # ORDER_FILLED → POSITION_CLOSED) tek satırda izlenebilir.
                self.audit_log.emit(
                    "SETUP_SKIPPED_MAX_CONCURRENT",
                    symbol=symbol, at_bar=at_bar.isoformat(),
                    current_positions=current, max_concurrent=cap,
                    blocking_pending_ids=pending_ids,
                    blocking_active_ids=active_ids,
                )
                return ProcessResult.SKIPPED_MAX_CONCURRENT

        # Pre-place mark price guard (Bug E-B, primary defense).
        # LIMIT entry seviyesi market fiyatın yanlış tarafına geçtiyse,
        # LIMIT anında fill olur ve SL stop_price'ı zaten geçilmiş seviyeye
        # düşer → Binance -2021 "Order would immediately trigger" reddi.
        # Bunun yerine setup'ı yumuşakça atla; sinyal kayıpları audit'te
        # SETUP_SKIPPED_PRICE_PASSED olarak görünür.
        if self.config.execution_pre_place_mark_guard:
            if self._mark_price_passed_entry(validated, symbol, at_bar):
                return ProcessResult.SKIPPED_PRICE_PASSED

        # Position sizing
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

        # Place LIMIT entry
        side = OrderSide.BUY if validated.setup.direction is Direction.LONG else OrderSide.SELL
        # Bug A: HEDGE mode → positionSide LONG/SHORT zorunlu (-4061 önler).
        # ONE_WAY → None (Binance default BOTH).
        position_side = self._resolve_position_side(validated.setup.direction)
        # Bug E-A: post-only → GTX (futures'ın LIMIT_MAKER muadili). Pre-place
        # guard'ı geçen ama hala yanlış tarafta olan setup'lar Binance
        # tarafında place ETMEDEN reddedilir (taker fill engellenir).
        entry_tif = (
            TimeInForce.GTX if self.config.execution_post_only
            else TimeInForce.GTC
        )
        req = OrderRequest(
            symbol=symbol, side=side, type=OrderType.LIMIT,
            qty=size, price=validated.setup.entry,
            time_in_force=entry_tif,
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

        # Track + audit
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
            # M-6 code review: post-mortem'de -5022/-2021 reject pattern'i
            # GTX'ten mi geliyor anlayabilmek için TIF'i de audit'le.
            time_in_force=entry_tif.value,
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
        """Atomik SL+TP placement (Bug D 2026-05-18).

        Main LIMIT entry fill oldu — bu noktada pozisyon zaten Binance'te
        açık. SL veya TP placement fail ederse pozisyon korumasız kalır
        (catastrophic risk). Davranış:

        - SL fail → emergency MARKET close (opposite side) + tracker.on_reject
          + audit ORDER_ROLLBACK. Yeniden deneme YOK.
        - SL ok, TP fail → SL cancel + emergency MARKET close + tracker.on_reject
          + audit ORDER_ROLLBACK.
        - İkisi de ok → tracker.on_fill (ACTIVE), normal akış.

        Önceki davranış (Bug D): SL fail → return → tracker hala PENDING →
        polling bir sonraki tick'te yine FILLED görür → sonsuz SL retry.
        """
        opposite_side = OrderSide.SELL if pending.side == "BUY" else OrderSide.BUY
        # HEDGE mode: exit order'lar aynı positionSide ile gönderilir (LONG
        # pozisyonu SELL ile kapatır ama positionSide hala 'LONG' — yoksa -4061).
        if self.config.execution_position_mode == "HEDGE":
            position_side = "LONG" if pending.side == "BUY" else "SHORT"
        else:
            position_side = None

        # 1) SL order (STOP_MARKET)
        sl_req = OrderRequest(
            symbol=pending.symbol, side=opposite_side, type=OrderType.STOP_MARKET,
            qty=pending.qty, stop_price=pending.sl,
            position_side=position_side,
        )
        try:
            sl_resp = self.order_client.place_order(sl_req)
        except BinanceOrderError as exc:
            self._rollback_filled_order(
                pending=pending, opposite_side=opposite_side,
                position_side=position_side,
                reason="SL_PLACEMENT_FAILED",
                error_code=exc.code, error_message=exc.message,
            )
            return

        # 2) TP order (LIMIT)
        tp_req = OrderRequest(
            symbol=pending.symbol, side=opposite_side, type=OrderType.LIMIT,
            qty=pending.qty, price=pending.tp, time_in_force=TimeInForce.GTC,
            position_side=position_side,
        )
        try:
            tp_resp = self.order_client.place_order(tp_req)
        except BinanceOrderError as exc:
            # SL koyduk ama TP fail — SL cancel + emergency close.
            try:
                self.order_client.cancel_order(pending.symbol, sl_resp.order_id)
            except BinanceOrderError as cancel_exc:
                self.audit_log.emit(
                    "ROLLBACK_SL_CANCEL_FAILED", order_id=pending.order_id,
                    sl_order_id=sl_resp.order_id,
                    error_code=cancel_exc.code, message=cancel_exc.message,
                )
            self._rollback_filled_order(
                pending=pending, opposite_side=opposite_side,
                position_side=position_side,
                reason="TP_PLACEMENT_FAILED",
                error_code=exc.code, error_message=exc.message,
            )
            return

        # 3) Happy path
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

    def _rollback_filled_order(
        self,
        *,
        pending: TrackedPosition,
        opposite_side: OrderSide,
        position_side: Optional[str],
        reason: str,
        error_code: int,
        error_message: str,
    ) -> None:
        """Emergency close (MARKET opposite) + ABORTED + audit ROLLBACK.

        Main LIMIT fill olduktan sonra SL veya TP placement başarısız oldu →
        pozisyon korumasız. MARKET ters order ile derhal kapat; başarılı/
        başarısız ABORTED state'e geç (polling retry önlemek için zorunlu).
        """
        emergency_filled = False
        try:
            close_req = OrderRequest(
                symbol=pending.symbol, side=opposite_side, type=OrderType.MARKET,
                qty=pending.qty, position_side=position_side,
            )
            self.order_client.place_order(close_req)
            emergency_filled = True
        except BinanceOrderError as close_exc:
            # Emergency close da fail etti — manuel müdahale şart. Reconcile
            # loop drift yakalayıp kill switch tetikleyecek; biz ABORTED'a
            # yine de geçiyoruz, polling sonsuz retry yapmasın.
            self.audit_log.emit(
                "ROLLBACK_EMERGENCY_CLOSE_FAILED",
                order_id=pending.order_id,
                error_code=close_exc.code, message=close_exc.message,
            )

        # Tracker'ı ABORTED'a al — polling bir daha bu order'a dokunmaz.
        # PENDING → ABORTED legal transition (on_reject).
        self.position_tracker.on_reject(pending.order_id, reason=reason)

        self.audit_log.emit(
            "ORDER_ROLLBACK",
            order_id=pending.order_id, symbol=pending.symbol,
            reason=reason, error_code=error_code, message=error_message,
            emergency_close_placed=emergency_filled,
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

    def _mark_price_passed_entry(
        self,
        validated: ValidatedSetup,
        symbol: str,
        at_bar: datetime,
    ) -> bool:
        """Bug E-B: Mark price entry seviyesini geçmiş mi?

        LONG: mark < entry → "geçmiş" (LIMIT BUY anında dolar, SL passed).
        SHORT: mark > entry → aynı.
        mark == entry → just-touched edge; geçmemiş kabul (place et).
        Fetch fail → fail-safe: False döner (order place edilir; downstream
        defense-in-depth — post-only ya da atomic rollback yakalar).
        """
        try:
            mark = self.order_client.get_mark_price(symbol)
        except BinanceOrderError as exc:
            # I-2 code review: kill_switch_signal'lı hatalar buradan da
            # eskalasyon yapmalı — guard fail-safe geçse bile gerçek
            # auth/permission/balance problemini bir sonraki place_order'a
            # devretmek yerine erkenden yakala.
            if exc.kill_switch_signal:
                self.kill_switch.trigger_external(
                    reason=f"BINANCE_ERROR_{exc.code}", details=[exc.message],
                )
            self.audit_log.emit(
                "MARK_PRICE_FETCH_FAILED",
                symbol=symbol, at_bar=at_bar.isoformat(),
                error=type(exc).__name__, message=exc.message,
                error_code=exc.code,
            )
            return False
        except Exception as exc:  # network/parse — fail-safe
            self.audit_log.emit(
                "MARK_PRICE_FETCH_FAILED",
                symbol=symbol, at_bar=at_bar.isoformat(),
                error=type(exc).__name__, message=str(exc),
            )
            return False

        entry = validated.setup.entry
        direction = validated.setup.direction
        passed = (
            (direction is Direction.LONG and mark < entry)
            or (direction is Direction.SHORT and mark > entry)
        )
        if passed:
            self.audit_log.emit(
                "SETUP_SKIPPED_PRICE_PASSED",
                symbol=symbol, at_bar=at_bar.isoformat(),
                direction=direction.value, entry=entry, mark_price=mark,
            )
        return passed

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
