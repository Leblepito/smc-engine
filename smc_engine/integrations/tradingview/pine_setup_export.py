"""Setup listesi -> Pine Script v6 overlay snippet'i.

Backtest cikti `Setup` nesnelerini canli chart'a retrospektif olarak izdusurmek
icin kullanilir. Cikti `var line.new` / `var label.new` cagrilariyla
entry/SL/TP seviyelerini cizer. LONG yesil, SHORT kirmizi. Setup tarihi
label icinde.

Bu modul Pine yorumlamaz — sadece string uretir.
Test: ``tests/test_pine_setup_export.py``.
"""

from __future__ import annotations

from smc_engine.types import Direction, Setup


_HEADER = "// === SMC Engine — backtest setup overlay (auto-generated) ==="
_FOOTER = "// === end auto-generated ==="


def _color_for(direction: Direction) -> str:
    return "color.green" if direction == Direction.LONG else "color.red"


def _direction_label(direction: Direction) -> str:
    return "LONG" if direction == Direction.LONG else "SHORT"


def _ts_to_iso_date(setup: Setup) -> str:
    """Setup tarihini Pine v6 ``timestamp("...")`` icin uygun str donduurur.

    Pine v6 ``timestamp(string)`` "yyyy-MM-dd HH:mm:ss" formatini bekler.
    Label gosteriminde ayrica kullanildigi icin saniye eklenmesi gosterimi
    biraz uzatir ama dogrudan timestamp() icine yapistirilabilmesi onemli.
    """
    return setup.created_at.strftime("%Y-%m-%d %H:%M:%S")


def _emit_setup(idx: int, setup: Setup) -> list[str]:
    """Tek bir setup icin Pine v6 cizim deyimleri."""
    color = _color_for(setup.direction)
    label_dir = _direction_label(setup.direction)
    ts_str = _ts_to_iso_date(setup)
    lines: list[str] = []

    lines.append(f"// --- Setup #{idx} ({label_dir} @ {ts_str}) ---")

    # Entry line (calisma alani: setup'in olustugu bar timestamp'ine yatay cizgi)
    lines.append(
        f"var line setup_{idx}_entry = line.new("
        f"x1=timestamp(\"{ts_str}\"), y1={setup.entry}, "
        f"x2=time, y2={setup.entry}, "
        f"xloc=xloc.bar_time, extend=extend.right, "
        f"color={color}, width=2)"
    )

    # SL line
    lines.append(
        f"var line setup_{idx}_sl = line.new("
        f"x1=timestamp(\"{ts_str}\"), y1={setup.sl}, "
        f"x2=time, y2={setup.sl}, "
        f"xloc=xloc.bar_time, extend=extend.right, "
        f"color={color}, style=line.style_dashed, width=1)"
    )

    # TP lines (her TP seviyesi ayri line)
    for tp_idx, tp_level in enumerate(setup.tp):
        lines.append(
            f"var line setup_{idx}_tp{tp_idx} = line.new("
            f"x1=timestamp(\"{ts_str}\"), y1={tp_level}, "
            f"x2=time, y2={tp_level}, "
            f"xloc=xloc.bar_time, extend=extend.right, "
            f"color={color}, style=line.style_dotted, width=1)"
        )

    # Setup label (yon + tarih + RR)
    lines.append(
        f"var label setup_{idx}_lbl = label.new("
        f"x=timestamp(\"{ts_str}\"), y={setup.entry}, "
        f"text=\"{label_dir} {ts_str} RR={setup.rr:.2f}\", "
        f"xloc=xloc.bar_time, style=label.style_label_left, "
        f"color={color}, textcolor=color.white, size=size.small)"
    )

    return lines


def to_pine_overlays(setups: list[Setup]) -> str:
    """``list[Setup]`` -> Pine Script v6 overlay snippet'i.

    Bos liste verilirse header/footer iceren ama icerigi olmayan snippet doner.
    """
    out: list[str] = [_HEADER]
    if not setups:
        out.append("// (no setups)")
    for idx, s in enumerate(setups):
        out.extend(_emit_setup(idx, s))
    out.append(_FOOTER)
    return "\n".join(out) + "\n"
