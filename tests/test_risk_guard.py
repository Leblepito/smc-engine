"""TDD test'leri — smc_engine/risk_guard.py (Plan Faz 4, task 4.1).

``validate(setup: Setup, account_state: AccountState, config)
    -> ValidatedSetup | Rejection``

risk_guard hard gate'leri sirayla uygular; ilk basarisiz gate
``Rejection(reason, gate, setup)`` dondurur. Hepsi gecerse
``ValidatedSetup(setup, position_size, risk_amount, guard_log)`` doner.

Gate uygulama sirasi (impl ile ayni):
  confluence -> regime -> deviation -> no_sl -> min_rr -> averaging ->
  drawdown_breaker -> session -> funding -> r_sizing

Tasarim kararlari:
  A — confluence gate = Setup.confluence_factor_count < min_confluence_factors
  B — averaging gate v1 = account_state.open_position is True
  C — asset_class "forex" -> session/hafta sonu gate;
      "crypto" -> funding window gate
  D — deviation savunmasi = setup.confirmation is None -> Rejection
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from smc_engine.config import SMCConfig
from smc_engine.risk_guard import validate
from smc_engine.types import (
    AccountState,
    Bias,
    Direction,
    POIKind,
    POIRef,
    Rejection,
    Setup,
    StructureBreak,
    StructureKind,
    TimeFrame,
    ValidatedSetup,
    Zone,
    ZoneAnchor,
    ZoneKind,
    ZoneStatus,
)

UTC = timezone.utc

# Hafta ici, seans/funding'den uzak bir referans an:
#   2026-03-04 = Carsamba; 03:17 UTC -> funding window'lardan (0/8/16) uzak,
#   forex seansi acik (londra/tokyo), hafta sonu degil.
GOOD_TS = datetime(2026, 3, 4, 3, 17, tzinfo=UTC)


# ============================================================
# Yardimcilar — sentetik Setup / AccountState insa
# ============================================================


def _zone(kind=ZoneKind.DEMAND, top=82.0, bottom=78.0):
    return Zone(
        kind=kind,
        top=top,
        bottom=bottom,
        timeframe=TimeFrame.H4,
        created_at=GOOD_TS,
        status=ZoneStatus.FRESH,
        origin_candle_ts=GOOD_TS,
        anchor=ZoneAnchor.BODY,
        age_bars=5,
    )


def _poi(kind=ZoneKind.DEMAND):
    return POIRef(
        kind=POIKind.ZONE, ref=_zone(kind=kind), htf_aligned=True, score_hint=1.0
    )


def _confirmation(direction=Direction.LONG):
    return StructureBreak(
        kind=StructureKind.CHoCH,
        direction=direction,
        broken_swing_price=80.0,
        confirm_candle_ts=GOOD_TS,
        timeframe=TimeFrame.M15,
    )


def _setup(
    *,
    direction=Direction.LONG,
    entry=82.0,
    sl=78.0,
    tp=None,
    confirmation="default",
    bias_context=Bias.BULLISH,
    confluence_score=0.7,
    confluence_factor_count=4,
    rr=2.0,
    created_at=GOOD_TS,
    poi_kind=ZoneKind.DEMAND,
):
    """Varsayilan: tum gate'leri GECEN gecerli LONG setup."""
    if tp is None:
        tp = [86.0, 92.0, 100.0]
    if confirmation == "default":
        confirmation = _confirmation(direction)
    return Setup(
        direction=direction,
        entry=entry,
        sl=sl,
        tp=tp,
        tp_weights=[0.5, 0.3, 0.2],
        poi=_poi(kind=poi_kind),
        confirmation=confirmation,
        bias_context=bias_context,
        confluence_score=confluence_score,
        rr=rr,
        created_at=created_at,
        confluence_factor_count=confluence_factor_count,
    )


