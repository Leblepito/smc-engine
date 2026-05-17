"""Manual kill switch reset (Spec §4.6).

Usage:
    python -m smc_engine.execution.kill_switch_reset
    # or
    python scripts/kill_switch_reset.py

Shows current state, asks confirmation, then resets.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make smc_engine importable when invoked as a plain script
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from smc_engine.config import SMCConfig, load_config  # noqa: E402
from smc_engine.execution.audit_log import AuditLog  # noqa: E402
from smc_engine.execution.kill_switch import KillSwitch  # noqa: E402


def _show_state(state_path: Path) -> None:
    if not state_path.exists():
        print(f"[kill_switch_reset] no state file at {state_path} — nothing to reset")
        return
    data = json.loads(state_path.read_text(encoding="utf-8"))
    s = data.get("state", {})
    print(f"Current kill switch state ({state_path}):")
    print(f"  triggered:           {s.get('triggered')}")
    print(f"  consecutive_losses:  {s.get('consecutive_losses')}")
    print(f"  daily_pnl:           {s.get('daily_pnl')}")
    print(f"  triggered_at:        {s.get('triggered_at')}")
    print(f"  triggered_reasons:   {s.get('triggered_reasons')}")


def main() -> int:
    cfg = load_config(Path("config.yaml")) if Path("config.yaml").exists() else SMCConfig()
    state_dir = Path(cfg.execution_state_dir)
    state_path = state_dir / "kill_switch_state.json"
    audit_dir = Path(cfg.execution_audit_log_dir)

    _show_state(state_path)
    print()
    answer = input("Reset kill switch? (yes/no): ").strip().lower()
    if answer != "yes":
        print("[kill_switch_reset] aborted")
        return 1

    audit = AuditLog(
        log_dir=str(audit_dir),
        engine_sha="manual-reset",
        testnet=cfg.execution_testnet,
        phase=cfg.execution_phase,
    )
    ks = KillSwitch(
        consecutive_loss_threshold=cfg.execution_kill_switch_consecutive_losses,
        daily_loss_threshold=cfg.execution_kill_switch_daily_loss_dollar,
        equity_minimum=cfg.execution_kill_switch_equity_minimum,
        state_path=state_path,
        audit_log=audit,
    )
    ks.reset()
    print("[kill_switch_reset] OK — kill switch cleared, audit logged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
