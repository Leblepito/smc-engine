"""SMC Engine canlı sinyal CLI — Sub-proje #2 (log-only) + #5A (execution opt-in).

Kullanım (log-only, sub-proje #2):
    python examples/run_live.py --symbols BTCUSDT,ETHUSDT,SOLUSDT --equity 10000

Kullanım (execution, sub-proje #5A testnet):
    python examples/run_live.py --config config.yaml --execution-enabled

Akış (Spec §3):
    APScheduler M15:05 → BinanceAdapter.fetch_ohlcv (D1, H4, M15)
        → orchestrator.analyze (at_bar=last_closed_M15, htf_cache)
        → setup_builder.build → risk_guard.validate
        → SignalLogger.emit → logs/signals-YYYYMMDD.jsonl + stdout
        → (opt-in) OrderManager.process_setup → logs/trades-YYYYMMDD.jsonl

Mainnet 3 katman guard:
  1) env SMC_ALLOW_LIVE=1
  2) config.execution.live_enabled=true
  3) startup 5sn delay + WARNING
"""

from __future__ import annotations

import argparse
import logging
import os
import signal as _signal
import sys
import time
from pathlib import Path

# .env yükle (varsa)
try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv()
except Exception:
    pass

from smc_engine.config import SMCConfig, load_config
from smc_engine.integrations.binance.adapter import BinanceAdapter
from smc_engine.integrations.binance.client import BinanceClient
from smc_engine.live.runner import LiveRunner
from smc_engine.live.scheduler import LiveScheduler
from smc_engine.live.signal_logger import SignalLogger
from smc_engine.types import TimeFrame


def _git_sha() -> str:
    """Best-effort current commit sha (audit log için)."""
    import subprocess
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _build_parser() -> argparse.ArgumentParser:
    """Argparse parser builder — test edilebilir olsun diye ayrı."""
    cfg_defaults = SMCConfig()
    p = argparse.ArgumentParser(
        description="SMC Engine live signal pipeline (config-driven; log-only OR execution)"
    )
    p.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml (default: ./config.yaml). Missing = SMCConfig defaults.",
    )
    p.add_argument(
        "--symbols",
        default=None,  # None → config'den al
        help="Comma-separated symbol list. Overrides config.live_symbols + config.execution_symbols.",
    )
    p.add_argument(
        "--equity",
        type=float,
        default=None,
        help="Account equity for R-sizing (display only in log-only mode). Overrides config.",
    )
    p.add_argument(
        "--log-dir",
        default=None,
        help="JSONL log directory (auto-created). Overrides config.live_log_dir.",
    )
    p.add_argument(
        "--testnet",
        action="store_true",
        help="(legacy) Use Binance futures testnet for sub-proje #2 read path.",
    )
    p.add_argument(
        "--buffer-seconds",
        type=int,
        default=None,
        help="Seconds after M15 close before tick fires. Overrides config.",
    )
    p.add_argument(
        "--execution-enabled",
        action="store_true",
        help="Force-enable order execution (Sub-proje #5A). "
             "Overrides config.execution.enabled. If neither this flag nor config "
             "enables it, runner stays in log-only mode.",
    )
    return p


def _parse_args_with(argv: list[str]) -> argparse.Namespace:
    """argparse helper for tests + main()."""
    return _build_parser().parse_args(argv)


def _validate_execution_config(config: SMCConfig) -> None:
    """Sanity check before bringing up execution stack.

    Combo guard: testnet=False + live_enabled=False = senseless config
    (mainnet talep ediliyor ama 3-layer guard'ın 2. katmanı kapalı).
    MainnetGuard zaten testnet'e düşürür ama bu sessiz override
    confusing'tir; explicit error daha net.
    """
    if not config.execution_enabled:
        return  # execution off — diğer alanlar önemsiz
    if not config.execution_testnet and not config.execution_live_enabled:
        raise RuntimeError(
            "Geçersiz execution config: testnet=False (mainnet) + "
            "live_enabled=False. Bu kombinasyon anlamsız — mainnet "
            "guard'ın 2. katmanı kapalı olduğu için MainnetGuard zaten "
            "TESTNET zorlardı ama explicit yanlış config'i bildirmek "
            "daha güvenli. Çözüm: ya testnet=True yap (testnet smoke), "
            "ya da live_enabled=True yap (mainnet smoke, SMC_ALLOW_LIVE=1 + "
            "startup delay)."
        )


def _resolve_config(args: argparse.Namespace) -> SMCConfig:
    """config.yaml + CLI override → effective SMCConfig."""
    config_path = Path(args.config)
    config = load_config(config_path) if config_path.exists() else SMCConfig()

    # CLI override priority: arg > config > default
    if args.symbols is not None:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        # Symbols hem live (signal logger) hem execution (per-symbol stack) için
        config.live_symbols = symbols
        config.execution_symbols = symbols
    if args.equity is not None:
        config.live_account_equity = args.equity
    if args.log_dir is not None:
        config.live_log_dir = args.log_dir
    if args.buffer_seconds is not None:
        config.live_scheduler_buffer_seconds = args.buffer_seconds
    if args.testnet:
        # legacy sub-proje #2 testnet (read-only). Sub-proje #5A için
        # execution_testnet kullanılır — config'den okunur, --testnet bağımsız.
        config.binance_testnet = True
    # CLI'da --execution-enabled True ise config'i override (config False olabilir)
    # False ise (default), config'deki değeri korur.
    if args.execution_enabled:
        config.execution_enabled = True

    return config


