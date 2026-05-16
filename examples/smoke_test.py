"""SMC Engine smoke test entrypoint — Plan Faz 2, task 2.6.

Minimal D1 + H4 + M15 veri seti ile ``orchestrator.analyze()`` cagrisi:
MarketPicture uretilir, crash yok. Faz 2'den itibaren her faz sonunda
calistirilir (regresyon erken yakalanir).

Calistirma:
    cd smc-engine && python3 examples/smoke_test.py
Cikti: kisa MarketPicture ozeti + "SMOKE OK".
"""

from __future__ import annotations

import os
import sys

# Repo kokunu import path'ine ekle (examples/ alt dizininden calistirildiginda).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd

from smc_engine.config import SMCConfig
from smc_engine.orchestrator import analyze
from smc_engine.risk_guard import validate as risk_validate
from smc_engine.setup_builder import build as build_setup
from smc_engine.types import AccountState, Rejection, TimeFrame, ValidatedSetup


def _candle(o, h, l, c, v=1000.0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def _df(rows, start, freq):
    idx = pd.date_range(start=start, periods=len(rows), freq=freq)
    return pd.DataFrame(rows, index=idx)[
        ["open", "high", "low", "close", "volume"]
    ]


def _build_dataset() -> dict:
    """Minimal hizalanmis D1 + H4 + M15 set (5 gunluk pencere)."""
    # D1: yukselen + geri cekilmeli -> swing/structure uretir.
    d1_pattern = [6, 6, 6, -4, -4, 7, 7, 7, 7, -3, -3, 8, 8, 8, 8]
    d1_rows = []
    price = 100.0
    for d in d1_pattern:
        o = price
        c = price + d
        d1_rows.append(_candle(o, max(o, c) + 1, min(o, c) - 1, c))
        price = c
    d1 = _df(d1_rows, "2026-01-01", "D")

    # H4: range benzeri salinim + birkac OB.
    h4_rows = []
    base = 100.0
    for i in range(60):
        o = base + (i % 10) * 1.5 - (i // 10)
        c = o + (1.2 if i % 2 == 0 else -1.0)
        h4_rows.append(_candle(o, max(o, c) + 0.8, min(o, c) - 0.8, c))
    h4 = _df(h4_rows, "2026-01-01", "4h")

    # M15: yumusak yukselen seri.
    m15_rows = []
    for i in range(480):
        o = 100.0 + i * 0.1
        m15_rows.append(_candle(o, o + 0.3, o - 0.2, o + 0.08))
    m15 = _df(m15_rows, "2026-01-01", "15min")

    return {TimeFrame.D1: d1, TimeFrame.H4: h4, TimeFrame.M15: m15}


def main() -> int:
    config = SMCConfig()
    dataset = _build_dataset()

    # 1) Cache'siz cagri.
    picture = analyze(dataset, config)

    # 2) at_bar ile cagri (look-ahead onleme yolu).
    m15 = dataset[TimeFrame.M15]
    t = m15.index[300].to_pydatetime()
    picture_at = analyze(dataset, config, at_bar=t)

    # 3) Cache'li cagri (HTF cache yolu).
    cache: dict = {}
    _ = analyze(dataset, config, at_bar=t, cache=cache)
    picture_cached = analyze(dataset, config, at_bar=t, cache=cache)

    # --- Determinizm dogrulamasi: at_bar=t cache'li/cache'siz ozdes ---
    assert picture_at.htf_bias == picture_cached.htf_bias
    assert picture_at.htf_range == picture_cached.htf_range
    assert picture_at.at_timestamp == picture_cached.at_timestamp

    print("=== SMC Engine smoke test — Faz 2 ===")
    print(f"  TF'ler          : {[tf.value for tf in picture.per_tf]}")
    print(f"  htf_bias        : {picture.htf_bias.value}")
    print(
        f"  htf_range       : "
        f"{None if picture.htf_range is None else (picture.htf_range.low, picture.htf_range.high)}"
    )
    print(f"  at_timestamp    : {picture.at_timestamp}")
    print(f"  current_price   : {picture.current_price:.4f}")
    print(f"  active_pois     : {len(picture.active_pois)}")
    for tf, snap in picture.per_tf.items():
        print(
            f"   - {tf.value:>3}: bias={snap.bias.value:<8} "
            f"zones={len(snap.zones)} imb={len(snap.imbalances)} "
            f"levels={len(snap.levels)} liq={len(snap.liquidity_events)} "
            f"struct={len(snap.structure)}"
        )
    print(f"  at_bar cagrisi  : at_timestamp={picture_at.at_timestamp} (t={t})")
    print(f"  cache anahtari  : {len(cache)} giris")

    # 4) setup_builder — MarketPicture -> Setup | None (Faz 3).
    # ATR artik orchestrator tarafindan her TFSnapshot'a yazilir; setup_builder
    # picture.per_tf[H4].atr'den okur — manuel OHLCV baglama yok.
    setup = build_setup(picture, config)
    if setup is None:
        print("  setup_builder   : None (confluence esigi altinda / yon notr)")
    else:
        print(
            f"  setup_builder   : {setup.direction.value} "
            f"entry={setup.entry:.2f} sl={setup.sl:.2f} "
            f"tp={[round(x, 2) for x in setup.tp]} "
            f"rr={setup.rr:.2f} score={setup.confluence_score:.3f}"
        )
    if setup is not None:
        assert abs(sum(setup.tp_weights) - 1.0) < 1e-9

    # 5) risk_guard — Setup -> ValidatedSetup | Rejection (Faz 4).
    # Sentetik bir saglikli AccountState; Setup yoksa risk_guard atlanir.
    account = AccountState(
        equity=10_000.0,
        open_position=False,
        recent_results=[],
        consecutive_losses=0,
        max_drawdown_pct=0.0,
    )
    if setup is None:
        print("  risk_guard      : skip (setup None)")
    else:
        verdict = risk_validate(setup, account, config)
        if isinstance(verdict, ValidatedSetup):
            print(
                f"  risk_guard      : OK size={verdict.position_size:.4f} "
                f"risk={verdict.risk_amount:.2f} "
                f"gates={verdict.guard_log}"
            )
        else:  # Rejection
            assert isinstance(verdict, Rejection)
            print(
                f"  risk_guard      : REJECT gate={verdict.gate} "
                f"reason={verdict.reason}"
            )

    # 6) backtest harness — kucuk sentetik veri -> BacktestResult, crash yok (Faz 5).
    from backtest.harness import run as run_backtest
    from backtest.report import ratchet_metric_line
    bt = run_backtest(dataset, config, initial_equity=10_000.0)
    assert bt.trades is not None
    assert len(bt.equity_curve) > 0
    assert "trade_count" in bt.metrics
    print(
        f"  harness         : trades={len(bt.trades)} "
        f"equity_bars={len(bt.equity_curve)} "
        f"final_equity={bt.equity_curve.iloc[-1]:.2f}"
    )
    print(f"  {ratchet_metric_line(bt)}")

    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
