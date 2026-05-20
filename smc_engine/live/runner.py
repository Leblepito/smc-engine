"""``LiveRunner`` — APScheduler tick → adapter.fetch → orchestrator → setup_builder → risk_guard → logger.

Spec §3 akışı. HTF cache runner ömrü boyu RAM'de tutulur. Look-ahead
garantisi: ``at_bar = last_closed_M15`` (forming bar asla görülmez).

Hata yönetimi (Spec §10):
- Adapter exception → log error + skip; sonraki M15 tick'inde tekrar dene.
- Setup yok / risk_guard reject → emit edilir (rejection da logger'ın işi).
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Protocol

from smc_engine.config import SMCConfig
from smc_engine.integrations._base import ExchangeAdapter
from smc_engine.live.account_state import build_static_account_state
from smc_engine.orchestrator import analyze as orchestrator_analyze
from smc_engine.risk_guard import validate as risk_guard_validate
from smc_engine.setup_builder import build_with_diagnostics
from smc_engine.types import Rejection, TimeFrame, ValidatedSetup

logger = logging.getLogger(__name__)


# Hangi TF'leri fetch edip orchestrator'a geçireceğiz.
_TFS_TO_FETCH: tuple[TimeFrame, ...] = (TimeFrame.D1, TimeFrame.H4, TimeFrame.M15)


class _SignalLoggerProtocol(Protocol):
    def emit(self, payload) -> None: ...


def _format_diagnostics(diagnostics: dict) -> str:
    """BuildResult.diagnostics dict'ini log-friendly key=value string'e çevir.

    Float değerler 4 ondalıkta; tick log'unun key=value şemasıyla uyumlu
    (analyze_signals.py generic ``key=value`` regex ile parse edebilir).

    Format safety (Important code review 2026-05-20): non-float değerlerde
    boşluk → '_'. Bugün diagnostics sadece sayı + tek-token htf_bias string'i
    içeriyor; ama beklenmedik bir değer (test mock repr'ı gibi) boşluk
    taşırsa tek-satır key=value şeması bozulmasın — defense-in-depth.
    """
    if not diagnostics:
        return ""
    parts = []
    for k, v in diagnostics.items():
        if isinstance(v, float):
            parts.append(f"{k}={v:.4f}")
        else:
            sval = str(v).replace(" ", "_")
            parts.append(f"{k}={sval}")
    return " ".join(parts)


class LiveRunner:
    """Tek-thread live pipeline. Scheduler tetikler → run_tick()."""

    def __init__(
        self,
        adapter: ExchangeAdapter,
        config: SMCConfig,
        signal_logger: _SignalLoggerProtocol,
        order_manager=None,  # Sub-proje #5A: opsiyonel; None ise log-only
    ) -> None:
        self.adapter = adapter
        self.config = config
        self.signal_logger = signal_logger
        # Sub-proje #5A execution hook. None → eski sub-proje #2 davranıÅı.
        # config.execution_enabled=True ise CLI/init bu parametreyi geçirir.
        self.order_manager = order_manager
        # HTF cache runner ömrü boyu paylaşılır (Spec §7.1 + §13 trade-off #3).
        # Per-symbol: orchestrator cache key = (tf, ts); sembolden bağımsız
        # olduğu için tek dict tüm sembollere paylaşılırsa BTC'nin D1 zone'u
        # ETH analizine sızar. Her sembol kendi cache'ini taşır.
        self._htf_caches: dict[str, dict] = {}

    def _cache_for(self, symbol: str) -> dict:
        if symbol not in self._htf_caches:
            self._htf_caches[symbol] = {}
        return self._htf_caches[symbol]

    # ---------------- look-ahead garantisi ----------------

    @staticmethod
    def _last_closed_m15(now: datetime) -> datetime:
        """En son kapanmış M15 bar'ın open_time'ı.

        Bir M15 bar [t, t+15) intervalinde açık; t+15'te kapanır. Eğer ``now``
        tam t+15 ise o bar henüz "kapanış anı" — bir önceki M15'i döndür
        (forming bar'ı dahil etmemek için katı sınır).
        """
        # Önce dakikayı 15'e yuvarla (truncate)
        truncated = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
        # Şu anki M15 bar'ın open_time'ı; ondan önceki bar son kapanan.
        return truncated - timedelta(minutes=15)

    # ---------------- per-symbol pipeline ----------------

    def run_once(self, symbol: str, now: Optional[datetime] = None) -> None:
        """Tek sembol için tek pipeline turunu çalıştır."""
        if now is None:
            # Naive UTC datetime — orchestrator at_bar timestamp'leri tz-naive.
            now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
        at_bar = self._last_closed_m15(now)
        try:
            ohlcv_by_tf = {
                tf: self.adapter.fetch_ohlcv(symbol, tf, self.config.lookback_bars(tf))
                for tf in _TFS_TO_FETCH
            }
        except Exception as exc:
            logger.error("adapter fetch failed for %s: %s\n%s", symbol, exc, traceback.format_exc())
            return

        try:
            picture = orchestrator_analyze(
                ohlcv_by_tf, self.config, at_bar=at_bar, cache=self._cache_for(symbol)
            )
        except Exception as exc:
            logger.error("orchestrator failed for %s: %s\n%s", symbol, exc, traceback.format_exc())
            return

        build_result = build_with_diagnostics(picture, self.config)
        setup = build_result.setup
        if setup is None:
            # Refactor (2026-05-20): _diagnose_no_setup heuristic'i kalktı.
            # build_with_diagnostics() artık SPESİFİK NoSetupReason + ölçülen
            # diagnostics döner (sl_atr_ratio, confluence_score vb.).
            reason = build_result.no_setup_reason
            reason_str = reason.value if reason is not None else "unknown"
            diag_str = _format_diagnostics(build_result.diagnostics)
            # Tek-şema tick log (I2): no_setup/validated_setup/rejection aynı
            # format. gate="none" sentinel (I1) — boş value ambiguity yok.
            logger.info(
                "tick symbol=%s at_bar=%s kind=no_setup gate=none reason=%s %s",
                symbol, at_bar.isoformat(), reason_str, diag_str,
            )
            return  # rejection değil; üretilemedi

        try:
            account_state = build_static_account_state(self.config)
            result = risk_guard_validate(setup, account_state, self.config)
        except Exception as exc:
            logger.error("risk_guard failed for %s: %s\n%s", symbol, exc, traceback.format_exc())
            return

        # Per-symbol tick summary (İş 2): validated mı rejection mı, hangi gate.
        # gate=none sentinel validated path için (I1 code review).
        kind = "validated_setup" if isinstance(result, ValidatedSetup) else "rejection"
        gate = (
            getattr(result, "gate", "none") if isinstance(result, Rejection)
            else "none"
        )
        logger.info(
            "tick symbol=%s at_bar=%s kind=%s gate=%s",
            symbol, at_bar.isoformat(), kind, gate,
        )

        try:
            self.signal_logger.emit(result)
        except Exception as exc:
            logger.error("signal_logger.emit failed for %s: %s\n%s", symbol, exc, traceback.format_exc())

        # Sub-proje #5A execution hook. order_manager varsa ve sonuç
        # ValidatedSetup ise (rejection deÄil) emire çevir. Rejection'lar
        # zaten signal_logger'da; execution'a girmez.
        if self.order_manager is not None and isinstance(result, ValidatedSetup):
            try:
                self.order_manager.process_setup(result, symbol=symbol, at_bar=at_bar)
            except Exception as exc:
                logger.error(
                    "order_manager.process_setup failed for %s: %s\n%s",
                    symbol, exc, traceback.format_exc(),
                )

    # ---------------- multi-symbol tick ----------------

    def run_tick(self, symbols: Iterable[str], now: Optional[datetime] = None) -> None:
        """Tek scheduler tick'i — her sembol için pipeline çalıştır."""
        for sym in symbols:
            self.run_once(sym, now=now)
