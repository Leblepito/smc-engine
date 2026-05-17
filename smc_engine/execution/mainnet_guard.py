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
    """Static class — 3 katmanlı mainnet kapısı."""

    @staticmethod
    def check(config: "SMCConfig") -> MainnetMode:
        """Tüm katmanları çalıştır; geÃ§en MAINNET, geçemeyen TESTNET."""
        # Layer 1: env var
        env_val = os.environ.get(_ENV_VAR, "")
        if env_val != _ENV_APPROVED_VALUE:
            logger.info(
                "MainnetGuard layer 1 fail: env %s=%r (need %r) → TESTNET",
                _ENV_VAR, env_val, _ENV_APPROVED_VALUE,
            )
            return MainnetMode.TESTNET

        # Layer 2: config flag
        if not getattr(config, "execution_live_enabled", False):
            logger.warning(
                "MainnetGuard layer 2 fail: env=%s OK but config.execution_live_enabled=False "
                "→ TESTNET (config-driven safety override)",
                _ENV_APPROVED_VALUE,
            )
            return MainnetMode.TESTNET

        # Layer 3: startup delay + WARNING
        logger.critical("â ï¸  " * 20)
        logger.critical("â ï¸   MAINNET MODE ACTIVE — REAL MONEY  â ï¸")
        logger.critical("â ï¸  " * 20)
        logger.critical(
            "Starting in %.0f seconds. Ctrl+C now to abort.", _STARTUP_DELAY_SECONDS,
        )
        _SLEEP(_STARTUP_DELAY_SECONDS)
        logger.critical("MainnetGuard: all 3 layers passed → MAINNET")
        return MainnetMode.MAINNET

    @staticmethod
    def is_approved(config: "SMCConfig") -> bool:
        return MainnetGuard.check(config) is MainnetMode.MAINNET
