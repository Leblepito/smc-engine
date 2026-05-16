"""Faz 5.2 + 5.3 — Backtest bar-replay harness.

Spec §8 bar-replay dongusu. Look-ahead bias YOK: ``t`` kapanisinda uretilen
setup ``t+1``'de dolar (fill modeli cozer). Tek pozisyon kurali. ``account_state``
her M15 barinda guncellenir. HTF cache (orchestrator §7.1) tutulur ve gecirilir.
``position_manager``'a delegasyon.

3-state dongu:
  pozisyon-yok -> (setup_builder + risk_guard) -> "pending fill"
              -> (fill modeli cozer) -> pozisyon-acik
              -> (position_manager kapatir) -> pozisyon-yok

Fill modelleri:
  next_open    — t'de uretilen setup t+1 acilisinda fill (+ slippage/spread).
                 Deterministik, varsayilan.
  limit_retest — POI entry bir limit; ``limit_retest_bars`` icinde retest
                 olursa fill, olmazsa pending expire.

İmza:
    run(ohlcv_by_tf, config, initial_equity=10_000.0, use_cache=True)
        -> BacktestResult
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from smc_engine.orchestrator import analyze
from smc_engine.risk_guard import validate as risk_validate
from smc_engine.setup_builder import build as build_setup
from smc_engine.types import (
    AccountState,
    BacktestResult,
    Direction,
    Position,
    TimeFrame,
    Trade,
    ValidatedSetup,
)
from backtest import metrics as _metrics
from backtest.position_manager import open_position, update as pm_update


# ============================================================
# Pending fill — 3-state dongunun ara durumu
# ============================================================


@dataclass
class _Pending:
    """Setup uretildi, henuz fill olmadi (pending fill state)."""

    validated: ValidatedSetup
    created_idx: int  # M15 bar indeksi (setup'in uretildigi bar)


# ============================================================
# Yardimcilar
# ============================================================


# U-3: _normalize ortak yardimciya tasindi (backtest/_utils.py).
from backtest._utils import normalize_ohlcv_by_tf as _normalize  # noqa: E402


def _mark_to_market(position: Optional[Position], close: float) -> float:
    """Acik pozisyonun bu kapanis fiyatinda gerceklemeden P&L'i."""
    if position is None:
        return 0.0
    if position.direction == Direction.LONG:
        return (close - position.entry) * position.remaining_size
    return (position.entry - close) * position.remaining_size


def _limit_retest_hit(
    direction: Direction, entry: float, bar: pd.Series
) -> bool:
    """Limit emri bu barda dolar mi? LONG: low <= entry; SHORT: high >= entry."""
    if direction == Direction.LONG:
        return bar["low"] <= entry
    return bar["high"] >= entry


# ============================================================
# Ana dongu
# ============================================================


