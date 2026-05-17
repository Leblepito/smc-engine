"""SMC Engine canlı sinyal CLI — Sub-proje #2 (log-only) + #5A (execution opt-in).

Kullanım (log-only, sub-proje #2):
    python examples/run_live.py --symbols BTCUSDT,ETHUSDT,SOLUSDT --equity 10000

Kullanım (execution, sub-proje #5A testnet):
    SMC_ALLOW_LIVE=0 python examples/run_live.py --execution-enabled --symbols BTCUSDT

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

from smc_engine.config import SMCConfig
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


def _parse_args() -> argparse.Namespace:
    cfg_defaults = SMCConfig()
    p = argparse.ArgumentParser(description="SMC Engine live signal pipeline (log-only)")
    p.add_argument(
        "--symbols",
        default=",".join(cfg_defaults.live_symbols),
        help="Comma-separated symbol list (e.g. BTCUSDT,ETHUSDT)",
    )
    p.add_argument(
        "--equity",
        type=float,
        default=cfg_defaults.live_account_equity,
        help="Account equity for R-sizing (log-only mode = static)",
    )
    p.add_argument(
        "--log-dir",
        default=cfg_defaults.live_log_dir,
        help="JSONL log directory (auto-created)",
    )
    p.add_argument(
        "--testnet",
        action="store_true",
        help="Use Binance futures testnet (default: mainnet read-only)",
    )
    p.add_argument(
        "--buffer-seconds",
        type=int,
        default=cfg_defaults.live_scheduler_buffer_seconds,
        help="Seconds after M15 close before tick fires",
    )
    # Sub-proje #5A execution opt-in. Default False — runner sub-proje #2
    # (log-only) davranışında kalır.
    p.add_argument(
        "--execution-enabled",
        action="store_true",
        help="Enable order execution (Sub-proje #5A). Default: log-only.",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("smc.live")

    # API key kontrolü (public futures klines için zorunlu DEĞİL ama uyar)
    if not os.environ.get("BINANCE_API_KEY") or not os.environ.get("BINANCE_API_SECRET"):
        log.warning(
            "BINANCE_API_KEY / BINANCE_API_SECRET set değil — public-only modda "
            "çalışılacak (futures kline endpoint'leri çoğunlukla public). .env "
            "dosyasına read-only key koymak rate-limit avantajı sağlar."
        )

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        log.error("En az bir sembol gerekli: --symbols BTCUSDT,...")
        return 2

    config = SMCConfig()
    config.live_symbols = symbols
    config.live_account_equity = args.equity
    config.live_log_dir = args.log_dir
    config.binance_testnet = args.testnet
    config.live_scheduler_buffer_seconds = args.buffer_seconds
    config.execution_enabled = args.execution_enabled

    log.info("starting live pipeline: symbols=%s equity=%.2f log_dir=%s testnet=%s execution=%s",
             symbols, args.equity, args.log_dir, args.testnet, args.execution_enabled)

    client = BinanceClient(
        testnet=args.testnet, rate_limit_buffer=config.binance_rate_limit_buffer
    )
    adapter = BinanceAdapter(client=client)

    # Sub-proje #5A: execution stack opt-in
    order_manager_per_symbol: dict[str, object] = {}
    audit_log = None
    if args.execution_enabled:
        from smc_engine.execution.audit_log import AuditLog
        from smc_engine.execution.kill_switch import KillSwitch
        from smc_engine.execution.mainnet_guard import MainnetGuard, MainnetMode
        from smc_engine.execution.order_manager import OrderManager
        from smc_engine.execution.position_tracker import PositionTracker
        from smc_engine.integrations.binance.order_client import BinanceOrderClient
        from pathlib import Path as _P

        # Mainnet guard ON-START — 3 katman; geçemezse TESTNET zorla
        mainnet_mode = MainnetGuard.check(config)
        use_testnet = mainnet_mode is MainnetMode.TESTNET
        log.info("execution mainnet guard: mode=%s use_testnet=%s",
                 mainnet_mode.value, use_testnet)

        order_client = BinanceOrderClient(
            api_key=os.environ.get("BINANCE_API_KEY", ""),
            api_secret=os.environ.get("BINANCE_API_SECRET", ""),
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
            state_path=_P(config.execution_state_dir) / "kill_switch_state.json",
            audit_log=audit_log,
        )

        for sym in symbols:
            tracker = PositionTracker()
            state_file = _P(config.execution_state_dir) / f"positions-{sym}.json"
            tracker.load_state(state_file)
            order_manager_per_symbol[sym] = OrderManager(
                order_client=order_client,
                position_tracker=tracker,
                audit_log=audit_log,
                kill_switch=kill_switch,
                config=config,
            )

    # Her sembol için ayrı logger (dosya adı aynı; symbol payload'da)
    # — burada tek logger kullanıp sembolü emit edilen payload'da gösteriyoruz.
    # Runner emit içinde sembol bilinmediği için per-symbol logger kullanmak daha temiz.
    loggers: dict[str, SignalLogger] = {
        sym: SignalLogger(log_dir=args.log_dir, symbol=sym, timeframe=TimeFrame.M15)
        for sym in symbols
    }

    runners: dict[str, LiveRunner] = {
        sym: LiveRunner(
            adapter=adapter,
            config=config,
            signal_logger=loggers[sym],
            order_manager=order_manager_per_symbol.get(sym),  # None ise log-only
        )
        for sym in symbols
    }

    scheduler = LiveScheduler(buffer_seconds=args.buffer_seconds)

    def _tick():
        for sym, runner in runners.items():
            try:
                runner.run_once(sym)
            except Exception as exc:
                log.error("tick failed for %s: %s", sym, exc)
        # Sub-proje #5A: after each M15 setup tick, also run execution polling
        # (cheap; in-memory checks for any PENDING/ACTIVE orders).
        if args.execution_enabled:
            for sym, om in order_manager_per_symbol.items():
                try:
                    om.tick_timeout_watcher()
                    om.tick_fill_polling()
                except Exception as exc:
                    log.error("execution tick failed for %s: %s", sym, exc)

    scheduler.start(_tick)
    log.info("scheduler started; M15 kapanış + %ds tetiklenecek. Ctrl+C ile durdur.",
             args.buffer_seconds)

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
