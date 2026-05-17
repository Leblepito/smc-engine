"""MainnetGuard placeholder — X3.1'de gerÃ§ek 3 katmanlÄ± impl ile deÄiÅir.

X1'de BinanceOrderClient bu sÄ±nÄ±fÄ± referans alabilsin diye var. ÅÅu an
``is_approved()`` her zaman False dÃ¶ner — testnet bypass'lÄ± Ã§alÄ±ÅÄ±r,
mainnet için X3'te tam implementasyon gelene kadar (kasten) güvenli-default.
"""

from __future__ import annotations


class MainnetGuard:
    """Placeholder — X3.1'de gerçek 3 katmanlÄ± guard ile deÄiÅir."""

    @staticmethod
    def is_approved() -> bool:
        """Default False (X3 öncesi); mainnet asla approve etmez."""
        return False