def _account(
    *,
    equity=10_000.0,
    open_position=False,
    recent_results=None,
    consecutive_losses=0,
    max_drawdown_pct=0.0,
):
    """Varsayilan: tum gate'leri GECEN saglikli hesap."""
    return AccountState(
        equity=equity,
        open_position=open_position,
        recent_results=recent_results if recent_results is not None else [],
        consecutive_losses=consecutive_losses,
        max_drawdown_pct=max_drawdown_pct,
    )


@pytest.fixture
def config():
    # asset_class default "crypto"; forex testleri kendi config'ini kurar.
    return SMCConfig()


# ============================================================
# 1 — confluence gate (karar A)
# ============================================================


def test_confluence_gate(config):
    """confluence_factor_count < min_confluence_factors -> Rejection."""
    setup = _setup(confluence_factor_count=1)  # min varsayilan 2
    res = validate(setup, _account(), config)
    assert isinstance(res, Rejection)
    assert res.gate == "confluence"
    assert res.setup is setup
    assert res.reason  # bos olmayan gerekce

    # Tam sinirda (== min) gecmeli.
    ok = _setup(confluence_factor_count=config.min_confluence_factors)
    assert isinstance(validate(ok, _account(), config), ValidatedSetup)


# ============================================================
# 2 — regime filtresi
# ============================================================


def test_regime_filter(config):
    """HTF bias NEUTRAL veya setup yonune ters -> Rejection(gate='regime')."""
    # NEUTRAL bias.
    neutral = _setup(bias_context=Bias.NEUTRAL)
    r1 = validate(neutral, _account(), config)
    assert isinstance(r1, Rejection) and r1.gate == "regime"

    # LONG setup ama bias BEARISH (ters).
    contra = _setup(direction=Direction.LONG, bias_context=Bias.BEARISH)
    r2 = validate(contra, _account(), config)
    assert isinstance(r2, Rejection) and r2.gate == "regime"

    # SHORT setup + BEARISH bias -> uyumlu, regime gecmeli.
    aligned_short = _setup(
        direction=Direction.SHORT,
        bias_context=Bias.BEARISH,
        entry=78.0,
        sl=82.0,
        tp=[74.0, 68.0, 60.0],
        confirmation=_confirmation(Direction.SHORT),
        poi_kind=ZoneKind.SUPPLY,
    )
    assert isinstance(validate(aligned_short, _account(), config), ValidatedSetup)


# ============================================================
# 3 — deviation savunmasi (karar D)
# ============================================================


def test_deviation_defense(config):
    """confirmation None -> Rejection(gate='deviation')."""
    setup = _setup(confirmation=None)
    res = validate(setup, _account(), config)
    assert isinstance(res, Rejection)
    assert res.gate == "deviation"

    # confirmation varsa deviation gate gecmeli.
    ok = _setup(confirmation=_confirmation(Direction.LONG))
    assert isinstance(validate(ok, _account(), config), ValidatedSetup)


# ============================================================
# 4 — yapisal hard stop zorunlu
# ============================================================


def test_structural_sl(config):
    """SL yok / yanlis tarafta -> Rejection(gate='no_sl')."""
    # SL None.
    no_sl = _setup(sl=None)
    r1 = validate(no_sl, _account(), config)
    assert isinstance(r1, Rejection) and r1.gate == "no_sl"

    # LONG ama SL entry'nin USTUNDE (yanlis taraf).
    bad_long = _setup(direction=Direction.LONG, entry=82.0, sl=85.0)
    r2 = validate(bad_long, _account(), config)
    assert isinstance(r2, Rejection) and r2.gate == "no_sl"

    # SHORT ama SL entry'nin ALTINDA (yanlis taraf).
    bad_short = _setup(
        direction=Direction.SHORT,
        bias_context=Bias.BEARISH,
        entry=78.0,
        sl=74.0,
        tp=[74.0, 68.0, 60.0],
        confirmation=_confirmation(Direction.SHORT),
        poi_kind=ZoneKind.SUPPLY,
    )
    r3 = validate(bad_short, _account(), config)
    assert isinstance(r3, Rejection) and r3.gate == "no_sl"

    # SL = entry -> mesafe 0 -> red.
    zero = _setup(entry=82.0, sl=82.0)
    r4 = validate(zero, _account(), config)
    assert isinstance(r4, Rejection) and r4.gate == "no_sl"


