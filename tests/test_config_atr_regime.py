def test_smcconfig_has_atr_regime_filter_fields_with_defaults():
    """SMCConfig 3 yeni alan icermeli (production default: filter on, p80, 96 bar)."""
    from smc_engine.config import SMCConfig
    cfg = SMCConfig()
    assert cfg.atr_percentile_window == 96
    assert cfg.atr_percentile_threshold == 0.80
    assert cfg.atr_regime_filter_enabled is True


def test_smcconfig_atr_regime_yaml_override():
    """YAML scalar override (test_config_sl_params_yaml_override pattern)."""
    import os
    import tempfile
    from smc_engine.config import load_config
    yaml_content = (
        "atr_percentile_window: 120\n"
        "atr_percentile_threshold: 0.70\n"
        "atr_regime_filter_enabled: false\n"
    )
    fd, path = tempfile.mkstemp(suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(yaml_content)
        cfg = load_config(path)
        assert cfg.atr_percentile_window == 120
        assert cfg.atr_percentile_threshold == 0.70
        assert cfg.atr_regime_filter_enabled is False
    finally:
        os.unlink(path)
