from smc_engine.config import SMCConfig, load_config


def test_smcconfig_has_atr_regime_filter_fields_with_defaults():
    """SMCConfig 3 yeni alan icermeli (production default: filter on, p80, 96 bar)."""
    cfg = SMCConfig()
    assert cfg.atr_percentile_window == 96
    assert cfg.atr_percentile_threshold == 0.80
    assert cfg.atr_regime_filter_enabled is True


def test_smcconfig_atr_regime_yaml_override(tmp_path):
    """YAML scalar override (test_config_sl_params_yaml_override pattern)."""
    p = tmp_path / "config.yaml"
    p.write_text(
        "atr_percentile_window: 120\n"
        "atr_percentile_threshold: 0.70\n"
        "atr_regime_filter_enabled: false\n"
    )
    cfg = load_config(p)
    assert cfg.atr_percentile_window == 120
    assert cfg.atr_percentile_threshold == 0.70
    assert cfg.atr_regime_filter_enabled is False
