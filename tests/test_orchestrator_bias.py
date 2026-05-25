"""_bias_from_snapshot — D1 EMA trend override (Spec 2026-05-24)."""
import pandas as pd
import pytest
from datetime import datetime, timedelta, timezone

from smc_engine.config import SMCConfig
from smc_engine.orchestrator import _bias_from_snapshot
from smc_engine.types import (
    Bias, Direction, StructureBreak, StructureKind, TimeFrame,
)


def _make_df(closes: list[float], start: datetime | None = None) -> pd.DataFrame:
    start = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
    if not closes:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    idx = pd.DatetimeIndex(
        [start + timedelta(days=i) for i in range(len(closes))]
    )
    return pd.DataFrame({
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1.0] * len(closes),
    }, index=idx)


def _make_break(direction: Direction, ts: datetime | None = None) -> StructureBreak:
    return StructureBreak(
        kind=StructureKind.BOS,
        direction=direction,
        broken_swing_price=100.0,
        confirm_candle_ts=ts or datetime(2024, 1, 10, tzinfo=timezone.utc),
        timeframe=TimeFrame.D1,
    )


# ============================================================
# CYCLE A — Core EMA path
# ============================================================


def _ema_on_cfg() -> SMCConfig:
    """Helper: SMCConfig with EMA explicitly enabled (default is False
    since 2026-05-25 validation rolled it back to opt-in)."""
    cfg = SMCConfig()
    cfg.bias_use_d1_ema_trend = True
    return cfg


def test_bias_ema_d1_close_above_returns_bullish():
    """tf=D1, 150 bar monotonic up → close > ema → BULLISH."""
    df = _make_df([100 + i * 0.5 for i in range(150)])
    bias = _bias_from_snapshot(df, [], None, TimeFrame.D1, _ema_on_cfg())
    assert bias == Bias.BULLISH


def test_bias_ema_d1_close_below_returns_bearish():
    """tf=D1, 150 bar monotonic down → close < ema → BEARISH."""
    df = _make_df([100 - i * 0.5 for i in range(150)])
    bias = _bias_from_snapshot(df, [], None, TimeFrame.D1, _ema_on_cfg())
    assert bias == Bias.BEARISH


def test_bias_ema_default_config_none_uses_ema():
    """tf=D1, 150 bar uptrend + config=None → EMA path (BULLISH)."""
    df = _make_df([100 + i * 0.5 for i in range(150)])
    bias = _bias_from_snapshot(df, [], None, TimeFrame.D1, None)
    assert bias == Bias.BULLISH


# ============================================================
# CYCLE B — TF gating + config-disabled + insufficient bars
# ============================================================


def test_bias_ema_d1_close_equal_returns_bullish():
    """tf=D1, 150 bar sabit close → close == ema → BULLISH (equality up).
    EMA path explicit (default 2026-05-25'ten beri opt-in)."""
    df = _make_df([100.0] * 150)
    bias = _bias_from_snapshot(df, [], None, TimeFrame.D1, _ema_on_cfg())
    # Sabit seriede ewm = sabit; assert equality via approx
    closes = df["close"]
    ema = closes.ewm(span=50, adjust=False).mean().iloc[-1]
    assert ema == pytest.approx(closes.iloc[-1])
    assert bias == Bias.BULLISH


def test_bias_ema_insufficient_bars_falls_back_to_structure():
    """tf=D1, 30 bar (<50) + structure → last break direction."""
    df = _make_df([100.0] * 30)
    br = _make_break(Direction.SHORT)
    bias = _bias_from_snapshot(df, [br], None, TimeFrame.D1, SMCConfig())
    assert bias == Bias.BEARISH


def test_bias_ema_disabled_falls_back_to_structure():
    """tf=D1, 150 bar + structure + config.bias_use_d1_ema_trend=False → structure."""
    df = _make_df([100 + i * 0.5 for i in range(150)])  # EMA path BULLISH derdi
    br = _make_break(Direction.SHORT)
    cfg = SMCConfig()
    cfg.bias_use_d1_ema_trend = False
    bias = _bias_from_snapshot(df, [br], None, TimeFrame.D1, cfg)
    assert bias == Bias.BEARISH  # structure öncelikli


def test_bias_ema_non_d1_tf_skips_ema():
    """tf=H4 + EMA on, 150 bar uptrend + structure SHORT → structure (EMA bypass).
    EMA explicit on — gating TF gereği bypass'i kanıtlamak için."""
    df = _make_df([100 + i * 0.5 for i in range(150)])  # EMA D1-only → bypass
    br = _make_break(Direction.SHORT)
    bias = _bias_from_snapshot(df, [br], None, TimeFrame.H4, _ema_on_cfg())
    assert bias == Bias.BEARISH  # structure (EMA D1-only)


# ============================================================
# CYCLE C — Eski close-trend + empty-df fallback regression koruması
# ============================================================


def test_bias_fallback_close_trend_bullish():
    """5 bar + 1% rise + structure=[] → close-trend BULLISH."""
    df = _make_df([100.0, 100.3, 100.6, 100.8, 101.0])  # +1%
    bias = _bias_from_snapshot(df, [], None, TimeFrame.D1, SMCConfig())
    assert bias == Bias.BULLISH


def test_bias_fallback_close_trend_bearish():
    """5 bar + 1% fall + structure=[] → close-trend BEARISH."""
    df = _make_df([100.0, 99.7, 99.4, 99.2, 99.0])  # -1%
    bias = _bias_from_snapshot(df, [], None, TimeFrame.D1, SMCConfig())
    assert bias == Bias.BEARISH


def test_bias_fallback_neutral_empty_df():
    """len(df)==0 + structure=[] → NEUTRAL."""
    df = _make_df([])
    bias = _bias_from_snapshot(df, [], None, TimeFrame.D1, SMCConfig())
    assert bias == Bias.NEUTRAL
