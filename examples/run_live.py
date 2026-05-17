"""SMC Engine — Sub-proje #2 canlı sinyal CLI (log-only mod).

Kullanım:
    python examples/run_live.py --symbols BTCUSDT,ETHUSDT,SOLUSDT --equity 10000

Akış (Spec §3):
    APScheduler M15:05 → BinanceAdapter.fetch_ohlcv (D1, H4, M15)
        → orchestrator.analyze (at_bar=last_closed_M15, htf_cache)
        → setup_builder.build → risk_guard.validate
        → SignalLogger.emit → logs/signals-YYYYMMDD.jsonl + stdout

#5'te emir gönderme eklenecek; bu CLI'de ``--live`` flag bilinçli olarak YOK.
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

    log.info("starting live pipeline: symbols=%s equity=%.2f log_dir=%s testnet=%s",
             symbols, args.equity, args.log_dir, args.testnet)

    client = BinanceClient(
        testnet=args.testnet, rate_limit_buffer=config.binance_rate_limit_buffer
    )
    adapter = BinanceAdapter(client=client)

    # Her sembol için ayrı logger (dosya adı aynı; symbol payload'da)
    # — burada tek logger kullanıp sembolü emit edilen payload'da gösteriyoruz.
    # Runner emit içinde sembol bilinmediği için per-symbol logger kullanmak daha temiz.
    loggers: dict[str, SignalLogger] = {
        sym: SignalLogger(log_dir=args.log_dir, symbol=sym, timeframe=TimeFrame.M15)
        for sym in symbols
    }

    runners: dict[str, LiveRunner] = {
        sym: LiveRunner(adapter=adapter, config=config, signal_logger=loggers[sym])
        for sym in symbols
    }

    scheduler = LiveScheduler(buffer_seconds=args.buffer_seconds)

    def _tick():
        for sym, runner in runners.items():
            try:
                runner.run_once(sym)
            except Exception as exc:
                log.error("tick failed for %s: %s", sym, exc)

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