# ============================================================
# 5 — min R:R gate
# ============================================================


def test_min_rr(config):
    """setup.rr < config.min_rr -> Rejection(gate='min_rr')."""
    low = _setup(rr=1.0)  # min_rr varsayilan 1.5
    res = validate(low, _account(), config)
    assert isinstance(res, Rejection)
    assert res.gate == "min_rr"

    # Tam sinirda (== min_rr) gecmeli.
    edge = _setup(rr=config.min_rr)
    assert isinstance(validate(edge, _account(), config), ValidatedSetup)


# ============================================================
# 6 — pacal yasak / averaging ban (karar B)
# ============================================================


def test_averaging_ban(config):
    """account_state.open_position is True -> Rejection(gate='averaging')."""
    setup = _setup()
    res = validate(setup, _account(open_position=True), config)
    assert isinstance(res, Rejection)
    assert res.gate == "averaging"

    # open_position False -> averaging gate gecmeli.
    assert isinstance(
        validate(setup, _account(open_position=False), config), ValidatedSetup
    )


# ============================================================
# 7 — R-bazli sizing
# ============================================================


def test_r_sizing(config):
    """position_size = risk_amount / |entry - SL|; risk_amount = equity*risk_pct."""
    setup = _setup(entry=82.0, sl=78.0)  # |entry-SL| = 4.0
    acc = _account(equity=10_000.0)
    res = validate(setup, acc, config)
    assert isinstance(res, ValidatedSetup)

    expected_risk = 10_000.0 * config.risk_pct  # 100.0
    expected_size = expected_risk / 4.0  # 25.0
    assert res.risk_amount == pytest.approx(expected_risk)
    assert res.position_size == pytest.approx(expected_size)

    # Farkli equity -> orantili olcek.
    res2 = validate(setup, _account(equity=50_000.0), config)
    assert res2.risk_amount == pytest.approx(50_000.0 * config.risk_pct)
    assert res2.position_size == pytest.approx(
        (50_000.0 * config.risk_pct) / 4.0
    )


# ============================================================
# 8 — drawdown devre kesici
# ============================================================


def test_drawdown_breaker(config):
    """N ardisik zarar VEYA max-DD esigi -> Rejection(gate='drawdown_breaker')."""
    # consecutive_losses >= max_consecutive_losses.
    losses = _account(consecutive_losses=config.max_consecutive_losses)
    r1 = validate(_setup(), losses, config)
    assert isinstance(r1, Rejection) and r1.gate == "drawdown_breaker"

    # max_drawdown_pct >= config.max_drawdown_pct.
    dd = _account(max_drawdown_pct=config.max_drawdown_pct)
    r2 = validate(_setup(), dd, config)
    assert isinstance(r2, Rejection) and r2.gate == "drawdown_breaker"

    # Esigin altinda -> gecmeli.
    healthy = _account(
        consecutive_losses=config.max_consecutive_losses - 1,
        max_drawdown_pct=config.max_drawdown_pct - 0.01,
    )
    assert isinstance(validate(_setup(), healthy, config), ValidatedSetup)


# ============================================================
# 9 — seans farkindaligi (karar C — forex)
# ============================================================


def test_session_awareness(config):
    """asset_class forex + hafta sonu -> Rejection(gate='session')."""
    forex_cfg = SMCConfig()
    forex_cfg.asset_class = "forex"

    # 2026-03-07 = Cumartesi -> hafta sonu.
    weekend_ts = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    setup = _setup(created_at=weekend_ts)
    res = validate(setup, _account(), forex_cfg)
    assert isinstance(res, Rejection)
    assert res.gate == "session"

    # Hafta ici forex -> session gate gecmeli.
    ok = _setup(created_at=GOOD_TS)
    assert isinstance(validate(ok, _account(), forex_cfg), ValidatedSetup)


