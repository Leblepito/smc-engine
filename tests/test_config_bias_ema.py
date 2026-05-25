"""SMCConfig bias EMA trend alanları (Spec §Configuration, 2026-05-24)."""
from smc_engine.config import SMCConfig


def test_smcconfig_has_bias_use_d1_ema_trend_default_false():
    """Default KAPALI — 2026-05-25 validation: P3 iyilesir ama P2 bozulur,
    cross-window kazanim yok; opt-in feature flag.
    Bkz: project_bias_fix_validation_2026_05_25.md
    """
    cfg = SMCConfig()
    assert cfg.bias_use_d1_ema_trend is False


def test_smcconfig_has_bias_d1_ema_period_default_50():
    cfg = SMCConfig()
    assert cfg.bias_d1_ema_period == 50


def test_smcconfig_bias_ema_fields_overridable():
    cfg = SMCConfig()
    cfg.bias_use_d1_ema_trend = False
    cfg.bias_d1_ema_period = 100
    assert cfg.bias_use_d1_ema_trend is False
    assert cfg.bias_d1_ema_period == 100
