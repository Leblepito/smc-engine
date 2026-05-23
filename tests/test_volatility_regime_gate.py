"""risk_guard volatility_regime gate testleri (Spec §13.2, 2026-05-23).

Bu gate Setup.regime_metrics['atr_percentile'] degerini config
esigiyle karsilastirir. Esigi asarsa -> Rejection(gate='volatility_regime').
Disabled veya regime_metrics yok/eksik -> gate atlanir (None).

_make_setup() helper'i TUM diger risk_guard gate'lerini gecen bir Setup
uretir; boylece volatility_regime gate'inin davranisini izole test ederiz.
"""

from datetime import datetime, timezone

from smc_engine import risk_guard
from smc_engine.config import SMCConfig
from smc_engine.types import (
    AccountState,
    Bias,
    Direction,
    Level,
    LevelKind,
    POIKind,
    POIRef,
    Rejection,
    Setup,
    StructureBreak,
    StructureKind,
    TimeFrame,
    ValidatedSetup,
)


# 2024-06-04 Sali 12:00 UTC -> hafta sonu degil, funding window
# (0/8/16 UTC) +/-30dk disinda. Boylece session/funding gate'leri pas gecer.
_SAFE_TS = datetime(2024, 6, 4, 12, 0, tzinfo=timezone.utc)


def _make_setup(regime_metrics: dict) -> Setup:
    """Volatility_regime DISINDAKI tum gate'leri gecen Setup.

    - confluence: confluence_factor_count=2 (default min=2)
    - regime: BULLISH bias + LONG direction
    - deviation: M15 StructureBreak confirmation verilir
    - no_sl: SL entry'nin altinda (LONG)
    - min_rr: rr=2.0 (default min=1.5)
    - averaging/drawdown: _make_account() ile garanti
    - funding/session: _SAFE_TS guvenli zaman
    """
    level = Level(
        kind=LevelKind.YO,
        price=100.0,
        timeframe=TimeFrame.D1,
        valid_from=_SAFE_TS,
        valid_until=None,
    )
    poi = POIRef(
        kind=POIKind.LEVEL,
        ref=level,
        htf_aligned=True,
        score_hint=0.5,
    )
    confirmation = StructureBreak(
        kind=StructureKind.CHoCH,
        direction=Direction.LONG,
        broken_swing_price=99.5,
        confirm_candle_ts=_SAFE_TS,
        timeframe=TimeFrame.M15,
    )
    return Setup(
        direction=Direction.LONG,
        entry=100.0,
        sl=98.0,
        tp=[104.0],
        tp_weights=[1.0],
        poi=poi,
        confirmation=confirmation,
        bias_context=Bias.BULLISH,
        confluence_score=0.7,
        rr=2.0,
        created_at=_SAFE_TS,
        confluence_factor_count=2,
        regime_metrics=regime_metrics,
    )


def _make_account() -> AccountState:
    """Tum account-bazli gate'leri gecen taze hesap."""
    return AccountState(
        equity=10_000.0,
        open_position=False,
        consecutive_losses=0,
        max_drawdown_pct=0.0,
    )


def test_volatility_regime_gate_vetos_high_atr_setup():
    """ATR percentile > 0.80 esik -> Rejection(gate='volatility_regime')."""
    setup = _make_setup(regime_metrics={"atr_percentile": 0.85})
    account = _make_account()
    cfg = SMCConfig()
    result = risk_guard.validate(setup, account, cfg)
    assert isinstance(result, Rejection), f"yuksek ATR vetolanmaliydi, geldi: {result!r}"
    assert result.gate == "volatility_regime"
    assert "ATR percentile" in result.reason


def test_volatility_regime_gate_admits_low_atr_setup():
    """ATR percentile <= esik -> ValidatedSetup (gate gecer)."""
    setup = _make_setup(regime_metrics={"atr_percentile": 0.50})
    account = _make_account()
    cfg = SMCConfig()
    result = risk_guard.validate(setup, account, cfg)
    assert isinstance(result, ValidatedSetup), (
        f"dusuk ATR ile setup gecmeliydi, geldi: {result!r}"
    )


def test_volatility_regime_gate_disabled_passes_all():
    """Filtre kapali (enabled=False) -> 0.99 bile gate'i tetiklemez."""
    setup = _make_setup(regime_metrics={"atr_percentile": 0.99})
    account = _make_account()
    cfg = SMCConfig(atr_regime_filter_enabled=False)
    result = risk_guard.validate(setup, account, cfg)
    assert isinstance(result, ValidatedSetup), (
        f"filtre disabled iken setup gecmeliydi, geldi: {result!r}"
    )


def test_volatility_regime_gate_missing_metrics_passes():
    """regime_metrics bos (warm-up) -> gate atlanir, setup gecer."""
    setup = _make_setup(regime_metrics={})
    account = _make_account()
    cfg = SMCConfig()
    result = risk_guard.validate(setup, account, cfg)
    assert isinstance(result, ValidatedSetup), (
        f"metrics yokken (warm-up) setup gecmeliydi, geldi: {result!r}"
    )
