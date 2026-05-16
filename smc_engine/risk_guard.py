"""SMC Engine risk_guard — hard gate'ler + R-bazli sizing — Spec §6.

``validate(setup: Setup, account_state: AccountState, config)
    -> ValidatedSetup | Rejection``

Konum (Spec §6): ``setup_builder`` aday ``Setup`` uretir -> ``risk_guard`` suzer
-> ``ValidatedSetup`` (gecerli) veya ``Rejection`` (gerekceli). Reddedilenler
ratchet icin loglanir.

**Gate'ler sirayla uygulanir; ilk basarisiz gate hemen ``Rejection`` dondurur.**
Uygulama sirasi (ucuz/yapisal -> zaman bagimli -> sizing):

  1. confluence       — Setup.confluence_factor_count < min_confluence_factors
  2. regime           — HTF bias NEUTRAL veya setup yonune ters
  3. deviation        — confirmation None (M15 CHoCH/BOS onayi yok)
  4. no_sl            — SL yok / yanlis tarafta / mesafe 0
  5. min_rr           — setup.rr < config.min_rr
  6. averaging        — account_state.open_position True (tek-pozisyon kurali)
  7. drawdown_breaker — N ardisik zarar VEYA max-DD esigi asildi
  8. session          — (forex) hafta sonu / seans-disi
  9. funding          — (crypto) funding window +/- buffer
  10. (sizing)        — R-bazli pozisyon boyutu hesaplanir

Tasarim kararlari (system-design taramasi):
  A — confluence gate, ``Setup.confluence_factor_count`` (sifir-olmayan faktor
      sayisi) ile ``config.min_confluence_factors`` karsilastirir.
  B — averaging gate v1: ``account_state.open_position is True`` -> red
      (AccountState v1 acik pozisyon yonu/P&L tasimaz; tek-pozisyon kurali).
  C — ``config.asset_class`` zaman gate'ini secer: "forex" -> seans/hafta sonu
      gate; "crypto" -> funding window gate.
  D — deviation savunmasi: ``setup.confirmation is None`` -> red. M15 CHoCH/BOS
      hard gate'i boyle uygulanir (deviation = stop tetigi, asla entry).

``validate()`` saf/deterministik: ayni (setup, account_state, config) -> ayni
cikti. Yan etki yok; ``time_utils`` salt zaman hesabi icin import edilir.
``setup.created_at`` seans/funding kontrolunde kullanilir (timestamp bazli).
"""

from __future__ import annotations

from smc_engine import time_utils
from smc_engine.types import (
    AccountState,
    Bias,
    Direction,
    Rejection,
    Setup,
    ValidatedSetup,
)

# Gate uygulama sirasi — guard_log ve dokumantasyon referansi.
GATE_ORDER: tuple[str, ...] = (
    "confluence",
    "regime",
    "deviation",
    "no_sl",
    "min_rr",
    "averaging",
    "drawdown_breaker",
    "session",
    "funding",
)


# ============================================================
# Gate yardimcilari — her biri (gerekce | None) dondurur.
#   None -> gate gecti; str -> gate basarisiz (Rejection gerekcesi).
# ============================================================


def _check_confluence(setup: Setup, config) -> str | None:
    """Karar A — sifir-olmayan confluence faktoru sayisi yeterli mi."""
    min_factors = getattr(config, "min_confluence_factors", 2)
    count = getattr(setup, "confluence_factor_count", 0)
    if count < min_factors:
        return (
            f"confluence faktoru yetersiz: {count} < {min_factors} "
            f"(min_confluence_factors)"
        )
    return None


def _check_regime(setup: Setup) -> str | None:
    """Regime filtresi — HTF bias net ve setup yonuyle uyumlu mu (anti-testere)."""
    bias = setup.bias_context
    if bias == Bias.NEUTRAL:
        return "HTF bias NEUTRAL — net trend yok, no trade"
    if setup.direction == Direction.LONG and bias != Bias.BULLISH:
        return f"LONG setup ama HTF bias {bias.value} — yon ters"
    if setup.direction == Direction.SHORT and bias != Bias.BEARISH:
        return f"SHORT setup ama HTF bias {bias.value} — yon ters"
    return None


def _check_deviation(setup: Setup) -> str | None:
    """Karar D — M15 CHoCH/BOS onayi (confirmation) zorunlu.

    confirmation None -> deviation savunmasi: kirilim teyit edilmemis,
    deviation stop tetigidir, asla entry.
    """
    if setup.confirmation is None:
        return "M15 CHoCH/BOS onayi yok — deviation savunmasi (confirmation zorunlu)"
    return None


def _check_no_sl(setup: Setup) -> str | None:
    """Yapisal hard stop zorunlu — SL var, dogru tarafta ve mesafe > 0."""
    if setup.sl is None:
        return "SL yok — yapisal hard stop zorunlu"
    if setup.direction == Direction.LONG:
        if setup.sl >= setup.entry:
            return (
                f"LONG SL ({setup.sl}) entry'nin ({setup.entry}) altinda olmali"
            )
    else:  # SHORT
        if setup.sl <= setup.entry:
            return (
                f"SHORT SL ({setup.sl}) entry'nin ({setup.entry}) ustunde olmali"
            )
    if abs(setup.entry - setup.sl) <= 0:
        return "SL mesafesi 0 — gecersiz stop"
    return None