def main(argv: list[str] | None = None) -> int:
    args = _parse_args_with(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("smc.live")

    config = _resolve_config(args)
    _validate_execution_config(config)

    symbols = config.live_symbols
    if not symbols:
        log.error("En az bir sembol gerekli: config.live_symbols veya --symbols")
        return 2

    log.info(
        "starting live pipeline: symbols=%s log_dir=%s execution_enabled=%s "
        "execution_testnet=%s execution_live_enabled=%s",
        symbols, config.live_log_dir, config.execution_enabled,
        config.execution_testnet, config.execution_live_enabled,
    )

    # API key uyarı (sub-proje #2 read path için)
    if not os.environ.get("BINANCE_API_KEY") or not os.environ.get("BINANCE_API_SECRET"):
        log.warning(
            "BINANCE_API_KEY / BINANCE_API_SECRET set değil — sub-proje #2 "
            "read path public-only modda (kline endpoint'leri çoğunlukla public)."
        )

    client = BinanceClient(
        testnet=config.binance_testnet,
        rate_limit_buffer=config.binance_rate_limit_buffer,
    )
    adapter = BinanceAdapter(client=client)

    # Sub-proje #5A: execution stack opt-in
    order_manager_per_symbol: dict[str, object] = {}
    audit_log = None
    if config.execution_enabled:
        from smc_engine.execution.audit_log import AuditLog
        from smc_engine.execution.kill_switch import KillSwitch
        from smc_engine.execution.mainnet_guard import MainnetGuard, MainnetMode
        from smc_engine.execution.order_manager import OrderManager
        from smc_engine.execution.position_tracker import PositionTracker
        from smc_engine.integrations.binance.order_client import BinanceOrderClient

        # Mainnet guard ON-START — 3 katman; geçemezse TESTNET zorla
        mainnet_mode = MainnetGuard.check(config)
        use_testnet = mainnet_mode is MainnetMode.TESTNET
        log.info("execution mainnet guard: mode=%s use_testnet=%s",
                 mainnet_mode.value, use_testnet)

        # from_env: testnet → BINANCE_TESTNET_API_KEY/SECRET,
        # mainnet → BINANCE_API_KEY/SECRET (otomatik)
        order_client = BinanceOrderClient.from_env(
            testnet=use_testnet,
            rate_limit_buffer=config.binance_rate_limit_buffer,
            config=config,
        )
        audit_log = AuditLog(
            log_dir=config.execution_audit_log_dir,
            engine_sha=_git_sha(),
            testnet=use_testnet,
            phase=config.execution_phase,
        )
        kill_switch = KillSwitch(
            consecutive_loss_threshold=config.execution_kill_switch_consecutive_losses,
            daily_loss_threshold=config.execution_kill_switch_daily_loss_dollar,
            equity_minimum=config.execution_kill_switch_equity_minimum,
            state_path=Path(config.execution_state_dir) / "kill_switch_state.json",
            audit_log=audit_log,
        )
        log.info(
            "execution stack initialized: order_client=BinanceOrderClient(testnet=%s) "
            "kill_switch(consec_losses=%d, daily_loss=$%.2f, equity_min=$%.2f)",
            use_testnet,
            config.execution_kill_switch_consecutive_losses,
            config.execution_kill_switch_daily_loss_dollar,
            config.execution_kill_switch_equity_minimum,
        )

        for sym in symbols:
            tracker = PositionTracker()
            state_file = Path(config.execution_state_dir) / f"positions-{sym}.json"
            tracker.load_state(state_file)
            order_manager_per_symbol[sym] = OrderManager(
                order_client=order_client,
                position_tracker=tracker,
                audit_log=audit_log,
                kill_switch=kill_switch,
                config=config,
            )
        log.info("OrderManager initialized for %d symbol(s): %s",
                 len(symbols), symbols)

    loggers: dict[str, SignalLogger] = {
        sym: SignalLogger(log_dir=config.live_log_dir, symbol=sym, timeframe=TimeFrame.M15)
        for sym in symbols
    }
    runners: dict[str, LiveRunner] = {
        sym: LiveRunner(
            adapter=adapter,
            config=config,
            signal_logger=loggers[sym],
            order_manager=order_manager_per_symbol.get(sym),
        )
        for sym in symbols
    }

    scheduler = LiveScheduler(buffer_seconds=config.live_scheduler_buffer_seconds)

    def _tick():
        for sym, runner in runners.items():
            try:
                runner.run_once(sym)
            except Exception as exc:
                log.error("tick failed for %s: %s", sym, exc)
        if config.execution_enabled:
            for sym, om in order_manager_per_symbol.items():
                try:
                    om.tick_timeout_watcher()
                    om.tick_fill_polling()
                except Exception as exc:
                    log.error("execution tick failed for %s: %s", sym, exc)

    scheduler.start(_tick)
    log.info("scheduler started; M15 kapanış + %ds tetiklenecek. Ctrl+C ile durdur.",
             config.live_scheduler_buffer_seconds)

    stop_requested = {"v": False}

    def _on_sigint(signum, frame):
        log.info("SIGINT alındı, shutdown başlıyor...")
        stop_requested["v"] = True

    _signal.signal(_signal.SIGINT, _on_sigint)
    try:
        while not stop_requested["v"]:
            time.sleep(1)
    finally:
        scheduler.stop()
        for lg in loggers.values():
            lg.close()
        try:
            adapter.close()
        except Exception:
            pass
        log.info("temiz çıkış.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
