"""Faz 5.4 — Backtest metrikleri.

Bilinen trade listesi + equity curve'den performans metrikleri:
  - Sharpe (yillik), Sortino (yillik)
  - win rate, profit factor
  - max drawdown (% + sure)
  - R-multiple dagilimi
  - expectancy = (win% x ort.kazanc R) - (loss% x ort.kayip R)
  - trade sayisi, ort. tutma suresi
  - confluence-score kovasi basina performans

<30 trade -> ``low_trade_count_warning`` bayragi (Sharpe yaniltici olabilir).

İmza:
    compute(trades, equity_curve, config) -> dict
"""

from __future__ import annotations

import math
from typing import Optional

import pandas as pd

from smc_engine.types import Trade

# Yillik faktor — M15 barlari icin: 1 yil ~= 35040 M15 bari.
# (365 gun x 24 saat x 4 bar/saat). equity curve bar-bazli -> bu faktorle
# yillik Sharpe/Sortino. U-7: ``_bars_per_year(index)`` TF'ye duyarli
# tahmin (``pd.infer_freq``); cikarim basarisizsa bu sabite fallback.
_BARS_PER_YEAR_M15 = 365 * 24 * 4


def _bars_per_year(equity_curve: "pd.Series") -> int:
    """Equity curve index'inden yillik bar sayisi tahmini.

    U-7: ``pd.infer_freq`` ile bar suresini cikar; bilinmiyorsa M15 sabit.
    Boylece H1/H4/D1 backtest'lerde Sharpe annualization dogru olcekte olur.
    """
    try:
        if equity_curve is None or len(equity_curve) < 3:
            return _BARS_PER_YEAR_M15
        freq = pd.infer_freq(equity_curve.index)
        if freq is None:
            return _BARS_PER_YEAR_M15
        offset = pd.tseries.frequencies.to_offset(freq)
        if offset is None:
            return _BARS_PER_YEAR_M15
        seconds_per_bar = offset.nanos / 1e9
        if seconds_per_bar <= 0:
            return _BARS_PER_YEAR_M15
        seconds_per_year = 365 * 24 * 3600
        return max(1, int(seconds_per_year / seconds_per_bar))
    except (ValueError, AttributeError, TypeError):
        return _BARS_PER_YEAR_M15
_MIN_TRADES = 30

# Ö-9: profit_factor inf yerine tavan deger (rapor/parse'ta inf string sorunu).
# gross_loss == 0 oldugunda kullanilir. Ratchet veya rapor "tanimsiz (zarar yok)"
# olarak yorumlayabilir; karsilastirma icin sayisal tavan.
_PROFIT_FACTOR_CAP = 999.0

# Confluence skor kovalari — [0.4,0.55), [0.55,0.7), [0.7,0.85), [0.85,1.0+]
_BUCKET_EDGES = [0.0, 0.55, 0.70, 0.85, 1.01]


def _bucket_label(score: float) -> str:
    for i in range(len(_BUCKET_EDGES) - 1):
        lo, hi = _BUCKET_EDGES[i], _BUCKET_EDGES[i + 1]
        if lo <= score < hi:
            return f"[{lo:.2f},{hi:.2f})"
    return f">={_BUCKET_EDGES[-1]:.2f}"


def _bar_returns(equity_curve: pd.Series) -> pd.Series:
    """Equity curve'den bar-bazli yuzde getiri serisi."""
    if equity_curve is None or len(equity_curve) < 2:
        return pd.Series(dtype=float)
    return equity_curve.pct_change().dropna()


def _sharpe(returns: pd.Series, bars_per_year: int = _BARS_PER_YEAR_M15) -> float:
    """Yillik Sharpe (risk-free = 0). ``bars_per_year`` index TF'sine gore U-7."""
    if len(returns) < 2:
        return 0.0
    std = returns.std(ddof=1)
    if std == 0 or math.isnan(std):
        return 0.0
    mean = returns.mean()
    return float(mean / std * math.sqrt(bars_per_year))


