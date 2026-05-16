"""Pine Script v6 setup overlay export — TradingView entegrasyon katmani testleri.

`to_pine_overlays(list[Setup])` Setup nesnelerini grafikte gosteren Pine
v6 snippet'i uretir (line.new/box.new/label.new). Burada Pine semantigi
test edilmiyor — yalnizca string formatinin dogrulugu.
"""

from __future__ import annotations

from datetime import datetime, timezone

from smc_engine.types import (
    Bias,
    Direction,
    POIKind,
    POIRef,
    Setup,
    TimeFrame,
    Zone,
    ZoneAnchor,
    ZoneKind,
    ZoneStatus,
)
from smc_engine.integrations.tradingview.pine_setup_export import to_pine_overlays


def _make_zone(direction: Direction) -> Zone:
    return Zone(
        kind=ZoneKind.DEMAND if direction == Direction.LONG else ZoneKind.SUPPLY,
        top=100.0,
        bottom=99.0,
        timeframe=TimeFrame.H1,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        status=ZoneStatus.FRESH,
        origin_candle_ts=datetime(2025, 1, 1, tzinfo=timezone.utc),
        anchor=ZoneAnchor.BODY,
        age_bars=1,
    )


def _make_setup(
    direction: Direction = Direction.LONG,
    entry: float = 100.0,
    sl: float = 98.0,
    tp: tuple[float, ...] = (103.0, 105.0, 108.0),
    created_at: datetime = datetime(2025, 6, 15, 12, 30, tzinfo=timezone.utc),
) -> Setup:
    z = _make_zone(direction)
    return Setup(
        direction=direction,
        entry=entry,
        sl=sl,
        tp=list(tp),
        tp_weights=[0.5, 0.3, 0.2],
        poi=POIRef(kind=POIKind.ZONE, ref=z, htf_aligned=True, score_hint=0.8),
        confirmation=None,
        bias_context=Bias.BULLISH if direction == Direction.LONG else Bias.BEARISH,
        confluence_score=0.72,
        rr=2.5,
        created_at=created_at,
        confluence_factor_count=3,
    )


def test_empty_list_returns_valid_header_snippet():
    out = to_pine_overlays([])
    assert isinstance(out, str)
    assert "// === SMC Engine" in out
    assert "// === end auto-generated ===" in out


def test_long_setup_uses_green_color():
    out = to_pine_overlays([_make_setup(direction=Direction.LONG)])
    assert "color.green" in out
    assert "color.red" not in out


def test_short_setup_uses_red_color():
    s = _make_setup(direction=Direction.SHORT, entry=100.0, sl=102.0, tp=(97.0, 95.0, 92.0))
    out = to_pine_overlays([s])
    assert "color.red" in out
    assert "color.green" not in out


def test_entry_sl_tp_levels_in_output():
    out = to_pine_overlays([_make_setup()])
    # Entry, SL ve tum TP fiyatlari ciktida gozukmeli
    assert "100.0" in out  # entry
    assert "98.0" in out  # sl
    assert "103.0" in out  # tp1
    assert "105.0" in out  # tp2
    assert "108.0" in out  # tp3


def test_entry_line_present():
    out = to_pine_overlays([_make_setup()])
    assert "line.new" in out


def test_sl_and_tp_lines_present():
    out = to_pine_overlays([_make_setup()])
    # En az dort line.new cagrisi olmali: entry + sl + 3 tp
    assert out.count("line.new") >= 5


def test_label_contains_setup_timestamp():
    out = to_pine_overlays([_make_setup(created_at=datetime(2025, 6, 15, 12, 30, tzinfo=timezone.utc))])
    assert "label.new" in out
    # ISO veya tarih str'si label icinde
    assert "2025-06-15" in out


def test_multiple_setups_indexed_uniquely():
    s1 = _make_setup(direction=Direction.LONG, entry=100.0, sl=98.0, tp=(103.0, 105.0, 108.0))
    s2 = _make_setup(direction=Direction.SHORT, entry=200.0, sl=202.0, tp=(197.0, 195.0, 192.0),
                     created_at=datetime(2025, 6, 16, 9, 0, tzinfo=timezone.utc))
    out = to_pine_overlays([s1, s2])
    # Iki setup'in renkleri ayri olmali
    assert "color.green" in out
    assert "color.red" in out
    # Iki setup'in entry fiyatlari ciktida
    assert "100.0" in out
    assert "200.0" in out


def test_long_short_direction_strings_in_labels():
    s_long = _make_setup(direction=Direction.LONG)
    s_short = _make_setup(direction=Direction.SHORT, sl=102.0, tp=(97.0, 95.0, 92.0))
    out_long = to_pine_overlays([s_long])
    out_short = to_pine_overlays([s_short])
    assert "LONG" in out_long
    assert "SHORT" in out_short
