"""``reconcile_check.py`` — manual reconcile invocation (Spec §9.2).

Read-only by default; --fix opens interactive prompt per drift.

Usage:
    python scripts/reconcile_check.py
    python scripts/reconcile_check.py --fix
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# .env yükle (HCLOUD_API_KEY ya da BINANCE keys)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from smc_engine.config import SMCConfig, load_config  # noqa: E402
from smc_engine.execution.audit_log import AuditLog  # noqa: E402
from smc_engine.execution.kill_switch import KillSwitch  # noqa: E402
from smc_engine.execution.position_tracker import PositionTracker  # noqa: E402
from smc_engine.execution.reconcile import ReconcileLoop  # noqa: E402
from smc_engine.integrations.binance.order_client import BinanceOrderClient  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--fix", action="store_true",
                   help="Interactive fix mode (5A: prompt-only, no auto-fix)")
    p.add_argument("--symbol", help="Single symbol scope")
    args = p.parse_args(argv)

    cfg = load_config(Path("config.yaml")) if Path("config.yaml").exists() else SMCConfig()
    if not cfg.execution_enabled:
        print("[reconcile_check] config.execution.enabled is False — nothing to do")
        return 0

    audit_dir = Path(cfg.execution_audit_log_dir)
    state_dir = Path(cfg.execution_state_dir)

    audit = AuditLog(log_dir=str(audit_dir), engine_sha="reconcile_check",
                     testnet=cfg.execution_testnet, phase=cfg.execution_phase)
    ks = KillSwitch(
        consecutive_loss_threshold=cfg.execution_kill_switch_consecutive_losses,
        daily_loss_threshold=cfg.execution_kill_switch_daily_loss_dollar,
        equity_minimum=cfg.execution_kill_switch_equity_minimum,
        state_path=state_dir / "kill_switch_state.json",
        audit_log=audit,
    )

    symbols = [args.symbol] if args.symbol else cfg.execution_symbols
    client = BinanceOrderClient(
        api_key=os.environ.get("BINANCE_API_KEY", ""),
        api_secret=os.environ.get("BINANCE_API_SECRET", ""),
        testnet=cfg.execution_testnet,
        rate_limit_buffer=cfg.binance_rate_limit_buffer,
        config=cfg,
    )

    any_drift = False
    for sym in symbols:
        tracker = PositionTracker()
        state_file = state_dir / f"positions-{sym}.json"
        tracker.load_state(state_file)
        rl = ReconcileLoop(
            order_client=client, position_tracker=tracker,
            audit_log=audit, kill_switch=ks,
        )
        before = ks.is_triggered()
        rl.tick()
        after = ks.is_triggered()
        if after and not before:
            any_drift = True
            print(f"[reconcile_check] DRIFT for {sym} — kill_switch triggered, audited")
        else:
            print(f"[reconcile_check] {sym}: no drift")

    if args.fix and any_drift:
        print()
        print("--fix mode: interactive resolution not yet implemented (5A).")
        print("Manual options:")
        print("  1. Inspect drift in logs/trades-YYYYMMDD.jsonl")
        print("  2. Delete state file: rm -f logs/state/positions-*.json")
        print("  3. Reset kill switch: scripts/kill_switch_reset.sh")
        print("  4. Restart service: sudo systemctl restart smc-engine")

    return 0 if not any_drift else 1


if __name__ == "__main__":
    raise SystemExit(main())
