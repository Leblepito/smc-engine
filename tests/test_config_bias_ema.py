"""SMCConfig bias EMA trend alanları (Spec §Configuration, 2026-05-24)."""
from smc_engine.config import SMCConfig


def test_smcconfig_has_bias_use_d1_ema_trend_default_true():
    cfg = SMCConfig()
    assert cfg.bias_use_d1_ema_trend is True


def test_smcconfig_has_bias_d1_ema_period_default_50():
    cfg = SMCConfig()
    assert cfg.bias_d1_ema_period == 50


def test_smcconfig_bias_ema_fields_overridable():
    cfg = SMCConfig()
    cfg.bias_use_d1_ema_trend = False
    cfg.bias_d1_ema_period = 100
    assert cfg.bias_use_d1_ema_trend is False
    assert cfg.bias_d1_ema_period == 100