def _sortino(returns: pd.Series, bars_per_year: int = _BARS_PER_YEAR_M15) -> float:
    """Yillik Sortino — yalnizca asagi yon (downside) volatilitesi (U-7)."""
    if len(returns) < 2:
        return 0.0
    downside = returns[returns < 0]
    if len(downside) < 1:
        return 0.0
    dstd = math.sqrt((downside ** 2).mean())
    if dstd == 0 or math.isnan(dstd):
        return 0.0
    mean = returns.mean()
    return float(mean / dstd * math.sqrt(bars_per_year))


def _max_drawdown(equity_curve: pd.Series) -> tuple[float, int, bool]:
    """Max drawdown (% olarak, pozitif) + sure (peak->recovery bar sayisi) +
    ruined bayragi.

    Ö-10: Sure tanimi = **peak'ten recovery'ye** (peak'e geri donen bar).
    Recover etmediyse peak'ten son bara kadar (acik DD penceresi).
    Onceki tanim peak->trough yarim ölçüydü; ratchet/rapor için peak->recovery
    daha bilgilendirici.

    ``equity_ruined`` (donus 3.elemani): equity zincirinde herhangi bir bar
    <= 0 oldugunda True. Bu durumda dogal DD% tanimsiz (negatif peak'e bolme);
    metrics.compute() ayri bayrak olarak isaretler ve max_drawdown_pct = 1.0
    (tam yikim) yazar.
    """
    if equity_curve is None or len(equity_curve) < 2:
        return 0.0, 0, False
    ruined = bool((equity_curve <= 0).any())
    if ruined:
        # equity<=0: DD% klasik formul yaniltici. Yikim acik bir sinyal:
        # max_dd 1.0 (tam), duration = ilk yikima kadar bar sayisi.
        peak_pos = int(equity_curve.cummax().idxmax() != equity_curve.index[0]
                       and 0 or 0)
        # peak: ilk maksimum noktasi.
        cm = equity_curve.cummax()
        peak_val = float(cm.max())
        # peak'in ilk gerceklestigi konum.
        peak_pos = int((equity_curve >= peak_val).to_numpy().argmax())
        # ruin bari: equity ilk kez <=0.
        ruin_pos = int((equity_curve <= 0).to_numpy().argmax())
        duration = max(0, ruin_pos - peak_pos)
        return 1.0, int(duration), True
    running_max = equity_curve.cummax()
    drawdown = (running_max - equity_curve) / running_max.replace(0, float("nan"))
    drawdown = drawdown.fillna(0.0)
    max_dd = float(drawdown.max())
    if max_dd <= 0:
        return 0.0, 0, False
    # En derin DD noktasi.
    trough_pos = int(drawdown.values.argmax())
    # Peak: trough'tan once running_max'a esit oldugu son nokta.
    peak_val = running_max.iloc[trough_pos]
    peak_pos = trough_pos
    for j in range(trough_pos, -1, -1):
        if equity_curve.iloc[j] >= peak_val:
            peak_pos = j
            break
    # Recovery: trough'tan SONRA equity tekrar peak_val'e ulasan ilk bar.
    recovery_pos = None
    for j in range(trough_pos + 1, len(equity_curve)):
        if equity_curve.iloc[j] >= peak_val:
            recovery_pos = j
            break
    # Recover olmadiysa: pencere sonuna kadar acik DD.
    end_pos = recovery_pos if recovery_pos is not None else len(equity_curve) - 1
    duration = end_pos - peak_pos
    return max_dd, int(duration), False


