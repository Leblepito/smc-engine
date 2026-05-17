"""SignalLogger testleri — JSONL günlük rotasyon + stdout (Spec §9)."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from smc_engine.live.signal_logger import SignalLogger
from smc_engine.types import (
    Bias,
    Direction,
    POIKind,
    POIRef,
    Rejection,
    Setup,
    TimeFrame,
    ValidatedSetup,
    Zone,
    ZoneAnchor,
    ZoneKind,
    ZoneStatus,
)


# ---------------- fixtures ----------------


def _fake_zone():
    return Zone(
        kind=ZoneKind.DEMAND,
        top=67500.0,
        bottom=67400.0,
        timeframe=TimeFrame.H4,
        created_at=datetime(2026, 5, 16, 12, 0),
        status=ZoneStatus.FRESH,
        origin_candle_ts=datetime(2026, 5, 16, 12, 0),
        anchor=ZoneAnchor.WICK,
        age_bars=5,
    )


def _fake_setup() -> Setup:
    poi = POIRef(kind=POIKind.ZONE, ref=_fake_zone(), htf_aligned=True, score_hint=0.7)
    return Setup(
        direction=Direction.LONG,
        entry=67432.5,
        sl=67100.0,
        tp=[67750.5, 68250.0, 69000.0],
        tp_weights=[0.5, 0.3, 0.2],
        poi=poi,
        confirmation=None,
        bias_context=Bias.BULLISH,
        confluence_score=0.62,
        rr=0.96,
        created_at=datetime(2026, 5, 16, 14, 45),
        confluence_factor_count=3,
    )


def _fake_validated_setup() -> ValidatedSetup:
    return ValidatedSetup(
        setup=_fake_setup(),
        position_size=0.0298,
        risk_amount=100.0,
        guard_log=["confluence", "regime", "deviation", "no_sl", "min_rr"],
    )


def _fake_rejection() -> Rejection:
    return Rejection(
        reason="LONG setup ama HTF bias BEARISH — yön ters",
        gate="regime",
        setup=_fake_setup(),
    )


# ---------------- log_dir auto-create ----------------


def test_logger_creates_log_dir_if_missing(tmp_path):
    log_dir = tmp_path / "doesnotexistyet" / "sub"
    assert not log_dir.exists()
    SignalLogger(log_dir=str(log_dir))
    assert log_dir.exists()
    assert log_dir.is_dir()


# ---------------- validated setup emit ----------------


def test_emit_validated_writes_jsonl_line(tmp_path, capsys):
    log_dir = tmp_path / "logs"
    logger = SignalLogger(log_dir=str(log_dir), symbol="BTCUSDT")
    vs = _fake_validated_setup()
    logger.emit(vs)

    # Bir günlük dosya oluşmalı
    files = list(log_dir.glob("signals-*.jsonl"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(content) == 1
    payload = json.loads(content[0])
    assert payload["kind"] == "validated_setup"
    assert payload["symbol"] == "BTCUSDT"
    assert payload["timeframe"] == "M15"
    assert payload["setup"]["direction"] == "LONG"
    assert payload["setup"]["entry"] == 67432.5
    assert payload["setup"]["rr"] == 0.96
    assert payload["position_size"] == 0.0298
    assert "guard_log" in payload
    # ts ISO format
    assert "T" in payload["ts"]

    # Stdout'a da basılmalı
    captured = capsys.readouterr()
    assert "BTCUSDT" in captured.out
    assert "validated_setup" in captured.out


def test_emit_rejection_writes_with_gate_and_reason(tmp_path, capsys):
    log_dir = tmp_path / "logs"
    logger = SignalLogger(log_dir=str(log_dir), symbol="ETHUSDT")
    rej = _fake_rejection()
    logger.emit(rej)

    files = list(log_dir.glob("signals-*.jsonl"))
    payload = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert payload["kind"] == "rejection"
    assert payload["symbol"] == "ETHUSDT"
    assert payload["gate"] == "regime"
    assert "ters" in payload["reason"]


def test_emit_appends_multiple_events(tmp_path):
    log_dir = tmp_path / "logs"
    logger = SignalLogger(log_dir=str(log_dir), symbol="BTCUSDT")
    logger.emit(_fake_validated_setup())
    logger.emit(_fake_rejection())
    logger.emit(_fake_validated_setup())
    files = list(log_dir.glob("signals-*.jsonl"))
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3


# ---------------- günlük rotasyon ----------------


def test_logger_rotates_file_per_day(tmp_path):
    log_dir = tmp_path / "logs"
    logger = SignalLogger(log_dir=str(log_dir), symbol="BTCUSDT")

    # Gün 1
    with patch("smc_engine.live.signal_logger._utcnow", return_value=datetime(2026, 5, 16, 14, 45)):
        logger.emit(_fake_validated_setup())

    # Gün 2
    with patch("smc_engine.live.signal_logger._utcnow", return_value=datetime(2026, 5, 17, 0, 5)):
        logger.emit(_fake_rejection())

    files = sorted(log_dir.glob("signals-*.jsonl"))
    assert len(files) == 2
    assert "20260516" in files[0].name
    assert "20260517" in files[1].name


def test_filename_format_is_signals_YYYYMMDD(tmp_path):
    log_dir = tmp_path / "logs"
    logger = SignalLogger(log_dir=str(log_dir), symbol="BTCUSDT")
    with patch("smc_engine.live.signal_logger._utcnow", return_value=datetime(2026, 5, 16, 14, 45)):
        logger.emit(_fake_validated_setup())
    files = list(log_dir.glob("signals-*.jsonl"))
    assert files[0].name == "signals-20260516.jsonl"


# ---------------- replay-friendly schema ----------------


def test_validated_setup_payload_includes_at_bar_and_factor_count(tmp_path):
    log_dir = tmp_path / "logs"
    logger = SignalLogger(log_dir=str(log_dir), symbol="BTCUSDT")
    logger.emit(_fake_validated_setup())
    files = list(log_dir.glob("signals-*.jsonl"))
    payload = json.loads(files[0].read_text(encoding="utf-8").strip())
    # at_bar = setup.created_at (Spec §9)
    assert "at_bar" in payload
    assert payload["setup"]["confluence_factor_count"] == 3
    assert payload["setup"]["tp"] == [67750.5, 68250.0, 69000.0]
    assert payload["setup"]["tp_weights"] == [0.5, 0.3, 0.2]
