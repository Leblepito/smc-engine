"""AuditLog testleri — trades-YYYYMMDD.jsonl daily rotation (Spec §4.5)."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch

import pytest

from smc_engine.execution.audit_log import AuditLog


def test_emit_writes_jsonl_line(tmp_path):
    audit = AuditLog(log_dir=str(tmp_path), engine_sha="abc123", testnet=True)
    audit.emit("ORDER_PLACED", order_id="12345", symbol="BTCUSDT", qty=0.002)

    files = list(tmp_path.glob("trades-*.jsonl"))
    assert len(files) == 1
    line = files[0].read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert payload["event"] == "ORDER_PLACED"
    assert payload["order_id"] == "12345"
    assert payload["symbol"] == "BTCUSDT"
    assert payload["qty"] == 0.002
    # Ortak field'lar
    assert "ts" in payload
    assert "T" in payload["ts"]
    assert payload["phase"] == "5A"
    assert payload["engine_sha"] == "abc123"
    assert payload["testnet"] is True


def test_emit_multiple_events_append(tmp_path):
    audit = AuditLog(log_dir=str(tmp_path), engine_sha="x", testnet=True)
    audit.emit("ORDER_PLACED", order_id="1")
    audit.emit("ORDER_FILLED", order_id="1", fill_price=78327.50)
    audit.emit("TP_HIT", order_id="1", pnl_dollar=3.01)
    files = list(tmp_path.glob("trades-*.jsonl"))
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    events = [json.loads(l)["event"] for l in lines]
    assert events == ["ORDER_PLACED", "ORDER_FILLED", "TP_HIT"]


def test_daily_rotation(tmp_path):
    audit = AuditLog(log_dir=str(tmp_path), engine_sha="x", testnet=True)
    with patch("smc_engine.execution.audit_log._utcnow",
               return_value=datetime(2026, 5, 17, 14, 45)):
        audit.emit("ORDER_PLACED", order_id="1")
    with patch("smc_engine.execution.audit_log._utcnow",
               return_value=datetime(2026, 5, 18, 0, 5)):
        audit.emit("ORDER_PLACED", order_id="2")
    files = sorted(tmp_path.glob("trades-*.jsonl"))
    assert len(files) == 2
    assert "20260517" in files[0].name
    assert "20260518" in files[1].name


def test_log_dir_auto_created(tmp_path):
    log_dir = tmp_path / "nested" / "deeper"
    assert not log_dir.exists()
    AuditLog(log_dir=str(log_dir), engine_sha="x", testnet=True)
    assert log_dir.exists()


def test_filename_format(tmp_path):
    audit = AuditLog(log_dir=str(tmp_path), engine_sha="x", testnet=True)
    with patch("smc_engine.execution.audit_log._utcnow",
               return_value=datetime(2026, 5, 17, 3, 15, 5)):
        audit.emit("TEST_EVENT")
    files = list(tmp_path.glob("trades-*.jsonl"))
    assert files[0].name == "trades-20260517.jsonl"


def test_testnet_flag_propagated_correctly(tmp_path):
    """mainnet=True iken testnet field False olmali (Spec §4.5)."""
    audit = AuditLog(log_dir=str(tmp_path), engine_sha="x", testnet=False)
    audit.emit("ORDER_PLACED")
    line = list(tmp_path.glob("trades-*.jsonl"))[0].read_text().strip()
    assert json.loads(line)["testnet"] is False
