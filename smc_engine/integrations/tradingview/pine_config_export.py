"""SMCConfig -> Pine Script v6 ``input.*`` bloku donusumu.

Ratchet -> Pine sync icin: ratchet ``SMCConfig`` parametrelerini optimize
edince burada uretilen Pine input bloku TradingView Pine Editor'a yapistirilir.
Boylelikle Pine indikator parametreleri Python tarafindaki *default*'larla
birebir uyumlu kalir. Alan isimleri (snake_case) Pine'da da gecerli oldugu
icin **birebir** korunur — alan adi degisirse Pine input adi da degisir.

Bu modul *Pine* yorumlamaz; salt string olusturur. Test ediliyor:
``tests/test_pine_config_export.py``.
"""

from __future__ import annotations

from dataclasses import fields

from smc_engine.config import SMCConfig, ConfluenceWeights


_HEADER = "// === SMC Engine — auto-generated inputs (ratchet sync) ==="
_FOOTER = "// === end auto-generated ==="

# Direkt scalar donusumde tutulmayan alanlar — bunlar elle aciklanir.
_SUBMAP_FIELDS = {
    "confluence_weights",
    "tf_lookback",
    "poi_kind_quality",
    "zone_status_factor",
}
_TUPLE_FIELDS = {"tp_r_multiples", "tp_weights"}


def _pine_float_literal(v: float) -> str:
    """Pine'in beklentisine uygun float gosterim — int'leri 1.0 yapar."""
    if isinstance(v, bool):  # bool da int subclass'i; ayri kola gitsin
        return "true" if v else "false"
    f = float(v)
    if f.is_integer():
        return f"{f:.1f}"  # 1.0, 2.0 — Pine input.float beklerken int olur, .0 ile zorla
    return repr(f)  # 1.5, 0.3 — kullanicinin yazdigi gibi


def _emit_scalar_input(name: str, value, group: str | None = None) -> str:
    """Tek bir alani Pine v6 ``input.*`` deyimine cevirir."""
    group_part = f', group="{group}"' if group else ""
    if isinstance(value, bool):
        return f'{name} = input.bool({"true" if value else "false"}, title="{name}"{group_part})'
    if isinstance(value, int):
        return f'{name} = input.int({value}, title="{name}"{group_part})'
    if isinstance(value, float):
        return f'{name} = input.float({_pine_float_literal(value)}, title="{name}"{group_part})'
    if isinstance(value, str):
        return f'{name} = input.string("{value}", title="{name}"{group_part})'
    raise TypeError(f"Unsupported scalar type for {name}: {type(value).__name__}")


def _emit_tuple_inputs(name: str, values: tuple) -> list[str]:
    """Tuple alan -> indeksli ``input.float`` deyimleri (``name_0``, ``name_1`` ...)."""
    out: list[str] = []
    for idx, v in enumerate(values):
        out.append(
            f'{name}_{idx} = input.float({_pine_float_literal(v)}, '
            f'title="{name}[{idx}]", group="{name}")'
        )
    return out


def _emit_dict_inputs(name: str, mapping: dict) -> list[str]:
    """Dict alan -> her anahtar icin ayri ``input.float`` deyimi."""
    out: list[str] = []
    for key, v in mapping.items():
        key_str = key.name if hasattr(key, "name") else str(key)
        out.append(
            f'{name}_{key_str} = input.float({_pine_float_literal(v)}, '
            f'title="{name}[{key_str}]", group="{name}")'
        )
    return out


def _emit_confluence_weights(cw: ConfluenceWeights) -> list[str]:
    """ConfluenceWeights dataclass'i -> Pine input.float'lar (group: Confluence weights)."""
    out: list[str] = []
    for f in fields(ConfluenceWeights):
        v = getattr(cw, f.name)
        out.append(
            f'{f.name} = input.float({_pine_float_literal(v)}, '
            f'title="{f.name}", group="Confluence weights")'
        )
    return out


def to_pine_inputs(config: SMCConfig) -> str:
    """``SMCConfig`` -> Pine Script v6 ``input.*`` bloku.

    Cikti deterministic ve idempotent — ayni config ayni stringi verir.
    Alan adlari Pine input degisken isimleriyle birebir esit.
    """
    lines: list[str] = [_HEADER]

    # 1) Scalar + tuple alanlar (config field siras ile, deterministik)
    lines.append("// --- Detector & risk scalars ---")
    for f in fields(SMCConfig):
        if f.name in _SUBMAP_FIELDS:
            continue
        value = getattr(config, f.name)
        if f.name in _TUPLE_FIELDS and isinstance(value, (tuple, list)):
            lines.extend(_emit_tuple_inputs(f.name, tuple(value)))
        else:
            lines.append(_emit_scalar_input(f.name, value))

    # 2) Confluence weights
    lines.append("")
    lines.append("// --- Confluence weights ---")
    lines.extend(_emit_confluence_weights(config.confluence_weights))

    # 3) Dict alanlar (POI kalite + zone status)
    lines.append("")
    lines.append("// --- POI kind quality map ---")
    lines.extend(_emit_dict_inputs("poi_kind_quality", config.poi_kind_quality))

    lines.append("")
    lines.append("// --- Zone status factor map ---")
    lines.extend(_emit_dict_inputs("zone_status_factor", config.zone_status_factor))

    lines.append("")
    lines.append(_FOOTER)
    return "\n".join(lines) + "\n"
