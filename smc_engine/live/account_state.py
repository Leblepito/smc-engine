"""Log-only mod statik AccountState builder (Spec §8).

Sub-proje #2 emir göndermez; ``AccountState`` yalnızca R-sizing için
``equity`` taşır. ``open_position`` daima False (averaging gate pasif),
``consecutive_losses`` 0 + ``max_drawdown_pct`` 0.0 (drawdown_breaker pasif).
Diğer gate'ler (confluence, regime, deviation, no_sl, min_rr, funding) tam
çalışır → sinyaller hâlâ kalite-filtreli.

#5'te bu builder Binance ``fetch_account`` çıktısından dinamik doldurulacak.
"""

from __future__ import annotations

from smc_engine.config import SMCConfig
from smc_engine.types import AccountState


def build_static_account_state(config: SMCConfig) -> AccountState:
    return AccountState(
        equity=float(config.live_account_equity),
        open_position=False,
        consecutive_losses=0,
        max_drawdown_pct=0.0,
        recent_results=None,
    )
