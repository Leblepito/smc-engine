"""``BinanceOrderClient`` — python-binance write API wrap (Spec §4.1).

Sub-proje #2'deki ``BinanceClient`` (read-only) ile paralel ama write
endpoint'leri için ayrı sınıf:
- Mainnet guard kontrolü (testnet=False ise MainnetGuard.is_approved zorunlu)
- Order place / cancel / get / open orders
- Position / account read
- Leverage / margin mode set (idempotent)
- Error code mapping (Spec §12.1): retryable / kill_switch_signal /
  reconcile_needed flagleri ile BinanceOrderError

Tüm metodlar OrderRequest / OrderResponse / Position / Account
dataclass'larıyla çalÄ±Åır (smc_engine.execution._base).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

from binance.client import Client  # type: ignore[import-untyped]

try:
    from binance.exceptions import (  # type: ignore[import-untyped]
        BinanceAPIException,
        BinanceRequestException,
    )
    _BINANCE_API_EXC: tuple = (BinanceAPIException,)
    _BINANCE_NET_EXC: tuple = (BinanceRequestException,)
except Exception:  # pragma: no cover
    _BINANCE_API_EXC = ()
    _BINANCE_NET_EXC = ()

from smc_engine.execution._base import (
    Account,
    OrderRequest,
    OrderResponse,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)
from smc_engine.execution.mainnet_guard import MainnetGuard
from smc_engine.execution.position_sizing import quantize_to_tick
from smc_engine.integrations.binance.symbols import extract_symbol_meta, normalize_symbol
from smc_engine.types import SymbolMeta

if False:  # TYPE_CHECKING workaround for circular import safety
    from smc_engine.config import SMCConfig  # noqa: F401


# ============================================================
# Errors
# ============================================================


class SymbolNotFound(Exception):
    """Sembol Binance futures exchange_info'da bulunamadı."""

    def __init__(self, symbol: str) -> None:
        super().__init__(f"{symbol} not in Binance futures exchange_info")
        self.symbol = symbol


class BinanceOrderError(Exception):
    """Wrapped Binance API error — Spec §12.1 mapping."""

    def __init__(
        self,
        code: int,
        message: str,
        retryable: bool = False,
        kill_switch_signal: bool = False,
        reconcile_needed: bool = False,
    ) -> None:
        self.code = code
        self.message = message
        self.retryable = retryable
        self.kill_switch_signal = kill_switch_signal
        self.reconcile_needed = reconcile_needed
        super().__init__(f"{code}: {message}")


# Error code → flags map (Spec §12.1)
_ERROR_MAP = {
    -1003: dict(retryable=True),                          # Too many requests (429)
    -1013: dict(kill_switch_signal=True),                 # PRICE_FILTER
    -2010: dict(),                                        # NEW_ORDER_REJECTED
    -2011: dict(reconcile_needed=True),                   # CANCEL_REJECTED
    -2019: dict(kill_switch_signal=True),                 # MARGIN_INSUFFICIENT
    -4131: dict(),                                        # PERCENT_PRICE
    -4046: dict(),                                        # No need to change margin (idempotent)
}


# ============================================================
# Retry config (patch'lenebilir)
# ============================================================


_MAX_RETRIES = 3
_RETRY_BACKOFFS = (1.0, 2.0, 4.0)


def _RETRY_SLEEP(seconds: float) -> None:  # pragma: no cover
    time.sleep(seconds)


# ============================================================
# Helpers
# ============================================================


def _to_order_response(d: dict) -> OrderResponse:
    """Binance kline response dict → OrderResponse."""
    return OrderResponse(
        order_id=str(d.get("orderId", "")),
        symbol=d.get("symbol", ""),
        side=OrderSide(d.get("side", "BUY")),
        type=OrderType(d.get("type", "LIMIT")),
        qty=float(d.get("origQty", "0")),
        price=float(d["price"]) if d.get("price") not in (None, "", "0") else None,
        status=OrderStatus(d.get("status", "NEW")),
        fill_qty=float(d.get("executedQty", "0")),
        fill_price=float(d.get("avgPrice", "0")),
        created_at=datetime.now(tz=timezone.utc).replace(tzinfo=None),
    )


