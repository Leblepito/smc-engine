"""``analyze_trades.py`` — read-only daily trades-YYYYMMDD.jsonl özet CLI (Spec §13.1).

analyze_signals.py pattern'iyle simetrik. trades-*.jsonl event'lerini
aggregate eder, terminal-friendly özet basar.

Kullanım:
    python scripts/analyze_trades.py
    python scripts/analyze_trades.py --date 2026-05-18
    python scripts/analyze_trades.py --since 2026-05-17 --until 2026-05-25
    python scripts/analyze_trades.py --format markdown --out reports/week1.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class Event:
    event: str
    ts: datetime
    payload: dict


@dataclass
class TradeStats:
    placed: int = 0
    filled: int = 0
    timeouts: int = 0
    rejects: int = 0
    tp_hits: int = 0
    sl_hits: int = 0
    manual_closes: int = 0
    total_pnl: float = 0.0
    wins: list = field(default_factory=list)   # PnL values
    losses: list = field(default_factory=list)


@dataclass
class Summary:
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    source_files: list[Path] = field(default_factory=list)
    by_symbol: dict[str, TradeStats] = field(default_factory=dict)
    overall: TradeStats = field(default_factory=TradeStats)
    kill_switch_triggers: int = 0
    kill_switch_resets: int = 0
    reconcile_drifts: int = 0
    last_kill_switch_state: Optional[dict] = None


_FILENAME_RE = re.compile(r"^trades-(\d{4})(\d{2})(\d{2})\.jsonl$")


def _date_from_filename(name: str) -> Optional[date]:
    m = _FILENAME_RE.match(name)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def find_log_files(log_dir: Path, since: Optional[date], until: Optional[date]) -> list[Path]:
    p = Path(log_dir)
    if not p.exists():
        return []
    matches: list[tuple[date, Path]] = []
    for path in p.glob("trades-*.jsonl"):
        d = _date_from_filename(path.name)
        if d is None:
            continue
        if since is not None and d < since:
            continue
        if until is not None and d > until:
            continue
        matches.append((d, path))
    matches.sort()
    return [path for _, path in matches]


def load_events(path: Path) -> list[Event]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[Event] = []
    with p.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"[analyze_trades] skip invalid JSON {p.name}:{lineno}: {exc}",
                      file=sys.stderr)
                continue
            event = obj.get("event")
            ts_raw = obj.get("ts")
            if not event or not ts_raw:
                continue
            try:
                ts = datetime.fromisoformat(ts_raw)
            except (TypeError, ValueError):
                continue
            out.append(Event(event=event, ts=ts, payload=obj))
    return out


def aggregate(events: list[Event], symbol_filter: Optional[str] = None) -> Summary:
    if symbol_filter:
        events = [e for e in events if e.payload.get("symbol") == symbol_filter]

    s = Summary()
    for ev in events:
        sym = ev.payload.get("symbol", "")
        stats = s.by_symbol.setdefault(sym, TradeStats()) if sym else s.overall

        if ev.event == "ORDER_PLACED":
            stats.placed += 1
            s.overall.placed += 1
        elif ev.event == "ORDER_FILLED":
            stats.filled += 1
            s.overall.filled += 1
        elif ev.event == "ORDER_TIMEOUT":
            stats.timeouts += 1
            s.overall.timeouts += 1
        elif ev.event == "ORDER_REJECTED":
            stats.rejects += 1
            s.overall.rejects += 1
        elif ev.event == "TP_HIT":
            stats.tp_hits += 1
            s.overall.tp_hits += 1
            pnl = float(ev.payload.get("pnl_dollar", 0.0))
            stats.total_pnl += pnl
            s.overall.total_pnl += pnl
            stats.wins.append(pnl)
            s.overall.wins.append(pnl)
        elif ev.event == "SL_HIT":
            stats.sl_hits += 1
            s.overall.sl_hits += 1
            pnl = float(ev.payload.get("pnl_dollar", 0.0))
            stats.total_pnl += pnl
            s.overall.total_pnl += pnl
            stats.losses.append(pnl)
            s.overall.losses.append(pnl)
        elif ev.event == "MANUAL_CLOSE":
            stats.manual_closes += 1
            s.overall.manual_closes += 1
        elif ev.event == "KILL_SWITCH_TRIGGERED":
            s.kill_switch_triggers += 1
            s.last_kill_switch_state = ev.payload
        elif ev.event == "KILL_SWITCH_RESET":
            s.kill_switch_resets += 1
        elif ev.event == "RECONCILE_DRIFT":
            s.reconcile_drifts += 1

    return s


def _pct(n: int, d: int) -> str:
    return "0.0%" if not d else f"{n / d * 100:.1f}%"


def render(summary: Summary, fmt: str = "text") -> str:
    lines: list[str] = []
    hr = "─" * 59
    lines.append(hr)
    lines.append(" SMC Engine — Trade Report")
    period = "all"
    if summary.period_start and summary.period_end:
        period = f"{summary.period_start} → {summary.period_end}"
    src = f"{len(summary.source_files)} file(s)" if summary.source_files else "(none)"
    lines.append(f" Period: {period}  |  Source: {src}")
    lines.append(hr)
    lines.append("")

    o = summary.overall
    lines.append("OVERALL")
    lines.append(f"  Orders placed:        {o.placed}")
    lines.append(f"  Filled:               {o.filled}  ({_pct(o.filled, o.placed)})")
    lines.append(f"  Timeouts:             {o.timeouts}")
    lines.append(f"  Rejects:              {o.rejects}")
    closed = o.tp_hits + o.sl_hits + o.manual_closes
    lines.append("")
    lines.append(f"  Closed positions:     {closed}")
    lines.append(f"  Wins (TP):            {o.tp_hits}  ({_pct(o.tp_hits, closed)})")
    lines.append(f"  Losses (SL):          {o.sl_hits}  ({_pct(o.sl_hits, closed)})")
    lines.append(f"  Manual closes:        {o.manual_closes}")
    lines.append("")
    lines.append("PNL")
    lines.append(f"  Total PnL:           ${o.total_pnl:+.2f}")
    if o.wins:
        lines.append(f"  Avg win:             ${sum(o.wins)/len(o.wins):+.2f}")
    if o.losses:
        lines.append(f"  Avg loss:            ${sum(o.losses)/len(o.losses):+.2f}")
    lines.append("")
    lines.append("BY SYMBOL")
    if not summary.by_symbol:
        lines.append("  (no symbols)")
    else:
        lines.append("  Symbol    Placed Filled  Wins  Losses   PnL")
        for sym in sorted(summary.by_symbol):
            st = summary.by_symbol[sym]
            lines.append(
                f"  {sym:<8}  {st.placed:6d} {st.filled:6d}  {st.tp_hits:4d}  {st.sl_hits:6d}  ${st.total_pnl:+7.2f}"
            )
    lines.append("")
    lines.append("KILL SWITCH")
    lines.append(f"  Triggers:             {summary.kill_switch_triggers}")
    lines.append(f"  Resets:               {summary.kill_switch_resets}")
    if summary.last_kill_switch_state:
        reasons = summary.last_kill_switch_state.get("reasons", [])
        lines.append(f"  Last triggered:       {reasons}")
    lines.append("")
    lines.append("RECONCILE")
    lines.append(f"  Drifts detected:      {summary.reconcile_drifts}")
    lines.append(hr)
    return "\n".join(lines) + "\n"


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Read-only daily summary of trades-YYYYMMDD.jsonl logs."
    )
    p.add_argument("--log-dir", default="logs/trades")
    p.add_argument("--date", type=_parse_date)
    p.add_argument("--since", type=_parse_date)
    p.add_argument("--until", type=_parse_date)
    p.add_argument("--symbol", default=None)
    p.add_argument("--format", default="text", choices=["text", "markdown"])
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    log_dir = Path(args.log_dir)
    if args.date:
        since = until = args.date
    else:
        since = args.since
        until = args.until
        if since is None and until is None:
            today = datetime.now(tz=timezone.utc).date()
            since = until = today

    files = find_log_files(log_dir, since=since, until=until)
    if not files:
        print(f"[analyze_trades] No trade files found in {log_dir} for {since} .. {until}")
        return 0

    events: list[Event] = []
    for f in files:
        events.extend(load_events(f))

    summary = aggregate(events, symbol_filter=args.symbol)
    summary.period_start = since
    summary.period_end = until
    summary.source_files = files

    out = render(summary, fmt=args.format)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(out, encoding="utf-8")
        print(f"[analyze_trades] wrote {out_path}")
    else:
        try:
            sys.stdout.write(out)
        except UnicodeEncodeError:
            sys.stdout.write(out.encode("ascii", errors="replace").decode("ascii"))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
