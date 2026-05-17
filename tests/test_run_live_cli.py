"""examples/run_live.py CLI argparse + config loading testleri (X6 fix)."""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest


def _load_run_live_module():
    """examples/run_live.py'yi modül olarak yükle."""
    repo_root = Path(__file__).resolve().parent.parent
    spec_path = repo_root / "examples" / "run_live.py"
    spec = importlib.util.spec_from_file_location("run_live_cli", spec_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# --config flag (X6 fix)
# ============================================================


def test_parser_has_config_flag():
    mod = _load_run_live_module()
    parser = mod._build_parser() if hasattr(mod, "_build_parser") else None
    if parser is None:
        # parse_args yapısı kullanılıyorsa argparse object'i içeride
        # — argv geçirerek test edelim
        ns = mod._parse_args_with(["--config", "/tmp/foo.yaml"])
        assert ns.config == "/tmp/foo.yaml"
        return
    args = parser.parse_args(["--config", "/tmp/foo.yaml"])
    assert args.config == "/tmp/foo.yaml"


def test_parser_config_default_is_config_yaml():
    """Default: ./config.yaml"""
    mod = _load_run_live_module()
    ns = mod._parse_args_with([])
    assert ns.config == "config.yaml"


# ============================================================
# Config loading + CLI override
# ============================================================


def test_load_config_yaml_picks_execution_block(tmp_path, monkeypatch):
    """config.yaml'da execution.enabled: true → execution stack init edilir."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(textwrap.dedent("""
        execution:
          enabled: true
          phase: "5A"
          testnet: true
          live_enabled: false
          symbols: [BTCUSDT]
    """))
    from smc_engine.config import load_config
    cfg = load_config(config_path)
    assert cfg.execution_enabled is True
    assert cfg.execution_testnet is True
    assert cfg.execution_live_enabled is False
    assert cfg.execution_symbols == ["BTCUSDT"]


def test_cli_execution_flag_overrides_config_false(tmp_path):
    """CLI'da --execution-enabled True ise, config'de false olsa bile override eder."""
    mod = _load_run_live_module()
    ns = mod._parse_args_with(["--execution-enabled"])
    assert ns.execution_enabled is True


def test_cli_no_execution_flag_uses_config(tmp_path):
    """CLI'da flag yoksa, config.execution.enabled değeri kullanılır.
    Bu test sadece flag default'unu doğrular; load logic CLI main'de."""
    mod = _load_run_live_module()
    ns = mod._parse_args_with([])
    # argparse default: action="store_true" → False
    assert ns.execution_enabled is False


# ============================================================
# Config combo guard — live_enabled=false + testnet=false
# ============================================================


def test_invalid_combo_live_disabled_with_mainnet_raises():
    """testnet=false (mainnet istenir) ama live_enabled=false → bilinçli yanlış config."""
    from smc_engine.config import SMCConfig
    cfg = SMCConfig()
    cfg.execution_enabled = True
    cfg.execution_testnet = False
    cfg.execution_live_enabled = False

    mod = _load_run_live_module()
    with pytest.raises(RuntimeError, match="live_enabled"):
        mod._validate_execution_config(cfg)


def test_valid_testnet_combo_passes():
    """testnet=true + live_enabled=false → geçerli (testnet smoke yolu)."""
    from smc_engine.config import SMCConfig
    cfg = SMCConfig()
    cfg.execution_enabled = True
    cfg.execution_testnet = True
    cfg.execution_live_enabled = False
    mod = _load_run_live_module()
    mod._validate_execution_config(cfg)  # raise etmemeli


def test_valid_mainnet_combo_passes():
    """testnet=false + live_enabled=true → geçerli (mainnet smoke yolu, env+startup delay)."""
    from smc_engine.config import SMCConfig
    cfg = SMCConfig()
    cfg.execution_enabled = True
    cfg.execution_testnet = False
    cfg.execution_live_enabled = True
    mod = _load_run_live_module()
    mod._validate_execution_config(cfg)  # raise etmemeli


def test_execution_disabled_skips_validation():
    """execution.enabled=false → testnet/live_enabled kombinasyonu önemsiz."""
    from smc_engine.config import SMCConfig
    cfg = SMCConfig()
    cfg.execution_enabled = False
    cfg.execution_testnet = False
    cfg.execution_live_enabled = False
    mod = _load_run_live_module()
    # raise etmemeli — execution kapalı, validation skip
    mod._validate_execution_config(cfg)
