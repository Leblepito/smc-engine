"""SMC detektor paketi — temiz import yuzeyi (Plan task 1.7).

6 detektorun ``detect`` fonksiyonu ad cakismasi olmadan yeniden adlandirilarak,
paylasilan ``find_swings`` yardimcisi da dogrudan export edilir.

Kullanim:
    from smc_engine.detectors import (
        find_swings,
        detect_range, detect_structure, detect_zones,
        detect_imbalances, detect_liquidity, detect_levels,
    )

Her detektor saf fonksiyon: ``(ohlcv, config, **kwargs) -> list[...]``.
``detect_liquidity`` ek olarak opsiyonel ``known_levels`` parametresi alir.
"""

from __future__ import annotations

from smc_engine.detectors._swing_utils import find_swings
from smc_engine.detectors.imbalance_detector import detect as detect_imbalances
from smc_engine.detectors.level_detector import detect as detect_levels
from smc_engine.detectors.liquidity_detector import detect as detect_liquidity
from smc_engine.detectors.range_detector import detect as detect_range
from smc_engine.detectors.structure_detector import detect as detect_structure
from smc_engine.detectors.zone_detector import detect as detect_zones

__all__ = [
    "find_swings",
    "detect_range",
    "detect_structure",
    "detect_zones",
    "detect_imbalances",
    "detect_liquidity",
    "detect_levels",
]