def _check_min_rr(setup: Setup, config) -> str | None:
    """Min R:R gate — rr < min_rr -> kotu setup (SL cok uzak / TP cok yakin)."""
    min_rr = getattr(config, "min_rr", 1.5)
    if setup.rr < min_rr:
        return f"R:R yetersiz: {setup.rr:.3f} < {min_rr} (min_rr)"
    return None


def _check_averaging(account_state: AccountState) -> str | None:
    """Karar B — pacal yasak v1: acik pozisyon varsa yeni giris yok."""
    if account_state.open_position:
        return "acik pozisyon var — pacal yasak (tek-pozisyon kurali)"
    return None


def _check_drawdown_breaker(account_state: AccountState, config) -> str | None:
    """Drawdown devre kesici — N ardisik zarar VEYA max-DD esigi asildi."""
    max_losses = getattr(config, "max_consecutive_losses", 5)
    max_dd = getattr(config, "max_drawdown_pct", 0.10)
    if account_state.consecutive_losses >= max_losses:
        return (
            f"ardisik zarar devre kesici: "
            f"{account_state.consecutive_losses} >= {max_losses}"
        )
    if account_state.max_drawdown_pct >= max_dd:
        return (
            f"max drawdown devre kesici: "
            f"{account_state.max_drawdown_pct:.3f} >= {max_dd}"
        )
    return None


def _check_session(setup: Setup) -> str | None:
    """Karar C (forex) — hafta sonu / seans-disi giris yok.

    v1: hafta sonu (Cmt/Pzr) -> red. ``setup.created_at`` kullanilir.
    """
    if time_utils.is_weekend(setup.created_at):
        return "forex hafta sonu — seans kapali, giris yok"
    return None


def _check_funding(setup: Setup, config) -> str | None:
    """Karar C (crypto) — funding window +/- buffer icinde giris yok."""
    buffer = getattr(config, "funding_buffer_minutes", 30)
    if time_utils.is_near_funding(setup.created_at, buffer_minutes=buffer):
        return (
            f"kripto funding window +/-{buffer}dk tamponu icinde — giris yok"
        )
    return None


# ============================================================
# Ana giris noktasi
# ============================================================


def validate(
    setup: Setup, account_state: AccountState, config
) -> ValidatedSetup | Rejection:
    """``Setup``'i hard gate'lerden gecir; ``ValidatedSetup`` veya ``Rejection``.

    Gate'ler ``GATE_ORDER`` sirasinda uygulanir; ilk basarisiz gate hemen
    ``Rejection(reason, gate, setup)`` dondurur. Hepsi gecerse R-bazli sizing
    hesaplanir ve ``ValidatedSetup(setup, position_size, risk_amount,
    guard_log)`` doner — ``guard_log`` gecilen gate'lerin (sirali) listesi.

    Karar C: ``config.asset_class`` zaman gate'ini secer — "forex" -> session
    gate, "crypto" -> funding gate. Diger asset_class degerleri -> ne session
    ne funding gate (varsayilan guvenli: zaman gate'i atlanir).

    Saf/deterministik — yan etki yok.
    """
    guard_log: list[str] = []
    asset_class = getattr(config, "asset_class", "crypto")

    # (gate_adi, gerekce | None) ciftleri — sirayla.
    checks: list[tuple[str, str | None]] = [
        ("confluence", _check_confluence(setup, config)),
        ("regime", _check_regime(setup)),
        ("deviation", _check_deviation(setup)),
        ("no_sl", _check_no_sl(setup)),
        ("min_rr", _check_min_rr(setup, config)),
        ("averaging", _check_averaging(account_state)),
        ("drawdown_breaker", _check_drawdown_breaker(account_state, config)),
    ]

    # Karar C — zaman gate'i asset_class'a gore.
    if asset_class == "forex":
        checks.append(("session", _check_session(setup)))
    elif asset_class == "crypto":
        checks.append(("funding", _check_funding(setup, config)))
    # diger asset_class -> zaman gate'i yok (guvenli varsayilan).

    for gate, reason in checks:
        if reason is not None:
            return Rejection(reason=reason, gate=gate, setup=setup)
        guard_log.append(gate)

    # --- R-bazli sizing (tum gate'ler gecti) ---
    # risk_amount = equity * risk_pct; position_size = risk_amount / |entry-SL|.
    risk_pct = getattr(config, "risk_pct", 0.01)
    risk_amount = account_state.equity * risk_pct
    sl_distance = abs(setup.entry - setup.sl)
    # sl_distance > 0 garantili: no_sl gate mesafe 0'i zaten reddetti.
    position_size = risk_amount / sl_distance
    guard_log.append("r_sizing")

    return ValidatedSetup(
        setup=setup,
        position_size=position_size,
        risk_amount=risk_amount,
        guard_log=guard_log,
    )
