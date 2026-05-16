"""Pine Script v6 config export — TradingView entegrasyon katmani testleri.

`to_pine_inputs(SMCConfig)` SMCConfig dataclass alanlarini Pine v6
`input.*` deyimlerine ceviren saf string export. Burada Pine semantigi
test edilmiyor — yalnizca format ve icerik dogrulugu.
"""

from __future__ import annotations

from dataclasses import fields

from smc_engine.config import SMCConfig, ConfluenceWeights
from smc_engine.integrations.tradingview.pine_config_export import to_pine_inputs


def test_header_and_footer_markers_present():
    out = to_pine_inputs(SMCConfig())
    assert "// === SMC Engine" in out
    assert "// === end auto-generated ===" in out


def test_idempotent():
    cfg = SMCConfig()
    assert to_pine_inputs(cfg) == to_pine_inputs(cfg)


def test_int_field_emits_input_int():
    out = to_pine_inputs(SMCConfig())
    # swing_lookback: int = 4
    assert "swing_lookback = input.int(4" in out


def test_float_field_emits_input_float():
    out = to_pine_inputs(SMCConfig())
    # ob_breakout_threshold: float = 1.5
    assert "ob_breakout_threshold = input.float(1.5" in out
    assert "fvg_min_gap_atr = input.float(0.3" in out


def test_string_field_emits_input_string():
    out = to_pine_inputs(SMCConfig())
    # asset_class: str = "crypto"
    assert "asset_class = input.string(\"crypto\"" in out


def test_all_scalar_fields_appear_with_exact_names():
    """SMCConfig'in tum scalar alanlari (snake_case) ciktida birebir ayni
    isimle bir input.* deyimi olarak yer almali. Ratchet -> Pine sync icin
    KRITIK: alan adi degisirse Pine input adi da degismeli.
    """
    out = to_pine_inputs(SMCConfig())
    SUBMAP = {"confluence_weights", "tf_lookback", "poi_kind_quality", "zone_status_factor"}
    TUPLE_FIELDS = {"tp_r_multiples", "tp_weights"}
    for f in fields(SMCConfig):
        if f.name in SUBMAP:
            continue
        if f.name in TUPLE_FIELDS:
            # Tuple alanlar indeksli olarak emit edilir: name_0, name_1, ...
            assert f"{f.name}_0 = input." in out, f"Missing tuple input for field: {f.name}"
            continue
        # Her scalar alan adi bir input. deyiminin solunda yer almali
        assert f"{f.name} = input." in out, f"Missing input for field: {f.name}"


def test_tuple_field_emits_indexed_inputs():
    out = to_pine_inputs(SMCConfig())
    # tp_r_multiples: tuple[float, float, float] = (1.5, 2.62, 4.23)
    assert "tp_r_multiples_0 = input.float(1.5" in out
    assert "tp_r_multiples_1 = input.float(2.62" in out
    assert "tp_r_multiples_2 = input.float(4.23" in out
    # tp_weights: tuple[float, float, float] = (0.5, 0.3, 0.2)
    assert "tp_weights_0 = input.float(0.5" in out
    assert "tp_weights_1 = input.float(0.3" in out
    assert "tp_weights_2 = input.float(0.2" in out


def test_confluence_weights_all_present_with_group():
    out = to_pine_inputs(SMCConfig())
    weights_default = ConfluenceWeights()
    for f in fields(ConfluenceWeights):
        # Her ConfluenceWeights alani ayri bir input.float satiri olmali
        assert f"{f.name} = input.float(" in out, f"Missing weight input: {f.name}"
        assert getattr(weights_default, f.name) is not None
    # group="Confluence weights" en az bir defa kullanilmis olmali
    assert 'group="Confluence weights"' in out


def test_dict_fields_emit_one_input_per_key():
    out = to_pine_inputs(SMCConfig())
    # poi_kind_quality default: ZONE=1.0, LEVEL=0.6, IMBALANCE=0.5
    assert "poi_kind_quality_ZONE = input.float(1.0" in out
    assert "poi_kind_quality_LEVEL = input.float(0.6" in out
    assert "poi_kind_quality_IMBALANCE = input.float(0.5" in out
    # zone_status_factor default: FRESH/TESTED/MITIGATED/BROKEN
    assert "zone_status_factor_FRESH = input.float(1.0" in out
    assert "zone_status_factor_TESTED = input.float(0.7" in out
    assert "zone_status_factor_MITIGATED = input.float(0.3" in out
    assert "zone_status_factor_BROKEN = input.float(0.0" in out


def test_overridden_config_reflected_in_output():
    cfg = SMCConfig(swing_lookback=7, ob_breakout_threshold=2.25, asset_class="forex")
    out = to_pine_inputs(cfg)
    assert "swing_lookback = input.int(7" in out
    assert "ob_breakout_threshold = input.float(2.25" in out
    assert "asset_class = input.string(\"forex\"" in out


def test_output_is_non_empty_string():
    out = to_pine_inputs(SMCConfig())
    assert isinstance(out, str)
    assert len(out) > 200  # birden fazla alan icermesi gerekiyor
