"""``BinanceClient`` — python-binance.Client sarmalayıcısı (Sub-proje #2, Spec §3, §10).

Sorumluluk:
- ENV (``BINANCE_API_KEY`` / ``BINANCE_API_SECRET``) ya da explicit param ile auth.
- USDT-M futures REST endpoint'lerini sarmalayan ince metodlar
  (``futures_klines``, ``futures_funding_rate``, ``futures_open_interest``,
  ``futures_exchange_info``).
- 5xx/transient ağ hatalarında exponential backoff ile 3 deneme.
- ``rate_limit_buffer`` (default 0.8): manuel tampon — python-binance kendi
  rate-limit takibini yapar; bu tampon adapter katmanında ek koruma için
  saklanır (BinanceAdapter ileride istek yoğunluğunu burayla harmanlar).

Mock'lanabilir hooks:
- ``_RETRY_EXC``: retry edilecek exception sınıfları (default python-binance
  exception'ları, runtime'da import edilir; mock'lamak için modül seviyesi).
- ``_RETRY_SLEEP``: backoff fonksiyonu (testte no-op'a patch edilebilir).
"""

from __future__ import annotations

import os
import time
from typing import Optional

# python-binance import — bağımlılık. Test ortamında zaten kurulu (pyproject deps).
from binance.client import Client  # type: ignore[import-untyped]

try:  # python-binance exception'ları — bazı sürümlerde adlar değişir
    from binance.exceptions import BinanceAPIException, BinanceRequestException  # type: ignore[import-untyped]
    _RETRY_EXC: tuple[type[BaseException], ...] = (BinanceRequestException,)
except Exception:  # pragma: no cover — kütüphane sürümü farkı
    _RETRY_EXC = ()


def _default_sleep(seconds: float) -> None:  # pragma: no cover — testlerde patch edilir
    time.sleep(seconds)


_RETRY_SLEEP = _default_sleep
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SEC = 0.5


class BinanceClient:
    """python-binance.Client'i sarmalar; retry + rate-limit tamponu sağlar."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        testnet: bool = False,
        rate_limit_buffer: float = 0.8,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get("BINANCE_API_KEY", "")
        secret = (
            api_secret if api_secret is not None else os.environ.get("BINANCE_API_SECRET", "")
        )
        self._client = Client(api_key=key, api_secret=secret, testnet=testnet)
        self.rate_limit_buffer = rate_limit_buffer

    # ---- retry helper -------------------------------------------------

    def _call_with_retry(self, fn, *args, **kwargs):
        """Transient exception'larda exponential backoff ile retry; diğer hatalar pass-through."""
        last_exc: Optional[BaseException] = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                return fn(*args, **kwargs)
            except _RETRY_EXC as exc:  # type: ignore[misc]
                last_exc = exc
                if attempt < _MAX_ATTEMPTS - 1:
                    _RETRY_SLEEP(_BACKOFF_BASE_SEC * (2 ** attempt))
        # tüm denemeler tükendi
        assert last_exc is not None  # mantıken; ama yine de
        raise last_exc

    # ---- futures REST sarmalayıcıları --------------------------------

    def futures_klines(self, *, symbol: str, interval: str, limit: int, **kwargs):
        return self._call_with_retry(
            self._client.futures_klines, symbol=symbol, interval=interval, limit=limit, **kwargs
        )

    def futures_funding_rate(self, *, symbol: str, limit: int = 1, **kwargs):
        return self._call_with_retry(
            self._client.futures_funding_rate, symbol=symbol, limit=limit, **kwargs
        )

    def futures_open_interest(self, *, symbol: str):
        return self._call_with_retry(self._client.futures_open_interest, symbol=symbol)

    def futures_exchange_info(self):
        return self._call_with_retry(self._client.futures_exchange_info)

    def close(self) -> None:
        """python-binance Client'in close_connection benzeri yoksa no-op."""
        close_fn = getattr(self._client, "close_connection", None)
        if callable(close_fn):
            close_fn()