def _to_position(d: dict) -> Position:
    return Position(
        symbol=d.get("symbol", ""),
        qty=float(d.get("positionAmt", "0")),
        entry_price=float(d.get("entryPrice", "0")),
        unrealized_pnl=float(d.get("unRealizedProfit", "0")),
        liquidation_price=float(d.get("liquidationPrice", "0")),
        margin_type=d.get("marginType", "isolated"),
    )


# ============================================================
# BinanceOrderClient
# ============================================================


class BinanceOrderClient:
    """python-binance write API wrap. testnet/mainnet URL auto-switch."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool,
        rate_limit_buffer: float = 0.8,
        config: "SMCConfig | None" = None,
    ) -> None:
        if not testnet:
            # Mainnet → 3 katman guard.
            if config is None:
                # Defansif: config verilmedi ama mainnet isteniyor → reject.
                raise RuntimeError(
                    "Mainnet requires SMCConfig argument (for MainnetGuard layer 2 check)."
                )
            if not MainnetGuard.is_approved(config):
                raise RuntimeError(
                    "Mainnet not approved — MainnetGuard.is_approved(config) False. "
                    "Mainnet için: SMC_ALLOW_LIVE=1 + config.execution_live_enabled=true."
                )
        self.testnet = testnet
        self.rate_limit_buffer = rate_limit_buffer
        self._client = Client(api_key=api_key, api_secret=api_secret, testnet=testnet)
        # exchange_info cache (Spec §4.1 / §10.1 — calc_position_size girişi)
        self._symbol_meta_cache: dict[str, SymbolMeta] = {}
        self._exchange_info_fetched_at: Optional[datetime] = None
        self._exchange_info_ttl_seconds: int = 86400  # 24h refresh

    # ---------------- env-based factory ----------------

    @classmethod
    def from_env(
        cls,
        testnet: bool,
        rate_limit_buffer: float = 0.8,
        config: "SMCConfig | None" = None,
    ) -> "BinanceOrderClient":
        """Construct from environment variables — testnet/mainnet key seti seçimi.

        Convention (kullanıcı 2026-05-17):
          - mainnet: BINANCE_API_KEY / BINANCE_API_SECRET
          - testnet: BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET

        testnet=True: testnet keys yoksa public-only modda çalışır (warning).
        testnet=False: mainnet keys zorunlu (yoksa RuntimeError) + MainnetGuard.
        """
        if testnet:
            api_key = os.environ.get("BINANCE_TESTNET_API_KEY", "")
            api_secret = os.environ.get("BINANCE_TESTNET_API_SECRET", "")
            if not api_key or not api_secret:
                logger.warning(
                    "BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET set "
                    "değil — testnet public-only modda. Order place çağrıları "
                    "auth gerektirir; smoke test sırasında set et."
                )
        else:
            api_key = os.environ.get("BINANCE_API_KEY", "")
            api_secret = os.environ.get("BINANCE_API_SECRET", "")
            if not api_key or not api_secret:
                raise RuntimeError(
                    "Mainnet için BINANCE_API_KEY ve BINANCE_API_SECRET .env'de "
                    "olmalı (read+trade permissionlı, withdraw KAPALI key)."
                )
        return cls(
            api_key=api_key, api_secret=api_secret,
            testnet=testnet, rate_limit_buffer=rate_limit_buffer,
            config=config,
        )

    # ---------------- retry + error mapping ----------------

    def _call_with_retry(self, fn, *args, **kwargs):
        """Retryable hatalarda exponential backoff; diÄer hatalar map'lenir + raise."""
        last_exc: Optional[BaseException] = None
        for attempt in range(_MAX_RETRIES):
            try:
                return fn(*args, **kwargs)
            except _BINANCE_API_EXC as exc:
                last_exc = self._map_exception(exc)
                if not last_exc.retryable or attempt >= _MAX_RETRIES - 1:
                    raise last_exc
                _RETRY_SLEEP(_RETRY_BACKOFFS[attempt])
            except _BINANCE_NET_EXC as exc:
                # Network/timeout — retry pattern
                if attempt >= _MAX_RETRIES - 1:
                    raise BinanceOrderError(
                        code=-1, message=f"network: {exc}", retryable=True,
                    )
                _RETRY_SLEEP(_RETRY_BACKOFFS[attempt])
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _map_exception(exc) -> BinanceOrderError:
        code = getattr(exc, "code", -1)
        msg = getattr(exc, "message", str(exc))
        flags = _ERROR_MAP.get(code, {})
        return BinanceOrderError(
            code=code,
            message=msg,
            retryable=bool(flags.get("retryable", False)),
            kill_switch_signal=bool(flags.get("kill_switch_signal", False)),
            reconcile_needed=bool(flags.get("reconcile_needed", False)),
        )

    # ---------------- write endpoints ----------------

    def place_order(self, request: OrderRequest) -> OrderResponse:
        # Bug C (2026-05-18): symbol_meta.tick_size'a göre price/stop_price
        # otomatik quantize — Binance -1111 "Precision is over the maximum"
        # önler. Cache miss durumunda raw değer gönderilir (best-effort).
        tick_size = 0.0
        try:
            tick_size = self.get_symbol_meta(request.symbol).tick_size
        except Exception:
            pass

        kwargs: dict = {
            "symbol": request.symbol,
            "side": request.side.value,
            "type": request.type.value,
            "quantity": request.qty,
        }
        if request.price is not None:
            kwargs["price"] = (
                quantize_to_tick(request.price, tick_size) if tick_size > 0
                else request.price
            )
        if request.stop_price is not None:
            kwargs["stopPrice"] = (
                quantize_to_tick(request.stop_price, tick_size) if tick_size > 0
                else request.stop_price
            )
        if request.type in (OrderType.LIMIT, OrderType.STOP_LIMIT):
            kwargs["timeInForce"] = request.time_in_force.value
        # Hedge mode: positionSide zorunlu (Binance -4061 önler). One-way mode'da None.
        if request.position_side is not None:
            kwargs["positionSide"] = request.position_side
        resp = self._call_with_retry(self._client.futures_create_order, **kwargs)
        return _to_order_response(resp)

    def get_position_mode(self) -> str:
        """Hesabın futures position mode'unu döner — "HEDGE" | "ONE_WAY".

        Binance ``futures_get_position_mode`` → ``{"dualSidePosition": bool}``.
        True = HEDGE (LONG/SHORT ayrı), False = ONE_WAY (BOTH).
        """
        resp = self._call_with_retry(self._client.futures_get_position_mode)
        return "HEDGE" if resp.get("dualSidePosition") else "ONE_WAY"

    def get_mark_price(self, symbol: str) -> float:
        """Sembolün şu anki mark price'ı (Binance premium index endpoint).

        Bug E-B (2026-05-19): Pre-place mark price guard için kullanılır.
        LIMIT entry'den önce gerçek market mark price kontrol edilir; setup
        entry seviyesini geçmişse process_setup SETUP_SKIPPED_PRICE_PASSED
        ile atlanır — Binance -2021 reject + atomic rollback'ten daha
        ekonomik (REST call + audit, vs. order place + rollback + emergency
        close).

        Eksik/null markPrice (M-3 code review 2026-05-19): silent 0.0
        fallback yerine BinanceOrderError(retryable=True) — guard fail-safe
        path'i çalışır (return False + audit MARK_PRICE_FETCH_FAILED), order
        place edilir (defense-in-depth A yakalar). 0.0 fallback olsaydı
        LONG için "mark < entry" hep True olur ve TÜM LONG setup'lar sessizce
        atlanırdı (silent outage).
        """
        norm = normalize_symbol(symbol)
        resp = self._call_with_retry(self._client.futures_mark_price, symbol=norm)
        mark_str = resp.get("markPrice")
        if mark_str is None or mark_str == "":
            raise BinanceOrderError(
                code=-1,
                message=f"markPrice missing in futures_mark_price response for {norm}",
                retryable=True,
            )
        return float(mark_str)

    def cancel_order(self, symbol: str, order_id: str) -> OrderResponse:
        resp = self._call_with_retry(
            self._client.futures_cancel_order, symbol=symbol, orderId=int(order_id),
        )
        return _to_order_response(resp)

    def get_open_orders(self, symbol: Optional[str] = None) -> list[OrderResponse]:
        if symbol is None:
            resp = self._call_with_retry(self._client.futures_get_open_orders)
        else:
            resp = self._call_with_retry(self._client.futures_get_open_orders, symbol=symbol)
        return [_to_order_response(o) for o in resp]

    def get_order(self, symbol: str, order_id: str) -> OrderResponse:
        resp = self._call_with_retry(
            self._client.futures_get_order, symbol=symbol, orderId=int(order_id),
        )
        return _to_order_response(resp)

    # ---------------- read endpoints ----------------

    def get_position(self, symbol: str) -> Position:
        resp = self._call_with_retry(self._client.futures_position_information, symbol=symbol)
        # Liste döner; tek sembol istesek de
        for entry in resp:
            if entry.get("symbol") == symbol:
                return _to_position(entry)
        # Bulunamadıysa zero-position dön
        return Position(symbol=symbol, qty=0.0, entry_price=0.0,
                        unrealized_pnl=0.0, liquidation_price=0.0)

    def get_account(self) -> Account:
        resp = self._call_with_retry(self._client.futures_account)
        return Account(
            equity=float(resp.get("totalWalletBalance", "0")),
            available_margin=float(resp.get("availableBalance", "0")),
            used_margin=float(resp.get("totalInitialMargin", "0")),
        )

    # ---------------- exchange_info / symbol meta ----------------

    def get_symbol_meta(self, symbol: str) -> SymbolMeta:
        """``SymbolMeta`` döner; 24h TTL'li in-memory cache.

        İlk çağrı / TTL bitiminde ``futures_exchange_info`` REST çağrısı yapar
        ve tüm sembolleri parse eder. Sembol bulunamazsa ``SymbolNotFound``.
        """
        norm = normalize_symbol(symbol)
        needs_refresh = (
            self._exchange_info_fetched_at is None
            or (datetime.now(tz=timezone.utc).replace(tzinfo=None)
                - self._exchange_info_fetched_at).total_seconds()
            > self._exchange_info_ttl_seconds
        )
        if needs_refresh:
            self._refresh_exchange_info()
        meta = self._symbol_meta_cache.get(norm)
        if meta is None:
            raise SymbolNotFound(norm)
        return meta

    def _refresh_exchange_info(self) -> None:
        info = self._call_with_retry(self._client.futures_exchange_info)
        cache: dict[str, SymbolMeta] = {}
        for entry in info.get("symbols", []):
            sym = entry.get("symbol", "")
            if not sym:
                continue
            meta = extract_symbol_meta({"symbols": [entry]}, sym)
            if meta is not None:
                cache[sym] = meta
        self._symbol_meta_cache = cache
        self._exchange_info_fetched_at = datetime.now(tz=timezone.utc).replace(tzinfo=None)

    # ---------------- leverage + margin ----------------

    def set_leverage(self, symbol: str, leverage: int) -> None:
        self._call_with_retry(
            self._client.futures_change_leverage, symbol=symbol, leverage=leverage,
        )

    def set_margin_mode(self, symbol: str, mode: str) -> None:
        """Idempotent — Binance -4046 "No need to change margin type" sessizce yutulur."""
        try:
            self._call_with_retry(
                self._client.futures_change_margin_type,
                symbol=symbol, marginType=mode.upper(),
            )
        except BinanceOrderError as exc:
            if exc.code == -4046:
                return  # zaten doÄru mode'da
            raise

    def close(self) -> None:
        close_fn = getattr(self._client, "close_connection", None)
        if callable(close_fn):
            close_fn()
