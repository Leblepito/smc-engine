"""MainnetGuard — 3 katmanlı mainnet kapısı (Spec §4.4, §11).

KRİTİK: gerçek para kullanan tek noktanın önündeki bariyer. Yanlış
geçirmek tÃ¼m sub-proje #5A'nın güvenlik gerekçesini bozar.

Katmanlar (whichever fails first → TESTNET):
  1) env: ``SMC_ALLOW_LIVE`` exactly "1" (anything else = reject)
  2) config: ``execution_live_enabled`` is True
  3) startup: 5sn delay + CRITICAL log warning (operatör Ctrl+C için pencere)

Her katman geÃ§erse → MAINNET. AksiÅhalde TESTNET zorlanır.
"""

from __future__ import annotations

import logging
import os
import time
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from smc_engine.config import SMCConfig

logger = logging.getLogger(__name__)


class MainnetMode(Enum):
    TESTNET = "TESTNET"
    MAINNET = "MAINNET"


# Patch-able for tests (no real sleep in test suite)
_SLEEP = time.sleep
_STARTUP_DELAY_SECONDS = 5.0
_ENV_VAR = "SMC_ALLOW_LIVE"
_ENV_APPROVED_VALUE = "1"


class MainnetGuard:
    """Static class — 3 katmanlı mainnet kapısı.

    Decision is cached process-wide (`_cached_result`) — first call runs all
    3 layers including 5sn delay; subsequent calls from same process return
    cached MAINNET/TESTNET decision (avoids double-delay when both the CLI
    and BinanceOrderClient call is_approved separately).
    """

    _cached_result: "MainnetMode | None" = None

    @classmethod
    def check(cls, config: "SMCConfig") -> MainnetMode:
        """Tüm katmanları çalıştır; geÃ§en MAINNET, geçemeyen TESTNET.

        First call runs full check (incl 5sn delay if all 3 pass); subsequent
        calls within the same process return cached decision.
        """
        if cls._cached_result is not None:
            return cls._cached_result

        # Layer 1: env var
        env_val = os.environ.get(_ENV_VAR, "")
        if env_val != _ENV_APPROVED_VALUE:
            logger.info(
                "MainnetGuard layer 1 fail: env %s=%r (need %r) → TESTNET",
                _ENV_VAR, env_val, _ENV_APPROVED_VALUE,
            )
            cls._cached_result = MainnetMode.TESTNET
            return cls._cached_result

        # Layer 2: config flag
        if not getattr(config, "execution_live_enabled", False):
            logger.warning(
                "MainnetGuard layer 2 fail: env=%s OK but config.execution_live_enabled=False "
                "→ TESTNET (config-driven safety override)",
                _ENV_APPROVED_VALUE,
            )
            cls._cached_result = MainnetMode.TESTNET
            return cls._cached_result

        # Layer 3: startup delay + WARNING (only on first call)
        logger.critical("â ï¸  " * 20)
        logger.critical("â ï¸   MAINNET MODE ACTIVE — REAL MONEY  â ï¸")
        logger.critical("â ï¸  " * 20)
        logger.critical(
            "Starting in %.0f seconds. Ctrl+C now to abort.", _STARTUP_DELAY_SECONDS,
        )
        _SLEEP(_STARTUP_DELAY_SECONDS)
        logger.critical("MainnetGuard: all 3 layers passed → MAINNET")
        cls._cached_result = MainnetMode.MAINNET
        return cls._cached_result

    @classmethod
    def is_approved(cls, config: "SMCConfig") -> bool:
        return cls.check(config) is MainnetMode.MAINNET

    @classmethod
    def reset_cache_for_tests(cls) -> None:
        """Test helper — clears the cached decision between test cases."""
        cls._cached_result = None
