"""config.py testleri — yaml yükleme, override, lookback→mum çevrimi, varsayılanlar."""

import textwrap

import pytest

from smc_engine.config import (
    TF_LOOKBACK,
    TF_MINUTES,
    ConfluenceWeights,
    SMCConfig,
    load_config,
    lookback_bars,
    lookback_minutes,
)
from smc_engine.types import TimeFrame


# ---------------- Varsayılanlar (Spec §13) ----------------


def test_detector_param_defaults():
    c = SMCConfig()
    assert c.swing_lookback == 4
    assert c.ob_breakout_threshold == 1.5
    assert c.fvg_min_gap_atr == 0.3
    assert c.deviation_tolerance_atr == 0.5
    assert c.equal_level_tolerance == 0.001
    assert c.max_zone_age_bars == 200
    assert c.confluence_min_score == 0.4
    assert c.min_rr == 1.5
    assert c.risk_pct == 0.01
    assert c.max_consecutive_losses == 5
    assert c.max_drawdown_pct == 0.10
    assert c.sl_min_atr_multiple == 0.5
    assert c.funding_buffer_minutes == 30


def test_confluence_weights_defaults():
    w = ConfluenceWeights()
    assert w.poi_quality == 0.25
    assert w.premium_discount == 0.20
    assert w.liquidity_context == 0.20
    assert w.level_confluence == 0.15
    assert w.fvg_imbalance == 0.10
    assert w.clustering == 0.10
    # Spec §7 ağırlıkları toplamı 1.0 olmalı.
    assert w.total() == pytest.approx(1.0)


def test_smcconfig_has_confluence_weights():
    c = SMCConfig()
    assert isinstance(c.confluence_weights, ConfluenceWeights)
    assert c.confluence_weights.total() == pytest.approx(1.0)


# ---------------- TF_LOOKBACK (Spec §4) ----------------


def test_tf_lookback_table():
    assert TF_LOOKBACK == {
        TimeFrame.D1: 365,
        TimeFrame.H8: 550,
        TimeFrame.H4: 600,
        TimeFrame.H1: 500,
        TimeFrame.M15: 336,
    }


def test_lookback_bars_helper():
    assert lookback_bars(TimeFrame.D1) == 365
    assert lookback_bars(TimeFrame.M15) == 336


def test_lookback_minutes_conversion():
    # D1: 365 mum × 1440 dk
    assert lookback_minutes(TimeFrame.D1) == 365 * 1440
    # M15: 336 mum × 15 dk
    assert lookback_minutes(TimeFrame.M15) == 336 * 15
    # H4: 600 mum × 240 dk
    assert lookback_minutes(TimeFrame.H4) == 600 * 240


def test_smcconfig_lookback_methods():
    c = SMCConfig()
    assert c.lookback_bars(TimeFrame.H1) == 500
    assert c.lookback_minutes(TimeFrame.H1) == 500 * 60


def test_tf_minutes_complete():
    for tf in TimeFrame:
        assert tf in TF_MINUTES
        assert tf in TF_LOOKBACK


# ---------------- load_config — yaml yükleme + override ----------------


def test_load_config_none_returns_defaults():
    c = load_config(None)
    assert c.swing_lookback == 4
    assert c.min_rr == 1.5


def test_load_config_missing_file_returns_defaults(tmp_path):
    c = load_config(tmp_path / "does_not_exist.yaml")
    assert c.swing_lookback == 4


def test_load_config_scalar_override(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent("""
        swing_lookback: 6
        min_rr: 2.5
        risk_pct: 0.02
    """))
    c = load_config(p)
    assert c.swing_lookback == 6
    assert c.min_rr == 2.5
    assert c.risk_pct == 0.02
    # dokunulmayan alanlar varsayılan kalır
    assert c.ob_breakout_threshold == 1.5


