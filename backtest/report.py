"""Faz 5.5 — Backtest raporu.

``BacktestResult`` -> insan-okunur chat ozeti + ``trades.csv`` + ratchet-uyumlu
tek-satir metrik (grep'lenebilir) + walk-forward tablo HOOK'u (Faz 6 doldurur).

İmzalar:
    summary(result, config=None) -> str          # chat ozeti
    write_trades_csv(result, path) -> str         # trades.csv yazar, path doner
    ratchet_metric_line(result) -> str            # tek satir, "RATCHET_METRIC ..."
    walk_forward_table(windows=None) -> str       # HOOK — Faz 6
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Optional

from smc_engine.types import BacktestResult


def _fmt(v, nd: int = 4) -> str:
    try:
        if v != v:  # NaN
            return "nan"
        fv = float(v)
        # math.isinf yakalanir: "inf" string'i yerine sentinel; profit_factor
        # icin Ö-9 zaten 999.0 cap koyuyor ama defansif kalsin.
        if math.isinf(fv):
            return "inf" if fv > 0 else "-inf"
        return f"{fv:.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


def summary(result: BacktestResult, config=None, windows=None) -> str:
    """Insan-okunur chat ozeti — cok satirli string.

    ``windows``: opsiyonel ``walk_forward()`` ciktisi. Verilirse walk-forward
    tablosu doldurulmus haliyle gomulur; verilmezse HOOK placeholder'i (Faz 5
    davranisi — geriye uyumlu).
    """
    m = result.metrics
    lines: list[str] = []
    lines.append("=== SMC Engine Backtest Raporu ===")
    lines.append(f"  Trade sayisi      : {m.get('trade_count', 0)}")
    if m.get("low_trade_count_warning"):
        lines.append("  [UYARI] <30 trade — Sharpe/metrikler yaniltici olabilir.")
    lines.append(f"  Win rate          : {_fmt(m.get('win_rate', 0.0), 3)}")
    pf = m.get('profit_factor', 0.0)
    # Ö-9: gross_loss==0 -> profit_factor 999.0 tavan; "tanimsiz" yorumla.
    if isinstance(pf, (int, float)) and pf >= 999.0:
        lines.append("  Profit factor     : tanimsiz (zarar yok)")
    else:
        lines.append(f"  Profit factor     : {_fmt(pf, 3)}")
    lines.append(f"  Expectancy (R)    : {_fmt(m.get('expectancy', 0.0), 4)}")
    lines.append(f"  Sharpe (yillik)   : {_fmt(m.get('sharpe', 0.0), 3)}")
    lines.append(f"  Sortino (yillik)  : {_fmt(m.get('sortino', 0.0), 3)}")
    lines.append(
        f"  Max drawdown      : {_fmt(m.get('max_drawdown_pct', 0.0) * 100, 2)}% "
        f"(sure: {m.get('max_drawdown_duration', 0)} bar)"
    )
    lines.append(f"  Toplam P&L        : {_fmt(m.get('total_pnl', 0.0), 2)}")
    rdist = m.get("r_multiple_distribution", {})
    if rdist:
        lines.append(
            f"  R-multiple        : mean={_fmt(rdist.get('mean', 0.0), 3)} "
            f"min={_fmt(rdist.get('min', 0.0), 2)} "
            f"max={_fmt(rdist.get('max', 0.0), 2)} "
            f"std={_fmt(rdist.get('std', 0.0), 3)}"
        )
    lines.append(f"  Ort. tutma (saat) : {_fmt(m.get('avg_holding_hours', 0.0), 2)}")
    buckets = m.get("confluence_buckets", {})
    if buckets:
        lines.append("  Confluence kovalari:")
        for label in sorted(buckets):
            b = buckets[label]
            lines.append(
                f"    {label}: count={b['count']} "
                f"avg_r={_fmt(b['avg_r'], 3)} "
                f"win_rate={_fmt(b['win_rate'], 3)}"
            )
    lines.append("")
    lines.append(ratchet_metric_line(result))
    lines.append("")
    lines.append(walk_forward_table(windows))
    return "\n".join(lines)


def write_trades_csv(result: BacktestResult, path: str | Path) -> str:
    """Trade'leri CSV'ye yazar; yazilan dosya yolunu doner."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "direction", "entry", "entry_ts", "exit_price", "exit_ts",
        "exit_reason", "pnl", "r_multiple", "size", "confluence_score",
        "confluence_factor_count",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for t in result.trades:
            w.writerow([
                t.direction.value, t.entry, t.entry_ts.isoformat(),
                t.exit_price, t.exit_ts.isoformat(), t.exit_reason,
                t.pnl, t.r_multiple, t.size, t.confluence_score,
                t.confluence_factor_count,
            ])
    return str(path)


