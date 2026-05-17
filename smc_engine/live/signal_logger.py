"""``SignalLogger`` — ValidatedSetup / Rejection → JSONL günlük rotasyon + stdout (Spec §9).

Format (her satır bir JSON):
- validated_setup → setup alanları + position_size + risk_amount + guard_log
- rejection      → gate + reason + setup snapshot

Günlük rotasyon: ``signals-YYYYMMDD.jsonl`` (UTC). Replay edilebilir; sonradan
walk-forward/bootstrap analizine girdi olur.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Union

from smc_engine.types import (
    Bias,
    Direction,
    POIKind,
    Rejection,
    Setup,
    TimeFrame,
    ValidatedSetup,
    Zone,
    ZoneAnchor,
    ZoneKind,
    ZoneStatus,
)


def _utcnow() -> datetime:
    """Test patch'lenebilir saat kaynağı."""
    return datetime.now(tz=timezone.utc)


def _serialize_enum(v: Any) -> Any:
    """Enum'u .name string'ine çevir; diğerlerini olduğu gibi bırak."""
    if hasattr(v, "name") and hasattr(v, "value"):
        return v.value if isinstance(v.value, str) else v.name
    return v


def _setup_to_dict(setup: Setup) -> dict:
    poi = setup.poi
    poi_ref = poi.ref
    poi_payload = {
        "kind": _serialize_enum(poi.kind),
        "htf_aligned": poi.htf_aligned,
        "score_hint": poi.score_hint,
    }
    if isinstance(poi_ref, Zone):
        poi_payload["zone"] = {
            "kind": _serialize_enum(poi_ref.kind),
            "top": poi_ref.top,
            "bottom": poi_ref.bottom,
            "timeframe": _serialize_enum(poi_ref.timeframe),
            "status": _serialize_enum(poi_ref.status),
            "anchor": _serialize_enum(poi_ref.anchor),
        }
    return {
        "direction": _serialize_enum(setup.direction),
        "entry": setup.entry,
        "sl": setup.sl,
        "tp": list(setup.tp),
        "tp_weights": list(setup.tp_weights),
        "rr": setup.rr,
        "confluence_score": setup.confluence_score,
        "confluence_factor_count": setup.confluence_factor_count,
        "bias_context": _serialize_enum(setup.bias_context),
        "created_at": setup.created_at.isoformat(),
        "poi": poi_payload,
    }


def _validated_to_dict(vs: ValidatedSetup) -> dict:
    return {
        "kind": "validated_setup",
        "setup": _setup_to_dict(vs.setup),
        "position_size": vs.position_size,
        "risk_amount": vs.risk_amount,
        "guard_log": list(vs.guard_log),
        "at_bar": vs.setup.created_at.isoformat(),
    }


def _rejection_to_dict(rej: Rejection) -> dict:
    return {
        "kind": "rejection",
        "gate": rej.gate,
        "reason": rej.reason,
        "setup": _setup_to_dict(rej.setup),
        "at_bar": rej.setup.created_at.isoformat(),
    }


class SignalLogger:
    """JSONL günlük dosyaya yaz + stdout'a bas."""

    def __init__(
        self,
        log_dir: str,
        symbol: str = "",
        timeframe: TimeFrame = TimeFrame.M15,
        stdout=None,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.symbol = symbol
        self.timeframe = timeframe
        self._stdout = stdout if stdout is not None else sys.stdout

    def _current_filename(self, now: datetime) -> Path:
        date_str = now.strftime("%Y%m%d")
        return self.log_dir / f"signals-{date_str}.jsonl"

    def emit(self, payload: Union[ValidatedSetup, Rejection]) -> None:
        now = _utcnow()
        if isinstance(payload, ValidatedSetup):
            body = _validated_to_dict(payload)
        elif isinstance(payload, Rejection):
            body = _rejection_to_dict(payload)
        else:
            raise TypeError(
                f"SignalLogger.emit: ValidatedSetup veya Rejection bekleniyordu, "
                f"{type(payload).__name__} geldi"
            )

        envelope = {
            "ts": now.replace(microsecond=0).isoformat(),
            "symbol": self.symbol,
            "timeframe": _serialize_enum(self.timeframe),
            **body,
        }
        line = json.dumps(envelope, ensure_ascii=False, sort_keys=True)

        path = self._current_filename(now)
        # Buffered append; her event sonrası fsync gerekmiyor (crash-resistant
        # değil — sub-proje #5 öncesi log-only mod, kayıp tolere edilebilir).
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

        # Stdout — tek satır kısa görsel
        try:
            self._stdout.write(line + "\n")
            self._stdout.flush()
        except Exception:
            pass

    def close(self) -> None:
        """API simetrisi için no-op (her emit kendi handle'ını kapatıyor)."""
        return