def compute(
    trades: list[Trade],
    equity_curve: pd.Series,
    config,
) -> dict:
    """Trade listesi + equity curve -> metrik sozlugu.

    Bos trade listesinde de crash etmez; <30 trade'de uyari bayragi koyar.
    """
    n = len(trades)
    metrics: dict = {}
    metrics["trade_count"] = n
    metrics["low_trade_count_warning"] = n < _MIN_TRADES

    returns = _bar_returns(equity_curve)
    bpy = _bars_per_year(equity_curve)
    metrics["sharpe"] = _sharpe(returns, bars_per_year=bpy)
    metrics["sortino"] = _sortino(returns, bars_per_year=bpy)

    max_dd, dd_dur, ruined = _max_drawdown(equity_curve)
    metrics["max_drawdown_pct"] = max_dd
    metrics["max_drawdown_duration"] = dd_dur
    # Ö-10: equity<=0 oldugunda ayri bayrak — DD% tek basina yaniltici.
    metrics["equity_ruined"] = ruined

    if n == 0:
        metrics["win_rate"] = 0.0
        metrics["profit_factor"] = 0.0
        metrics["expectancy"] = 0.0
        metrics["avg_holding_hours"] = 0.0
        metrics["r_multiple_distribution"] = {
            "mean": 0.0, "min": 0.0, "max": 0.0, "std": 0.0,
        }
        metrics["confluence_buckets"] = {}
        metrics["total_pnl"] = 0.0
        return metrics

    r_mults = [t.r_multiple for t in trades]
    pnls = [t.pnl for t in trades]
    wins = [t for t in trades if t.r_multiple > 0]
    losses = [t for t in trades if t.r_multiple < 0]

    metrics["win_rate"] = len(wins) / n
    metrics["total_pnl"] = float(sum(pnls))

    # Profit factor = brut kar / brut zarar.
    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
    if gross_loss > 0:
        metrics["profit_factor"] = float(gross_profit / gross_loss)
    elif gross_profit > 0:
        # Ö-9: gross_loss==0 -> matematik olarak tanimsiz; ratchet/rapor parse
        # icin sonsuz yerine sayisal tavan (_PROFIT_FACTOR_CAP). Rapor bunu
        # "tanimsiz (zarar yok)" olarak yorumlar.
        metrics["profit_factor"] = float(_PROFIT_FACTOR_CAP)
    else:
        metrics["profit_factor"] = 0.0

    # Expectancy = (win% x ort.kazanc R) - (loss% x ort.kayip R-mutlak).
    win_pct = len(wins) / n
    loss_pct = len(losses) / n
    avg_win_r = (sum(t.r_multiple for t in wins) / len(wins)) if wins else 0.0
    avg_loss_r = (
        abs(sum(t.r_multiple for t in losses) / len(losses)) if losses else 0.0
    )
    metrics["expectancy"] = win_pct * avg_win_r - loss_pct * avg_loss_r

    # R-multiple dagilimi.
    mean_r = sum(r_mults) / n
    if n > 1:
        var = sum((r - mean_r) ** 2 for r in r_mults) / (n - 1)
        std_r = math.sqrt(var)
    else:
        std_r = 0.0
    metrics["r_multiple_distribution"] = {
        "mean": float(mean_r),
        "min": float(min(r_mults)),
        "max": float(max(r_mults)),
        "std": float(std_r),
    }

    # Ort. tutma suresi (saat).
    holding_hours = []
    for t in trades:
        delta = pd.Timestamp(t.exit_ts) - pd.Timestamp(t.entry_ts)
        holding_hours.append(delta.total_seconds() / 3600.0)
    metrics["avg_holding_hours"] = float(sum(holding_hours) / n)

    # Confluence-score kovasi basina performans.
    buckets: dict[str, dict] = {}
    for t in trades:
        label = _bucket_label(t.confluence_score)
        b = buckets.setdefault(
            label, {"count": 0, "_r_sum": 0.0, "_pnl_sum": 0.0, "_wins": 0}
        )
        b["count"] += 1
        b["_r_sum"] += t.r_multiple
        b["_pnl_sum"] += t.pnl
        if t.r_multiple > 0:
            b["_wins"] += 1
    for label, b in buckets.items():
        c = b["count"]
        b["avg_r"] = b.pop("_r_sum") / c
        b["total_pnl"] = b.pop("_pnl_sum")
        b["win_rate"] = b.pop("_wins") / c
    metrics["confluence_buckets"] = buckets

    return metrics


# ============================================================
# Faz 6.4 — Bootstrap Sharpe guven araligi (anti-overfit)
# ============================================================

# Sabit varsayilan seed — ayni trade listesi -> ayni CI (determinizm, Karar B).
_BOOTSTRAP_SEED = 20260515