def ratchet_metric_line(result: BacktestResult) -> str:
    """Ratchet-uyumlu tek-satir metrik — grep'lenebilir.

    Format: ``RATCHET_METRIC key=val key=val ...``
    Ratchet loop bu satiri parse eder (primary metric: sharpe + min_trades gate).
    """
    m = result.metrics
    parts = [
        f"sharpe={_fmt(m.get('sharpe', 0.0), 4)}",
        f"sortino={_fmt(m.get('sortino', 0.0), 4)}",
        f"expectancy={_fmt(m.get('expectancy', 0.0), 4)}",
        f"profit_factor={_fmt(m.get('profit_factor', 0.0), 4)}",
        f"win_rate={_fmt(m.get('win_rate', 0.0), 4)}",
        f"max_dd={_fmt(m.get('max_drawdown_pct', 0.0), 4)}",
        f"trades={m.get('trade_count', 0)}",
        f"low_trade_warning={int(bool(m.get('low_trade_count_warning', True)))}",
    ]
    return "RATCHET_METRIC " + " ".join(parts)


def _short_ts(ts) -> str:
    """Timestamp -> kisa 'YYYY-MM-DD' string (tablo hizalama icin)."""
    try:
        return str(ts)[:10]
    except (TypeError, ValueError):
        return str(ts)


def walk_forward_table(windows: Optional[list] = None) -> str:
    """Walk-forward sonuc tablosu — Faz 6.3 (anti-overfit, Spec §8.1).

    ``windows``: ``backtest.walk_forward.walk_forward()`` ciktisi — her ogesi
    ``train_start/train_end/test_start/test_end`` (Timestamp) +
    ``train_metrics/test_metrics`` (dict) iceren sozluk. None/bos ise HOOK
    placeholder'i doner (geriye uyumlu — Faz 5 davranisi).

    Tablo her pencere icin train ve test Sharpe + expectancy + trade sayisini
    yan yana gosterir; alttaki ozet satiri train/test Sharpe ortalamalarini
    ve test toplam trade sayisini verir (overfit sinyali: train >> test).
    """
    if not windows:
        return (
            "=== Walk-Forward Tablosu ===\n"
            "  [HOOK] walk_forward() sonucu verilmedi (Faz 5 davranisi)."
        )
    lines = ["=== Walk-Forward Tablosu ===",
             "  pencere | train araligi          | test araligi           "
             "| tr_sharpe | te_sharpe | tr_exp  | te_exp  | tr_tr | te_tr"]
    tr_sharpes: list[float] = []
    te_sharpes: list[float] = []
    te_trades_total = 0
    for w in windows:
        tm = w.get("train_metrics", {})
        sm = w.get("test_metrics", {})
        idx = w.get("index", 0)
        tr_s = float(tm.get("sharpe", 0.0))
        te_s = float(sm.get("sharpe", 0.0))
        tr_sharpes.append(tr_s)
        te_sharpes.append(te_s)
        te_trades_total += int(sm.get("trade_count", 0))
        lines.append(
            f"  {idx + 1:>7} | "
            f"{_short_ts(w.get('train_start')):>10}..{_short_ts(w.get('train_end')):<10} | "
            f"{_short_ts(w.get('test_start')):>10}..{_short_ts(w.get('test_end')):<10} | "
            f"{_fmt(tr_s, 3):>9} | {_fmt(te_s, 3):>9} | "
            f"{_fmt(tm.get('expectancy', 0.0), 3):>7} | "
            f"{_fmt(sm.get('expectancy', 0.0), 3):>7} | "
            f"{tm.get('trade_count', 0):>5} | {sm.get('trade_count', 0):>5}"
        )
    n = len(windows)
    avg_tr = sum(tr_sharpes) / n if n else 0.0
    avg_te = sum(te_sharpes) / n if n else 0.0
    lines.append(
        f"  --- ozet: pencere={n} | ort tr_sharpe={_fmt(avg_tr, 3)} "
        f"| ort te_sharpe={_fmt(avg_te, 3)} | toplam test trade={te_trades_total}"
    )
    if te_trades_total < 30:
        lines.append(
            "  [UYARI] toplam test trade < 30 — walk-forward Sharpe yaniltici "
            "olabilir (Spec §8.1 min trade count)."
        )
    return "\n".join(lines)
