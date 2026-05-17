"""``analyze_signals.py`` — read-only günlük ``signals-YYYYMMDD.jsonl`` özet CLI.

Çalıştırma:
    python scripts/analyze_signals.py                       # bugünün özeti
    python scripts/analyze_signals.py --date 2026-05-17
    python scripts/analyze_signals.py --since 2026-05-17 --until 2026-05-20
    python scripts/analyze_signals.py --symbol BTCUSDT
    python scripts/analyze_signals.py --kind validated_setup
    python scripts/analyze_signals.py --format markdown --out reports/week1.md

Bu script salt-okunur: ``signal_logger.py`` veya runner'a dokunmaz. Sadece
``logs/`` altındaki JSONL dosyalarını parse eder, terminale (veya ``--out``
ile dosyaya) Markdown / düz metin rapor basar. Stdlib-only — 3rd-party UI
bağımlılığı yok.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


# =========================================================
# Veri tipleri
# =========================================================


@dataclass
class Event:
    kind: str           # "validated_setup" | "rejection"
    symbol: str
    at_bar: datetime
    payload: dict       # tam JSON satırı (setup, gate, reason, vs.)


@dataclass
class SymbolStats:
    symbol: str
    validated: int = 0
    rejected: int = 0

    @property
    def total(self) -> int:
        return self.validated + self.rejected

    @property
    def validation_rate(self) -> float:
        return (self.validated / self.total) if self.total else 0.0


# Confluence bucket sınırları — Spec'in tablodaki aralıklar.
# `<0.60` bucket'i altta: confluence_min_score=0.4 olabildiği için [0.4, 0.6)
# setup'ları validated olarak gelebilir; bunları görmezden gelmemek için.
_CONF_BUCKETS: list[tuple[str, float, float]] = [
    ("<0.60",    0.0,  0.60),
    ("0.60-0.70", 0.60, 0.70),
    ("0.70-0.80", 0.70, 0.80),
    ("0.80-0.90", 0.80, 0.90),
    ("0.90-1.00", 0.90, 1.001),  # 1.0 dahil
]

_RR_BUCKETS: list[tuple[str, float, float]] = [
    ("1.0-1.5", 1.0, 1.5),
    ("1.5-2.0", 1.5, 2.0),
    ("2.0-2.5", 2.0, 2.5),
    ("2.5+",    2.5, float("inf")),
]


@dataclass
class Summary:
    total: int = 0
    validated_count: int = 0
    rejected_count: int = 0
    by_symbol: dict[str, SymbolStats] = field(default_factory=dict)
    # rejection gate -> count, en yüksekten sıralı (insertion order = sort)
    rejection_breakdown: dict[str, int] = field(default_factory=dict)
    confluence_distribution: dict[str, int] = field(default_factory=dict)
    rr_distribution: dict[str, int] = field(default_factory=dict)
    validated_events: list[Event] = field(default_factory=list)
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    source_files: list[Path] = field(default_factory=list)
    symbol_filter: Optional[str] = None
    kind_filter: Optional[str] = None

    @property
    def validation_rate(self) -> float:
        return (self.validated_count / self.total) if self.total else 0.0


# =========================================================
# A1 — Loader
# =========================================================


def load_events(path: Path) -> list[Event]:
    """JSONL dosyasını parse et. Bozuk satırları skip + stderr warning.

    Eksik / parse edilemeyen satır kaydı düşürür ama dosyanın geri kalanını
    çalıştırmaya devam eder. Dosya yoksa boş liste döner (CLI üst katmanı
    "no files" mesajını ayrıca verir).
    """
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
                print(
                    f"[analyze_signals] skip invalid JSON {p.name}:{lineno}: {exc}",
                    file=sys.stderr,
                )
                continue
            ev = _event_from_obj(obj)
            if ev is None:
                print(
                    f"[analyze_signals] skip event missing required fields "
                    f"{p.name}:{lineno}",
                    file=sys.stderr,
                )
                continue
            out.append(ev)
    return out


def _event_from_obj(obj: dict) -> Optional[Event]:
    kind = obj.get("kind")
    symbol = obj.get("symbol")
    at_bar_raw = obj.get("at_bar")
    if not kind or not symbol or not at_bar_raw:
        return None
    try:
        at_bar = datetime.fromisoformat(at_bar_raw)
    except (TypeError, ValueError):
        return None
    return Event(kind=kind, symbol=symbol, at_bar=at_bar, payload=obj)


_FILENAME_RE = re.compile(r"^signals-(\d{4})(\d{2})(\d{2})\.jsonl$")


def _date_from_filename(name: str) -> Optional[date]:
    m = _FILENAME_RE.match(name)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def find_log_files(
    log_dir: Path,
    since: Optional[date] = None,
    until: Optional[date] = None,
) -> list[Path]:
    """``log_dir/signals-YYYYMMDD.jsonl`` dosyalarını glob et + tarih filtresi uygula."""
    p = Path(log_dir)
    if not p.exists():
        return []
    matches: list[tuple[date, Path]] = []
    for path in p.glob("signals-*.jsonl"):
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


# =========================================================
# A2 — Aggregator
# =========================================================


def _validated_setup(ev: Event) -> Optional[dict]:
    if ev.kind != "validated_setup":
        return None
    setup = ev.payload.get("setup")
    return setup if isinstance(setup, dict) else None


def _bucket(value: float, buckets: list[tuple[str, float, float]]) -> Optional[str]:
    for label, lo, hi in buckets:
        if lo <= value < hi:
            return label
    return None


def aggregate(
    events: Iterable[Event],
    symbol_filter: Optional[str] = None,
    kind_filter: Optional[str] = None,
) -> Summary:
    events = list(events)
    if symbol_filter:
        events = [e for e in events if e.symbol == symbol_filter]
    if kind_filter:
        events = [e for e in events if e.kind == kind_filter]

    s = Summary(symbol_filter=symbol_filter, kind_filter=kind_filter)
    s.total = len(events)

    # Bucket'leri sıfırla — dağılım tabloları için tüm bucket'lar görünsün.
    for label, _, _ in _CONF_BUCKETS:
        s.confluence_distribution[label] = 0
    for label, _, _ in _RR_BUCKETS:
        s.rr_distribution[label] = 0

    rej_counter: Counter = Counter()

    for ev in events:
        stats = s.by_symbol.setdefault(ev.symbol, SymbolStats(symbol=ev.symbol))
        if ev.kind == "validated_setup":
            s.validated_count += 1
            stats.validated += 1
            s.validated_events.append(ev)
            setup = _validated_setup(ev) or {}
            conf = setup.get("confluence_score")
            if isinstance(conf, (int, float)):
                b = _bucket(float(conf), _CONF_BUCKETS)
                if b:
                    s.confluence_distribution[b] += 1
            rr = setup.get("rr")
            if isinstance(rr, (int, float)):
                b = _bucket(float(rr), _RR_BUCKETS)
                if b:
                    s.rr_distribution[b] += 1
        elif ev.kind == "rejection":
            s.rejected_count += 1
            stats.rejected += 1
            gate = ev.payload.get("gate") or "unknown"
            rej_counter[gate] += 1

    # En yüksekten sırala
    s.rejection_breakdown = dict(rej_counter.most_common())
    return s


# =========================================================
# A3 — Renderer
# =========================================================


_BLOCK = "█"
_HR = "─" * 59


def _fmt_pct(n: int, total: int) -> str:
    if not total:
        return "0.0%"
    return f"{(n / total) * 100:.1f}%"


_BAR_MAX_WIDTH = 24


def _bar(count: int, scale: int) -> str:
    """Plain-text histogram bar — scale (max count) referansla normalize edilir.

    En yüksek bin'e BAR_MAX_WIDTH karakter genişlik düşer; diğerleri orantısal.
    """
    if scale <= 0 or count <= 0:
        return ""
    width = max(1, round((count / scale) * _BAR_MAX_WIDTH))
    return _BLOCK * width


def _period_label(s: Summary) -> str:
    if s.period_start is None or s.period_end is None:
        return "all available"
    if s.period_start == s.period_end:
        return f"{s.period_start.isoformat()} (1 day)"
    days = (s.period_end - s.period_start).days + 1
    return f"{s.period_start.isoformat()} → {s.period_end.isoformat()} ({days} days)"


def _source_label(s: Summary) -> str:
    if not s.source_files:
        return "(no files)"
    if len(s.source_files) == 1:
        return f"logs/{s.source_files[0].name}"
    return f"{len(s.source_files)} files"


def render(summary: Summary, fmt: str = "text") -> str:
    if fmt == "markdown":
        return _render_markdown(summary)
    return _render_text(summary)


def _render_text(s: Summary) -> str:
    lines: list[str] = []
    lines.append(_HR)
    lines.append(" SMC Engine — Signal Analysis Report")
    lines.append(f" Period: {_period_label(s)}  |  Source: {_source_label(s)}")
    if s.symbol_filter:
        lines.append(f" Symbol filter: {s.symbol_filter}")
    if s.kind_filter:
        lines.append(f" Kind filter:   {s.kind_filter}")
    lines.append(_HR)
    lines.append("")

    # OVERALL
    lines.append("OVERALL")
    lines.append(f"  Total events:      {s.total:6d}")
    lines.append(f"  Validated setups:  {s.validated_count:6d}  ({_fmt_pct(s.validated_count, s.total)})")
    lines.append(f"  Rejections:        {s.rejected_count:6d}  ({_fmt_pct(s.rejected_count, s.total)})")
    lines.append("")

    # BY SYMBOL
    lines.append("BY SYMBOL")
    if not s.by_symbol:
        lines.append("  (no events)")
    else:
        lines.append("  Symbol      Validated  Rejected  Total  Setup Rate")
        for sym in sorted(s.by_symbol):
            st = s.by_symbol[sym]
            lines.append(
                f"  {sym:<10}  {st.validated:9d}  {st.rejected:8d}  {st.total:5d}  "
                f"{_fmt_pct(st.validated, st.total):>10}"
            )
    lines.append("")

    # REJECTION BREAKDOWN
    lines.append("REJECTION BREAKDOWN (top gates)")
    if not s.rejection_breakdown:
        lines.append("  (no rejections)")
    else:
        lines.append("  Gate                 Count   %")
        total_rej = s.rejected_count or 1
        for gate, count in s.rejection_breakdown.items():
            lines.append(
                f"  {gate:<18}  {count:6d}  {_fmt_pct(count, total_rej):>5}"
            )
    lines.append("")

    # VALIDATED SETUPS — DETAIL
    lines.append("VALIDATED SETUPS — DETAIL")
    if not s.validated_events:
        lines.append("  (none)")
    else:
        lines.append("  #  at_bar               Sym       Dir   Entry         RR     Conf  Factors")
        for idx, ev in enumerate(s.validated_events, start=1):
            setup = _validated_setup(ev) or {}
            direction = setup.get("direction", "?")
            entry = setup.get("entry", float("nan"))
            rr = setup.get("rr", float("nan"))
            conf = setup.get("confluence_score", float("nan"))
            factors = setup.get("confluence_factor_count", 0)
            ts_str = ev.at_bar.strftime("%Y-%m-%d %H:%M:%S")
            lines.append(
                f"  {idx:<2} {ts_str}  {ev.symbol:<8}  {direction:<5} "
                f"{entry:>10.4f}  {rr:>4.2f}  {conf:>4.2f}  {factors}"
            )
    lines.append("")

    # CONFLUENCE DISTRIBUTION
    lines.append("CONFLUENCE DISTRIBUTION (validated only)")
    max_conf = max(s.confluence_distribution.values()) if s.confluence_distribution else 0
    for label, count in s.confluence_distribution.items():
        bar = _bar(count, max_conf)
        lines.append(f"  {label}  {bar:<10}  ({count})")
    lines.append("")

    # R:R DISTRIBUTION
    lines.append("R:R DISTRIBUTION (validated only)")
    max_rr = max(s.rr_distribution.values()) if s.rr_distribution else 0
    for label, count in s.rr_distribution.items():
        bar = _bar(count, max_rr)
        lines.append(f"  {label:<10}  {bar:<10}  ({count})")

    lines.append(_HR)
    return "\n".join(lines) + "\n"


def _render_markdown(s: Summary) -> str:
    lines: list[str] = []
    lines.append("# SMC Engine — Signal Analysis Report")
    lines.append("")
    lines.append(f"**Period:** {_period_label(s)}")
    lines.append(f"**Source:** {_source_label(s)}")
    if s.symbol_filter:
        lines.append(f"**Symbol filter:** {s.symbol_filter}")
    if s.kind_filter:
        lines.append(f"**Kind filter:** {s.kind_filter}")
    lines.append("")

    lines.append("## Overall")
    lines.append("")
    lines.append("| Metric | Count | % |")
    lines.append("| --- | ---: | ---: |")
    lines.append(f"| Total events | {s.total} | 100.0% |")
    lines.append(f"| Validated setups | {s.validated_count} | {_fmt_pct(s.validated_count, s.total)} |")
    lines.append(f"| Rejections | {s.rejected_count} | {_fmt_pct(s.rejected_count, s.total)} |")
    lines.append("")

    lines.append("## By Symbol")
    lines.append("")
    lines.append("| Symbol | Validated | Rejected | Total | Setup Rate |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for sym in sorted(s.by_symbol):
        st = s.by_symbol[sym]
        lines.append(
            f"| {sym} | {st.validated} | {st.rejected} | {st.total} | {_fmt_pct(st.validated, st.total)} |"
        )
    if not s.by_symbol:
        lines.append("| _(no events)_ |  |  |  |  |")
    lines.append("")

    lines.append("## Rejection Breakdown")
    lines.append("")
    lines.append("| Gate | Count | % |")
    lines.append("| --- | ---: | ---: |")
    total_rej = s.rejected_count or 1
    for gate, count in s.rejection_breakdown.items():
        lines.append(f"| {gate} | {count} | {_fmt_pct(count, total_rej)} |")
    if not s.rejection_breakdown:
        lines.append("| _(no rejections)_ |  |  |")
    lines.append("")

    lines.append("## Validated Setups")
    lines.append("")
    lines.append("| # | at_bar | Symbol | Dir | Entry | RR | Conf | Factors |")
    lines.append("| ---: | --- | --- | --- | ---: | ---: | ---: | ---: |")
    for idx, ev in enumerate(s.validated_events, start=1):
        setup = _validated_setup(ev) or {}
        lines.append(
            f"| {idx} | {ev.at_bar.isoformat()} | {ev.symbol} | "
            f"{setup.get('direction', '?')} | {setup.get('entry', 'n/a')} | "
            f"{setup.get('rr', 'n/a')} | {setup.get('confluence_score', 'n/a')} | "
            f"{setup.get('confluence_factor_count', 0)} |"
        )
    if not s.validated_events:
        lines.append("| _(none)_ |  |  |  |  |  |  |  |")
    lines.append("")

    lines.append("## Confluence Distribution (validated only)")
    lines.append("")
    lines.append("| Bucket | Count |")
    lines.append("| --- | ---: |")
    for label, count in s.confluence_distribution.items():
        lines.append(f"| {label} | {count} |")
    lines.append("")

    lines.append("## R:R Distribution (validated only)")
    lines.append("")
    lines.append("| Bucket | Count |")
    lines.append("| --- | ---: |")
    for label, count in s.rr_distribution.items():
        lines.append(f"| {label} | {count} |")
    lines.append("")

    return "\n".join(lines) + "\n"


# =========================================================
# A4 — CLI entry
# =========================================================


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Read-only daily summary of signals-YYYYMMDD.jsonl logs."
    )
    p.add_argument("--log-dir", default="logs", help="Directory containing signals-*.jsonl files")
    p.add_argument("--date", type=_parse_date, help="Single day (YYYY-MM-DD)")
    p.add_argument("--since", type=_parse_date, help="Start date (inclusive, YYYY-MM-DD)")
    p.add_argument("--until", type=_parse_date, help="End date (inclusive, YYYY-MM-DD)")
    p.add_argument("--symbol", default=None, help="Filter by symbol (e.g. BTCUSDT)")
    p.add_argument(
        "--kind",
        default=None,
        choices=["validated_setup", "rejection"],
        help="Only count one kind",
    )
    p.add_argument("--format", default="text", choices=["text", "markdown"])
    p.add_argument("--out", default=None, help="Write report to file (instead of stdout)")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    log_dir = Path(args.log_dir)

    if args.date is not None:
        since = until = args.date
    else:
        since = args.since
        until = args.until
        if since is None and until is None:
            # UTC default — JSONL dosya adı UTC tarih kullanıyor (signal_logger).
            today_utc = datetime.now(tz=timezone.utc).date()
            since = until = today_utc

    files = find_log_files(log_dir, since=since, until=until)
    if not files:
        msg = f"[analyze_signals] No signal files found in {log_dir} for period {since} .. {until}"
        print(msg)
        return 0

    events: list[Event] = []
    for f in files:
        events.extend(load_events(f))

    summary = aggregate(events, symbol_filter=args.symbol, kind_filter=args.kind)
    summary.period_start = since
    summary.period_end = until
    summary.source_files = files

    output = render(summary, fmt=args.format)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"[analyze_signals] wrote {out_path} ({len(output)} chars)")
    else:
        # Windows cp1252 stdout için fallback — encode hatasını yutmadan
        # değiştir, çünkü block karakteri (█) cp1252'de yok.
        try:
            sys.stdout.write(output)
        except UnicodeEncodeError:
            sys.stdout.write(output.encode("ascii", errors="replace").decode("ascii"))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