def test_load_config_confluence_weight_override(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent("""
        confluence_weights:
          poi_quality: 0.40
          clustering: 0.05
    """))
    c = load_config(p)
    assert c.confluence_weights.poi_quality == 0.40
    assert c.confluence_weights.clustering == 0.05
    # dokunulmayan ağırlık varsayılan kalır
    assert c.confluence_weights.premium_discount == 0.20


def test_load_config_tf_lookback_override(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent("""
        tf_lookback:
          D1: 450
          H4: 720
    """))
    c = load_config(p)
    assert c.tf_lookback[TimeFrame.D1] == 450
    assert c.tf_lookback[TimeFrame.H4] == 720
    # dokunulmayan TF varsayılan kalır
    assert c.tf_lookback[TimeFrame.M15] == 336


def test_load_config_unknown_keys_ignored(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("totally_unknown_key: 123\nswing_lookback: 9\n")
    c = load_config(p)
    assert c.swing_lookback == 9
    assert not hasattr(c, "totally_unknown_key")


def test_load_config_empty_file(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("")
    c = load_config(p)
    assert c.swing_lookback == 4


# ---------------- Faz 3 review fix: yeni SMCConfig tuning alanlari ----------------


def test_smcconfig_atr_period_default():
    assert SMCConfig().atr_period == 14


def test_smcconfig_tuning_field_defaults():
    c = SMCConfig()
    assert c.tp_r_multiples == (1.5, 2.62, 4.23)
    assert c.tp_weights == (0.5, 0.3, 0.2)
    assert sum(c.tp_weights) == pytest.approx(1.0)
    assert c.ote_low == 0.618
    assert c.ote_high == 0.786
    assert c.sl_band_buffer_mult == 0.25
    assert c.sl_abs_buffer_pct == 0.003
    assert c.cluster_tolerance_pct == 0.02


def test_smcconfig_quality_map_defaults():
    c = SMCConfig()
    assert c.poi_kind_quality == {"ZONE": 1.0, "LEVEL": 0.6, "IMBALANCE": 0.5}
    assert c.zone_status_factor == {
        "FRESH": 1.0, "TESTED": 0.7, "MITIGATED": 0.3, "BROKEN": 0.0,
    }


def test_smcconfig_quality_maps_independent_instances():
    """default_factory -> her instance kendi dict'ine sahip (paylasilmaz)."""
    a, b = SMCConfig(), SMCConfig()
    a.poi_kind_quality["ZONE"] = 9.9
    assert b.poi_kind_quality["ZONE"] == 1.0


def test_load_config_atr_period_override(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("atr_period: 21\n")
    c = load_config(p)
    assert c.atr_period == 21


def test_load_config_tuple_scalar_override(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent("""
        tp_r_multiples: [1.0, 2.0, 3.0]
        tp_weights: [0.6, 0.25, 0.15]
        ote_low: 0.5
        sl_band_buffer_mult: 0.4
        cluster_tolerance_pct: 0.05
    """))
    c = load_config(p)
    assert c.tp_r_multiples == (1.0, 2.0, 3.0)
    assert c.tp_weights == (0.6, 0.25, 0.15)
    assert c.ote_low == 0.5
    assert c.sl_band_buffer_mult == 0.4
    assert c.cluster_tolerance_pct == 0.05


def test_load_config_poi_kind_quality_override(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent("""
        poi_kind_quality:
          ZONE: 0.9
          IMBALANCE: 0.2
    """))
    c = load_config(p)
    assert c.poi_kind_quality["ZONE"] == 0.9
    assert c.poi_kind_quality["IMBALANCE"] == 0.2
    # dokunulmayan anahtar varsayilan kalir
    assert c.poi_kind_quality["LEVEL"] == 0.6


def test_load_config_zone_status_factor_override(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent("""
        zone_status_factor:
          TESTED: 0.5
    """))
    c = load_config(p)
    assert c.zone_status_factor["TESTED"] == 0.5
    assert c.zone_status_factor["FRESH"] == 1.0