def run(
    ohlcv_by_tf: dict,
    config,
    initial_equity: float = 10_000.0,
    use_cache: bool = True,
    m15_lookback: Optional[int] = None,
) -> BacktestResult:
    """M15 bar-replay backtest.

    Args:
        ohlcv_by_tf: ``{TimeFrame|str: pd.DataFrame}`` — en az M15 zorunlu.
        config: ``SMCConfig``.
        initial_equity: baslangic equity.
        use_cache: HTF detektor cache'i (determinizmi etkilemez, hizlandirir).
            NOT (U-15): Cache ANAHTARI ``(tf, son bar timestamp'i)``. M15 her bar
            yeni timestamp'le geldiginde key farklidir -> M15 katmaninda cache
            FIILEN HIT ETMEZ; yalnizca HTF (D1/H8/H4/H1) snapshot'larini
            hizlandirir. Ad geriye uyumluluk icin ``use_cache`` olarak kaliyor.
        m15_lookback: opsiyonel — ``orchestrator.analyze``'a gecirilen M15
            diliminin maksimum bar sayisi. ``None`` (varsayilan) ise tum M15
            gecmisi gecirilir (Faz 5 davranisi — geriye uyumlu). Bir tam sayi
            verilirse her M15 barinda yalnizca son ``m15_lookback`` bar
            orchestrator'a gecer: per-cagri maliyeti O(m15_lookback) ile
            sinirlanir (harness perf siniri — README'ye bak). Look-ahead
            GUVENLIGI korunur: pencere yalnizca GECMISI keser, ``at_bar``'dan
            sonrasini asla icermez. Determinizm: sabit pencere boyu -> ayni
            input -> ayni output.

    Returns:
        ``BacktestResult`` — trades + equity_curve (M15 index) + metrics.
    """
    data = _normalize(ohlcv_by_tf)
    if TimeFrame.M15 not in data:
        raise ValueError("harness.run: M15 OHLCV zorunlu")
    if m15_lookback is not None and m15_lookback < 1:
        raise ValueError("harness.run: m15_lookback >= 1 olmali")
    m15 = data[TimeFrame.M15]
    n = len(m15)
    fill_model = getattr(config, "fill_model", "next_open")
    limit_bars = getattr(config, "limit_retest_bars", 5)

    cache: Optional[dict] = {} if use_cache else None

    # --- state ---
    equity = float(initial_equity)
    position: Optional[Position] = None
    pending: Optional[_Pending] = None
    trades: list[Trade] = []
    recent_results: list[float] = []  # R-multiple gecmisi
    consecutive_losses = 0
    peak_equity = equity
    max_dd_pct = 0.0

    equity_index: list = []
    equity_values: list[float] = []

    for i in range(n):
        bar = m15.iloc[i]
        bar_ts = m15.index[i]
        close = float(bar["close"])

        # ----------------------------------------------------------
        # 1. Acik pozisyonu BU barin OHLC'siyle guncelle — ONCE (§8 adim 1)
        # ----------------------------------------------------------
        if position is not None:
            position, closed = pm_update(position, bar, config)
            for tr in closed:
                trades.append(tr)
                equity += tr.pnl
                recent_results.append(tr.r_multiple)
                if tr.r_multiple < 0:
                    consecutive_losses += 1
                elif tr.r_multiple > 0:
                    consecutive_losses = 0

        # ----------------------------------------------------------
        # 2. Pending fill cozumu (t'de uretilmis setup -> t veya sonrasi)
        #    next_open: created_idx + 1 barinda open'da fill.
        #    limit_retest: created_idx+1 .. +limit_bars araliginda retest.
        # ----------------------------------------------------------
        if position is None and pending is not None:
            filled = False
            if fill_model == "next_open":
                # t+1 acilisinda fill (created_idx setup'in uretildigi bar).
                if i == pending.created_idx + 1:
                    position = open_position(
                        pending.validated,
                        fill_price=float(bar["open"]),
                        fill_ts=bar_ts,
                        config=config,
                    )
                    filled = True
                    pending = None
            else:  # limit_retest
                age = i - pending.created_idx
                if 1 <= age <= limit_bars:
                    entry = pending.validated.setup.entry
                    direction = pending.validated.setup.direction
                    if _limit_retest_hit(direction, entry, bar):
                        position = open_position(
                            pending.validated,
                            fill_price=entry,
                            fill_ts=bar_ts,
                            config=config,
                        )
                        filled = True
                        pending = None
                if pending is not None and age > limit_bars:
                    pending = None  # expire — retest olmadi

            # Ö-4: Ayni barda fill olduysa, fill SONRASI intrabar SL/TP'yi
            # KONTROL ET (position_manager.update SL-first uygular —
            # konservatif). Eski davranis "fill bardan sonra basla" dedi
            # ve yon-bagimli sapma yaratti: SL erteleme = iyimserlik. Yeni:
            # fill barında da pm_update -> SL barin low/high'inda iken
            # tetiklenir, gercek brokerage davranisina yakin.
            if filled and position is not None:
                position, closed_on_fill = pm_update(position, bar, config)
                for tr in closed_on_fill:
                    trades.append(tr)
                    equity += tr.pnl
                    recent_results.append(tr.r_multiple)
                    if tr.r_multiple < 0:
                        consecutive_losses += 1
                    elif tr.r_multiple > 0:
                        consecutive_losses = 0

        # ----------------------------------------------------------
        # 3. Pozisyon + pending yoksa: yeni setup ara (§8 adim 2-3)
        # ----------------------------------------------------------
        if position is None and pending is None:
            # M15 lookback penceresi: yalnizca GECMISI keser (look-ahead
            # guvenli). m15_lookback None ise tam veri (Faz 5 davranisi).
            if m15_lookback is None:
                analyze_data = data
            else:
                lo = max(0, i - m15_lookback + 1)
                analyze_data = dict(data)
                analyze_data[TimeFrame.M15] = m15.iloc[lo:i + 1]
            picture = analyze(analyze_data, config,
                              at_bar=bar_ts.to_pydatetime()
                              if hasattr(bar_ts, "to_pydatetime") else bar_ts,
                              cache=cache)
            setup = build_setup(picture, config)
            if setup is not None:
                account_state = AccountState(
                    equity=equity,
                    open_position=False,
                    recent_results=list(recent_results),
                    consecutive_losses=consecutive_losses,
                    max_drawdown_pct=max_dd_pct,
                )
                verdict = risk_validate(setup, account_state, config)
                if isinstance(verdict, ValidatedSetup):
                    pending = _Pending(validated=verdict, created_idx=i)

        # ----------------------------------------------------------
        # 4. Equity mark-to-market + drawdown (§8 adim 4)
        # ----------------------------------------------------------
        mtm = equity + _mark_to_market(position, close)
        equity_index.append(bar_ts)
        equity_values.append(mtm)
        if mtm > peak_equity:
            peak_equity = mtm
        if peak_equity > 0:
            dd = (peak_equity - mtm) / peak_equity
            if dd > max_dd_pct:
                max_dd_pct = dd

    equity_curve = pd.Series(equity_values, index=pd.DatetimeIndex(equity_index))

    result_metrics = _metrics.compute(trades, equity_curve, config)

    return BacktestResult(
        trades=trades,
        equity_curve=equity_curve,
        metrics=result_metrics,
    )
