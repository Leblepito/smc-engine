"""Detektor ortak kume-yardimcilari (U-11).

`range_detector` ve `liquidity_detector` arasinda neredeyse birebir
tekrar eden fiyat-yakinligi kumeleme mantigi burada konsolide edildi.
"""

from __future__ import annotations

from typing import Callable, TypeVar

T = TypeVar("T")


def cluster_by_price(
    points: list[T],
    tolerance: float,
    *,
    price_of: Callable[[T], float],
) -> list[list[T]]:
    """Verilen `points` listesini fiyat-yakinligina gore kumele (greedy).

    Iki nokta ayni kumeye girer: `abs(p2 - p1) / abs(p1) <= tolerance`.
    Algoritma:
      1. `price_of` ile fiyatlari okuyup artan siralar.
      2. Ardisik fark esikten kucuk-esitse ayni kumeye ekler, aksi halde
         yeni kume baslatir.

    Args:
        points: Kumelenecek noktalar (`SwingPoint`, `(idx, price)` tuple
            vb. — `price_of` ile fiyat cikartilabildigi surece her tip
            kabul edilir).
        tolerance: Goreli fiyat farki esigi (`equal_level_tolerance`).
        price_of: Bir noktanin fiyatini donen callable.

    Returns:
        Kume listesi; her kume en az 1 nokta icerir. `points` bos ise `[]`.
    """
    if not points:
        return []
    ordered = sorted(points, key=price_of)
    clusters: list[list[T]] = [[ordered[0]]]
    for p in ordered[1:]:
        ref = price_of(clusters[-1][-1])
        cur = price_of(p)
        if ref != 0 and abs(cur - ref) / abs(ref) <= tolerance:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return clusters