# ============================================================
# 10 — funding tamponu (karar C — crypto)
# ============================================================


def test_funding_buffer(config):
    """asset_class crypto + funding window +/-buffer -> Rejection(gate='funding')."""
    # 08:00 UTC = funding window; tam ustunde -> tampon icinde.
    funding_ts = datetime(2026, 3, 4, 8, 0, tzinfo=UTC)
    setup = _setup(created_at=funding_ts)
    res = validate(setup, _account(), config)  # config default crypto
    assert isinstance(res, Rejection)
    assert res.gate == "funding"

    # Tampon icinde (08:20, buffer 30dk) -> yine red.
    near_ts = datetime(2026, 3, 4, 8, 20, tzinfo=UTC)
    res2 = validate(_setup(created_at=near_ts), _account(), config)
    assert isinstance(res2, Rejection) and res2.gate == "funding"

    # Funding'den uzak -> funding gate gecmeli.
    ok = _setup(created_at=GOOD_TS)
    assert isinstance(validate(ok, _account(), config), ValidatedSetup)


# ============================================================
# 11 — varlik profili — asset_class'a gore dogru gate
# ============================================================


def test_asset_profile(config):
    """asset_class'a gore dogru zaman gate'i uygulanir."""
    # crypto: hafta sonu funding'den uzaksa SORUN DEGIL (funding gate, session degil).
    weekend_ts = datetime(2026, 3, 7, 3, 17, tzinfo=UTC)  # Cmt, funding'den uzak
    crypto_cfg = SMCConfig()  # crypto
    assert crypto_cfg.asset_class == "crypto"
    res_crypto = validate(_setup(created_at=weekend_ts), _account(), crypto_cfg)
    assert isinstance(res_crypto, ValidatedSetup)  # crypto hafta sonu acik

    # forex: funding window saatinde ama hafta ici -> SORUN DEGIL
    # (forex'te funding gate yok).
    funding_ts = datetime(2026, 3, 4, 8, 0, tzinfo=UTC)  # Carsamba, funding saati
    forex_cfg = SMCConfig()
    forex_cfg.asset_class = "forex"
    res_forex = validate(_setup(created_at=funding_ts), _account(), forex_cfg)
    assert isinstance(res_forex, ValidatedSetup)  # forex funding'i umursamaz

    # forex hafta sonu -> session gate uygulanir.
    weekend_forex = validate(
        _setup(created_at=weekend_ts), _account(), forex_cfg
    )
    assert isinstance(weekend_forex, Rejection)
    assert weekend_forex.gate == "session"

    # crypto funding saati -> funding gate uygulanir.
    crypto_funding = validate(
        _setup(created_at=funding_ts), _account(), crypto_cfg
    )
    assert isinstance(crypto_funding, Rejection)
    assert crypto_funding.gate == "funding"


# ============================================================
# 12 — tum gate'leri gecen gecerli setup
# ============================================================


def test_valid_setup(config):
    """Tum gate'leri gecen setup -> ValidatedSetup, guard_log dolu."""
    setup = _setup()
    res = validate(setup, _account(), config)
    assert isinstance(res, ValidatedSetup)
    assert res.setup is setup
    assert res.position_size > 0
    assert res.risk_amount > 0
    # guard_log gecilen gate'lerin listesi — dolu olmali.
    assert isinstance(res.guard_log, list)
    assert len(res.guard_log) > 0
    # Beklenen gate isimleri guard_log'da.
    for gate in (
        "confluence",
        "regime",
        "deviation",
        "no_sl",
        "min_rr",
        "averaging",
        "drawdown_breaker",
    ):
        assert gate in res.guard_log

    # Deterministik — ayni input ayni cikti.
    res2 = validate(_setup(), _account(), config)
    assert res2.position_size == pytest.approx(res.position_size)
    assert res2.guard_log == res.guard_log