def _sharpe_from_r_multiples(r_mults) -> float:
    """Bir R-multiple ornegi icin (yillik-olmayan) Sharpe benzeri oran.

    Trade-bazli Sharpe = ortalama R / R standart sapmasi. Bootstrap CI bu
    trade-bazli oran uzerinden hesaplanir (equity-curve bar getirileri degil) —
    cunku resample edilen birim trade'dir.
    """
    n = len(r_mults)
    if n < 2:
        return 0.0
    mean = sum(r_mults) / n
    var = sum((r - mean) ** 2 for r in r_mults) / (n - 1)
    std = math.sqrt(var)
    if std == 0.0 or math.isnan(std):
        return 0.0
    return float(mean / std)


def bootstrap_trade_sharpe_ci(
    trades: list,
    n_samples: int = 1000,
    ci: float = 0.95,
    seed: Optional[int] = None,
) -> tuple[float, float]:
    """Trade R-multiple'larindan bootstrap ile **trade-bazli** Sharpe %CI.

    UYARI — KR-3 (rapor 2026-05-15): Bu fonksiyon **trade-bazli Sharpe**
    (mean(R) / std(R)) dagiliminin CI'sini hesaplar; ``metrics.compute()``
    raporladigi Sharpe ise **equity-curve bar-getirilerinden yillıklandirilmis**
    Sharpe'tir. İkisi farkli kavramdir (ölcek ve yön karsilastirilabilir
    ama birebir ayni degil); ratchet gate'i hangi Sharpe'i kullanacagina
    kullanici karar verir (TBD — Spec §12 gate karari).

    Geri uyumluluk: eski ad ``bootstrap_sharpe_ci`` bu fonksiyona alias'tir
    (asagida) — yeni kod ``bootstrap_trade_sharpe_ci`` kullanmali.

    Yontem (Spec §8.1 — Confidence interval):
      1. ``trades`` listesinden R-multiple'lari al.
      2. ``n_samples`` kez: ayni uzunlukta REPLACEMENT ile yeniden ornekle,
         her ornekte trade-bazli Sharpe (mean R / std R) hesapla.
      3. Bootstrap dagiliminin (1-ci)/2 ve 1-(1-ci)/2 persentilleri -> (alt, ust).

    Determinizm (Karar B): ``numpy.random.default_rng(seed)`` — ayni trade
    listesi + ayni seed -> ozdes CI. ``seed=None`` ise sabit ``_BOOTSTRAP_SEED``.

    Args:
        trades: ``Trade`` listesi (``r_multiple`` alani okunur).
        n_samples: bootstrap resample sayisi.
        ci: guven seviyesi (0.95 -> %2.5 / %97.5 persentil).
        seed: RNG seed; None -> ``_BOOTSTRAP_SEED``.

    Returns:
        ``(alt_sinir, ust_sinir)`` float ikilisi. <2 trade -> ``(0.0, 0.0)``.

    Ratchet gate notu: alt_sinir <= 0 ise trade-bazli Sharpe istatistiksel
    olarak sifirdan ayirt edilemez — ratchet metrigi reddetmeli (Spec §12).
    """
    import numpy as np

    r_mults = [float(t.r_multiple) for t in trades]
    n = len(r_mults)
    if n < 2:
        return (0.0, 0.0)

    rng = np.random.default_rng(_BOOTSTRAP_SEED if seed is None else seed)
    arr = np.asarray(r_mults, dtype=float)

    sharpes = np.empty(n_samples, dtype=float)
    for i in range(n_samples):
        sample = rng.choice(arr, size=n, replace=True)
        sharpes[i] = _sharpe_from_r_multiples(sample.tolist())

    alpha = (1.0 - ci) / 2.0
    lo = float(np.percentile(sharpes, alpha * 100.0))
    hi = float(np.percentile(sharpes, (1.0 - alpha) * 100.0))
    return (lo, hi)


# ---- Geri uyumluluk alias'i (KR-3) ----------------------------------
# Eski ad ``bootstrap_sharpe_ci`` -> ``bootstrap_trade_sharpe_ci`` (yeniden
# adlandirildi cunku bu trade-bazli Sharpe'i hesapliyor, metrics.compute()'in
# raporladigi equity-curve yillik Sharpe'tan farkli bir metriktir).
bootstrap_sharpe_ci = bootstrap_trade_sharpe_ci
