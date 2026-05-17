"""analyze_signals CLI testleri — JSONL özet rapor (Sub-proje #2 yardımcı)."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

# scripts/ klasörü sys.path'te değil — modül import için path ekle.
ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import analyze_signals as az  # noqa: E402


FIXTURE = ROOT / "tests" / "fixtures" / "signals_sample.jsonl"


# =========================================================
# A1 — Loader
# =========================================================


def test_load_jsonl_parses_validated_and_rejection(capsys):
    """3 satır: 1 validated, 1 bozuk (skip+warn), 1 rejection."""
    events = az.load_events(FIXTURE)
    assert len(events) == 2
    kinds = [e.kind for e in events]
    assert "validated_setup" in kinds
    assert "rejection" in kinds
    # bozuk satır stderr'e uyarı yazmalı
    err = capsys.readouterr().err
    assert "skip" in err.lower() or "warn" in err.lower() or "invalid" in err.lower()


def test_load_events_returns_typed_dataclass():
    events = az.load_events(FIXTURE)
    e0 = events[0]
    assert isinstance(e0, az.Event)
    assert isinstance(e0.at_bar, datetime)
    assert isinstance(e0.symbol, str)
    assert isinstance(e0.payload, dict)


def test_load_events_missing_file_returns_empty_list(tmp_path):
    assert az.load_events(tmp_path / "does_not_exist.jsonl") == []


def test_load_events_for_dates_filters_by_filename(tmp_path):
    """Multi-day: logs/signals-YYYYMMDD.jsonl glob + tarih aralığı filtresi."""
    (tmp_path / "signals-20260517.jsonl").write_text(
        json.dumps({"at_bar": "2026-05-17T03:00:00", "kind": "validated_setup",
                    "symbol": "BTCUSDT", "timeframe": "M15", "ts": "2026-05-17T03:15:00+00:00",
                    "setup": {"direction": "LONG", "entry": 1.0, "sl": 0.9, "tp": [1.1], "tp_weights": [1.0],
                              "rr": 1.0, "confluence_score": 0.6, "confluence_factor_count": 2,
                              "bias_context": "BULLISH", "created_at": "2026-05-17T03:00:00",
                              "poi": {"kind": "ZONE", "htf_aligned": True, "score_hint": 1.0}},
                    "position_size": 0.1, "risk_amount": 100.0, "guard_log": []}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "signals-20260518.jsonl").write_text(
        json.dumps({"at_bar": "2026-05-18T03:00:00", "kind": "rejection",
                    "symbol": "ETHUSDT", "timeframe": "M15", "ts": "2026-05-18T03:15:00+00:00",
                    "gate": "regime", "reason": "test",
                    "setup": {"direction": "LONG", "entry": 1.0, "sl": 0.9, "tp": [1.1], "tp_weights": [1.0],
                              "rr": 1.0, "confluence_score": 0.6, "confluence_factor_count": 2,
                              "bias_context": "BULLISH", "created_at": "2026-05-18T03:00:00",
                              "poi": {"kind": "ZONE", "htf_aligned": True, "score_hint": 1.0}}}) + "\n",
        encoding="utf-8",
    )
    files = az.find_log_files(tmp_path, since=date(2026, 5, 17), until=date(2026, 5, 17))
    assert len(files) == 1
    assert files[0].name == "signals-20260517.jsonl"

    files_both = az.find_log_files(tmp_path, since=date(2026, 5, 17), until=date(2026, 5, 18))
    assert len(files_both) == 2


# =========================================================
# A2 — Aggregator
# =========================================================


def _make_validated(symbol="BTCUSDT", at_bar="2026-05-17T03:15:00", direction="LONG",
                    entry=100.0, rr=1.5, conf=0.80, factors=4):
    return az.Event(
        kind="validated_setup",
        symbol=symbol,
        at_bar=datetime.fromisoformat(at_bar),
        payload={
            "setup": {
                "direction": direction, "entry": entry, "rr": rr,
                "confluence_score": conf, "confluence_factor_count": factors,
            },
            "position_size": 0.1, "risk_amount": 100.0, "guard_log": [],
        },
    )


def _make_rejection(symbol="BTCUSDT", gate="confluence", at_bar="2026-05-17T03:15:00"):
    return az.Event(
        kind="rejection",
        symbol=symbol,
        at_bar=datetime.fromisoformat(at_bar),
        payload={"gate": gate, "reason": "x", "setup": {"direction": "LONG", "rr": 0.0,
                                                          "confluence_score": 0.3,
                                                          "confluence_factor_count": 1, "entry": 100.0}},
    )


def test_aggregate_basic_counts():
    events = [_make_validated()] * 3 + [_make_rejection()] * 2
    s = az.aggregate(events)
    assert s.total == 5
    assert s.validated_count == 3
    assert s.rejected_count == 2
    assert s.validation_rate == pytest.approx(0.6)


def test_aggregate_empty_list():
    s = az.aggregate([])
    assert s.total == 0
    assert s.validated_count == 0
    assert s.validation_rate == 0.0


def test_aggregate_by_symbol():
    events = [
        _make_validated("BTCUSDT"), _make_validated("BTCUSDT"),
        _make_rejection("BTCUSDT"), _make_rejection("BTCUSDT"), _make_rejection("BTCUSDT"),
        _make_validated("ETHUSDT"),
        _make_rejection("ETHUSDT"),
    ]
    s = az.aggregate(events)
    assert s.by_symbol["BTCUSDT"].validated == 2
    assert s.by_symbol["BTCUSDT"].rejected == 3
    assert s.by_symbol["BTCUSDT"].total == 5
    assert s.by_symbol["ETHUSDT"].validated == 1
    assert s.by_symbol["ETHUSDT"].total == 2


def test_aggregate_rejection_breakdown_sorted_desc():
    events = [
        _make_rejection(gate="confluence"), _make_rejection(gate="confluence"),
        _make_rejection(gate="confluence"),
        _make_rejection(gate="min_rr"), _make_rejection(gate="min_rr"),
        _make_rejection(gate="regime"),
    ]
    s = az.aggregate(events)
    gates = list(s.rejection_breakdown.items())
    # En yüksekten sırala
    assert gates[0][0] == "confluence"
    assert gates[0][1] == 3
    assert gates[1][0] == "min_rr"
    assert gates[-1][0] == "regime"


def test_aggregate_confluence_distribution_buckets_validated_only():
    """Bucket: 0.6-0.7, 0.7-0.8, 0.8-0.9, 0.9-1.0. Sadece validated sayılır."""
    events = [
        _make_validated(conf=0.65),
        _make_validated(conf=0.75),
        _make_validated(conf=0.85),
        _make_validated(conf=0.85),
        _make_rejection(),  # bucket'a girmemeli
    ]
    s = az.aggregate(events)
    buckets = s.confluence_distribution
    assert buckets["0.60-0.70"] == 1
    assert buckets["0.70-0.80"] == 1
    assert buckets["0.80-0.90"] == 2
    assert buckets["0.90-1.00"] == 0


def test_aggregate_rr_distribution_buckets_validated_only():
    events = [
        _make_validated(rr=1.2), _make_validated(rr=1.8),
        _make_validated(rr=2.3), _make_validated(rr=3.0),
        _make_rejection(),  # sayılmamalı
    ]
    s = az.aggregate(events)
    buckets = s.rr_distribution
    assert buckets["1.0-1.5"] == 1
    assert buckets["1.5-2.0"] == 1
    assert buckets["2.0-2.5"] == 1
    assert buckets["2.5+"] == 1


def test_aggregate_symbol_filter():
    events = [_make_validated("BTCUSDT"), _make_validated("ETHUSDT"), _make_rejection("ETHUSDT")]
    s = az.aggregate(events, symbol_filter="ETHUSDT")
    assert s.total == 2
    assert "BTCUSDT" not in s.by_symbol


def test_aggregate_kind_filter_validated_only():
    events = [_make_validated(), _make_rejection(), _make_validated()]
    s = az.aggregate(events, kind_filter="validated_setup")
    assert s.total == 2
    assert s.rejected_count == 0


# =========================================================
# A3 — Renderer
# =========================================================


def _full_summary():
    events = [
        _make_validated("BTCUSDT", conf=0.85, rr=1.5),
        _make_validated("BTCUSDT", conf=0.75, rr=2.1),
        _make_validated("ETHUSDT", conf=0.85, rr=1.8),
        _make_rejection("BTCUSDT", gate="confluence"),
        _make_rejection("BTCUSDT", gate="min_rr"),
        _make_rejection("ETHUSDT", gate="regime"),
    ]
    return az.aggregate(events)


def test_render_plain_text_contains_sections():
    out = az.render(_full_summary(), fmt="text")
    for section in ("OVERALL", "BY SYMBOL", "REJECTION BREAKDOWN", "VALIDATED SETUPS"):
        assert section in out


def test_render_plain_text_includes_symbol_rows():
    out = az.render(_full_summary(), fmt="text")
    assert "BTCUSDT" in out
    assert "ETHUSDT" in out


def test_render_plain_text_histogram_uses_block_chars():
    out = az.render(_full_summary(), fmt="text")
    # Histogram bar — Unicode block karakteri (full block ya da equivalent)
    assert any(ch in out for ch in ("█", "▓", "▌"))


def test_render_markdown_uses_table_syntax():
    out = az.render(_full_summary(), fmt="markdown")
    assert "## " in out
    assert "| Symbol " in out
    # Markdown tablo ayraç satırı
    assert "| --- " in out or "|---" in out


def test_render_empty_summary_is_graceful():
    s = az.aggregate([])
    out = az.render(s, fmt="text")
    assert "OVERALL" in out
    assert "0" in out  # total=0 görünmeli


# =========================================================
# A4 — CLI entry
# =========================================================


def test_cli_with_no_files_exits_zero_with_message(tmp_path, capsys):
    """logs/ boşsa "No signal files found" + exit 0 (warning, error değil)."""
    rc = az.main(["--log-dir", str(tmp_path)])
    assert rc == 0
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "No signal files" in combined or "no signal" in combined.lower()


def test_cli_renders_summary_for_date(tmp_path, capsys):
    """--date YYYY-MM-DD belirtildiğinde o günün dosyası okunur ve rapor basılır."""
    # tmp_path/signals-20260517.jsonl — fixture'dan kopyala
    log = tmp_path / "signals-20260517.jsonl"
    log.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    rc = az.main(["--log-dir", str(tmp_path), "--date", "2026-05-17"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "OVERALL" in out
    assert "2026-05-17" in out


def test_cli_with_out_writes_file(tmp_path):
    """--out + --format markdown → dosya oluşur, içerik render() ile aynı."""
    log = tmp_path / "signals-20260517.jsonl"
    log.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    out_file = tmp_path / "report.md"
    rc = az.main([
        "--log-dir", str(tmp_path),
        "--date", "2026-05-17",
        "--format", "markdown",
        "--out", str(out_file),
    ])
    assert rc == 0
    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "## " in content


def test_cli_symbol_filter_excludes_other_symbols(tmp_path, capsys):
    log = tmp_path / "signals-20260517.jsonl"
    log.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    rc = az.main(["--log-dir", str(tmp_path), "--date", "2026-05-17", "--symbol", "BTCUSDT"])
    assert rc == 0
    out = capsys.readouterr().out
    # ETHUSDT rejection vardı fixture'da — filtre sonrası görünmemeli
    assert "ETHUSDT" not in out
