"""``analyze_combined.py`` — join signals.jsonl with trades.jsonl (Spec §13.2).

Maps each ValidatedSetup (from signals-YYYYMMDD.jsonl) to its order lifecycle
events (from trades-YYYYMMDD.jsonl) via signal_at_bar / at_bar field.

Usage:
    python scripts/analyze_combined.py --date 2026-05-18
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def load_signals(signals_dir: Path, day: date) -> list[dict]:
    path = signals_dir / f"signals-{day.strftime('%Y%m%d')}.jsonl"
    if not path.exists():
        return []
    out: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


def load_trade_events(trades_dir: Path, day: date) -> list[dict]:
    path = trades_dir / f"trades-{day.strftime('%Y%m%d')}.jsonl"
    if not path.exists():
        return []
    out: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", type=_parse_date,
                   default=datetime.now(tz=timezone.utc).date())
    p.add_argument("--signals-dir", default="logs")
    p.add_argument("--trades-dir", default="logs/trades")
    args = p.parse_args(argv)

    signals = load_signals(Path(args.signals_dir), args.date)
    trades = load_trade_events(Path(args.trades_dir), args.date)

    # Group trade events by at_bar
    trades_by_bar: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        bar = t.get("at_bar")
        if bar:
            trades_by_bar[bar].append(t)

    print(f"=== Combined Signal + Trade Report for {args.date} ===\n")
    print(f"Signals: {len(signals)}  Trade events: {len(trades)}\n")

    for s in signals:
        kind = s.get("kind", "?")
        sym = s.get("symbol", "?")
        at_bar = s.get("at_bar", "?")
        setup = s.get("setup", {})

        print(f"[{at_bar}] {sym} {kind}", end="")
        if kind == "validated_setup":
            print(f" dir={setup.get('direction')} entry={setup.get('entry')} "
                  f"rr={setup.get('rr')} conf={setup.get('confluence_score')}")
        elif kind == "rejection":
            print(f" gate={s.get('gate')} reason={s.get('reason')}")
        else:
            print()

        # Match trade events
        for t in trades_by_bar.get(at_bar, []):
            print(f"    → {t.get('event')} {t.get('order_id', '')}", end="")
            if t.get("event") in ("TP_HIT", "SL_HIT"):
                print(f" pnl=${t.get('pnl_dollar'):+.2f}")
            elif t.get("event") == "ORDER_FILLED":
                print(f" fill_price={t.get('fill_price')} slippage={t.get('slippage')}")
            elif t.get("event") == "ORDER_PLACED":
                print(f" price={t.get('price')} size={t.get('qty')}")
            else:
                print()
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
