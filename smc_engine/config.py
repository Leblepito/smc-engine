"""SMC Engine config — Spec §4 (TF_LOOKBACK), §7 (confluence ağırlıkları),
§13 (detektör parametre varsayılanları).

``SMCConfig`` tüm parametreleri tutar. ``load_config(path)`` bir YAML dosyasını
okuyup varsayılanların üzerine yazar (env > file precedence burada uygulanmaz —
bu Faz 0 kapsamı sadece dosya override).
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

import yaml

from smc_engine.types import TimeFrame

# ============================================================
# Lookback-per-TF — Spec §4 TF_LOOKBACK tablosu
# ============================================================

TF_LOOKBACK: dict[TimeFrame, int] = {
    TimeFrame.D1: 365,
    TimeFrame.H8: 550,
    TimeFrame.H4: 600,
    TimeFrame.H1: 500,
    TimeFrame.M15: 336,
}

# Her TF'nin bir mumunun süresi (dakika) — lookback → tarih aralığı çevrimi için.
TF_MINUTES: dict[TimeFrame, int] = {
    TimeFrame.M15: 15,
    TimeFrame.H1: 60,
    TimeFrame.H4: 240,
    TimeFrame.H8: 480,
    TimeFrame.D1: 1440,
}


def lookback_bars(tf: TimeFrame) -> int:
    """Verilen TF için hazırlık penceresi mum sayısı."""
    return TF_LOOKBACK[tf]


def lookback_minutes(tf: TimeFrame) -> int:
    """Lookback penceresinin dakika cinsinden uzunluğu (mum sayısı × mum süresi)."""
    return TF_LOOKBACK[tf] * TF_MINUTES[tf]


# ============================================================
# Confluence ağırlıkları — Spec §7 tablosu
# ============================================================


@dataclass
class ConfluenceWeights:
    poi_quality: float = 0.25
    premium_discount: float = 0.20
    liquidity_context: float = 0.20
    level_confluence: float = 0.15
    fvg_imbalance: float = 0.10
    clustering: float = 0.10

    def total(self) -> float:
        return (
            self.poi_quality
            + self.premium_discount
            + self.liquidity_context
            + self.level_confluence
            + self.fvg_imbalance
            + self.clustering
        )


# ============================================================
# SMCConfig — detektör parametre varsayılanları (Spec §13) + confluence
# ============================================================


@dataclass
class SMCConfig:
    # --- Detektör parametreleri (Spec §13) ---
    swing_lookback: int = 4
    ob_breakout_threshold: float = 1.5
    fvg_min_gap_atr: float = 0.3
    # U-2: imbalance siniflandirma esikleri (LIQ_VOID / INEFFICIENCY) ATR
    # carpani; eskiden imbalance_detector modul sabitiydi -> config'e tasindi.
    liq_void_gap_atr: float = 2.0
    inefficiency_gap_atr: float = 5.0
    deviation_tolerance_atr: float = 0.5
    equal_level_tolerance: float = 0.001
    max_zone_age_bars: int = 200

    # --- setup_builder / risk_guard eşikleri (Spec §13) ---
    confluence_min_score: float = 0.4
    min_rr: float = 1.5
    risk_pct: float = 0.01
    max_consecutive_losses: int = 5
    max_drawdown_pct: float = 0.10
    sl_min_atr_multiple: float = 0.5
    funding_buffer_minutes: int = 30
    # risk_guard confluence gate: en az kac sifir-olmayan confluence faktoru
    # gerekli (Setup.confluence_factor_count >= bu deger).
    min_confluence_factors: int = 2
    # Varlik sinifi — risk_guard seans/funding gate'ini secer.
    # "crypto" -> funding window gate; "forex" -> hafta sonu/seans gate.
    asset_class: str = "crypto"

    # --- Backtest maliyet / fill modelleri (Faz 5) ---
    spread: float = 0.0
    commission_pct: float = 0.0004
    slippage_pct: float = 0.0005
    fill_model: str = "next_open"  # "next_open" | "limit_retest"
    limit_retest_bars: int = 5

    # --- ATR hesabi (setup_builder + orchestrator) ---
    atr_period: int = 14

    # --- setup_builder tuning sabitleri (ratchet optimize edebilsin) ---
    tp_r_multiples: tuple[float, float, float] = field(
        default_factory=lambda: (1.5, 2.62, 4.23)
    )
    tp_weights: tuple[float, float, float] = field(
        default_factory=lambda: (0.5, 0.3, 0.2)
    )
    ote_low: float = 0.618
    ote_high: float = 0.786
    sl_band_buffer_mult: float = 0.25
    sl_abs_buffer_pct: float = 0.003
    cluster_tolerance_pct: float = 0.02

    # --- setup_builder kalite haritalari (yaml alt-haritasi ile override) ---
    poi_kind_quality: dict = field(
        default_factory=lambda: {
            "ZONE": 1.0,
            "LEVEL": 0.6,
            "IMBALANCE": 0.5,
        }
    )
    zone_status_factor: dict = field(
        default_factory=lambda: {
            "FRESH": 1.0,
            "TESTED": 0.7,
            "MITIGATED": 0.3,
            "BROKEN": 0.0,
        }
    )

    # --- Confluence ağırlıkları (Spec §7) ---
    confluence_weights: ConfluenceWeights = field(default_factory=ConfluenceWeights)

    # --- TF lookback haritası (Spec §4) ---
    tf_lookback: dict[TimeFrame, int] = field(
        default_factory=lambda: dict(TF_LOOKBACK)
    )

    # --- Sub-proje #2 — live runner config (Spec §6) ---
    live_symbols: list = field(
        default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    )
    live_exchange: str = "binance"
    live_asset_class: str = "futures_usdtm"
    live_scheduler_buffer_seconds: int = 5
    live_log_dir: str = "./logs"
    live_account_equity: float = 10000.0

    # --- Sub-proje #2 — binance adapter config (Spec §6) ---
    binance_testnet: bool = False
    binance_rate_limit_buffer: float = 0.8

    def lookback_bars(self, tf: TimeFrame) -> int:
        return self.tf_lookback[tf]

    def lookback_minutes(self, tf: TimeFrame) -> int:
        return self.tf_lookback[tf] * TF_MINUTES[tf]


# Düz (scalar) alan adları — YAML override için.
# Sub-map / kompleks alanlar — duz scalar override disinda tutulur.
_SUBMAP_FIELDS = (
    "confluence_weights",
    "tf_lookback",
    "poi_kind_quality",
    "zone_status_factor",
    # Sub-proje #2 — live + binance bloklari YAML'da "live:" / "binance:"
    # alti olarak gelir; flat scalar olarak okunamaz (alan adi prefix'siz
    # gelir). load_config bunlari ayri bir branch'te handle eder.
    "live_symbols",
    "live_exchange",
    "live_asset_class",
    "live_scheduler_buffer_seconds",
    "live_log_dir",
    "live_account_equity",
    "binance_testnet",
    "binance_rate_limit_buffer",
)

# Sub-proje #2 — "live:" YAML alti -> SMCConfig.live_<key> alani esleme.
_LIVE_KEYS = {
    "symbols": "live_symbols",
    "exchange": "live_exchange",
    "asset_class": "live_asset_class",
    "scheduler_buffer_seconds": "live_scheduler_buffer_seconds",
    "log_dir": "live_log_dir",
    "account_equity": "live_account_equity",
}

# Sub-proje #2 — "binance:" YAML alti -> SMCConfig.binance_<key> alani esleme.
_BINANCE_KEYS = {
    "testnet": "binance_testnet",
    "rate_limit_buffer": "binance_rate_limit_buffer",
}
# tuple alanlar yaml'dan liste gelir; scalar setattr ile listeyi tuple'a
# cevirerek alabiliriz, bu yuzden _SCALAR_FIELDS'te kalirlar.
_SCALAR_FIELDS = {
    f.name
    for f in fields(SMCConfig)
    if f.name not in _SUBMAP_FIELDS
}
_WEIGHT_FIELDS = {f.name for f in fields(ConfluenceWeights)}


# U-1: YAML degerlerini hedef tipine zorla — ``risk_pct: "abc"`` sessizce
# string atanmasini engelle. ``int``/``float``/``bool`` alanlarda cast dene;
# basarisizsa ``ValueError`` firlat (sessiz duzeltme yok, fail-fast).
_NUMERIC_TYPES = (int, float)


def _coerce_scalar(field_name: str, value, target_type):
    """YAML degerini hedef tipine cevirir; uyumsuzsa ``ValueError``.

    Bool ozel: Python ``bool`` ``int`` alt sinifi oldugu icin ``int``'e once
    bakilir; aksi halde ``True/False -> 1/0`` sessizce gecerdi.
    """
    if value is None:
        return value
    # Tuple alanlar list olarak gelir; cagiran taraf tuple'a cevirir.
    if target_type is tuple:
        return value
    # Hedef bool ise -> sadece bool kabul (int '1' yanlislikla True olmasin)
    if target_type is bool:
        if isinstance(value, bool):
            return value
        raise ValueError(
            f"config[{field_name}]: bool bekleniyordu, {type(value).__name__} geldi: {value!r}"
        )
    if target_type in _NUMERIC_TYPES:
        # bool, int alt sinifi — explicit reddet (``risk_pct: true`` -> hata).
        if isinstance(value, bool):
            raise ValueError(
                f"config[{field_name}]: {target_type.__name__} bekleniyordu, bool geldi: {value!r}"
            )
        try:
            return target_type(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"config[{field_name}]: {target_type.__name__} bekleniyordu ama "
                f"{type(value).__name__} cast edilemedi: {value!r}"
            ) from exc
    # str / dict / list / digerleri: dogrudan kabul (gelecekteki alanlar icin)
    return value


def _coerce_for_field(field_obj, value):
    """Dataclass alaninin tipine bakarak ``_coerce_scalar`` cagir.

    Sadece basit annotation'lar (``int``, ``float``, ``bool``, ``str``, ``tuple``)
    icin coercion uygulanir; karmasik annotation'lar (``dict``, ``Optional[..]``,
    ``Union[..]``) dokunulmadan gecer.
    """
    annot = field_obj.type
    if isinstance(annot, str):
        # ``from __future__ import annotations`` -> string annotation
        s_ann = annot.strip()
        if s_ann == "int":
            return _coerce_scalar(field_obj.name, value, int)
        if s_ann == "float":
            return _coerce_scalar(field_obj.name, value, float)
        if s_ann == "bool":
            return _coerce_scalar(field_obj.name, value, bool)
        if s_ann == "str":
            # str bekleniyorsa ama int geldi -> str'e cevir (geri uyumluluk)
            if not isinstance(value, str):
                return str(value)
            return value
        # tuple[...], dict[...], Optional[..] -> dokunma
        return value
    # Runtime tip nesnesi (eski stil)
    if annot in _NUMERIC_TYPES:
        return _coerce_scalar(field_obj.name, value, annot)
    return value


_FIELDS_BY_NAME = {f.name: f for f in fields(SMCConfig)}


def load_config(path: Optional[str | Path] = None) -> SMCConfig:
    """YAML dosyasından config yükle; varsayılanların üzerine yazar.

    ``path`` None veya dosya yoksa salt varsayılan ``SMCConfig`` döner.
    Desteklenen YAML anahtarları:
      - düz detektör/risk parametreleri (``swing_lookback`` vb.)
      - ``confluence_weights:`` alt-haritası
      - ``tf_lookback:`` alt-haritası (anahtarlar TF ismi string: "D1", "H4"...)
    """
    config = SMCConfig()
    if path is None:
        return config

    p = Path(path)
    if not p.exists():
        return config

    with p.open("r", encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh) or {}

    if not isinstance(data, dict):
        return config

    _TUPLE_FIELDS = ("tp_r_multiples", "tp_weights")
    for key, value in data.items():
        if key in _SCALAR_FIELDS:
            if key in _TUPLE_FIELDS and isinstance(value, list):
                value = tuple(value)
            else:
                # U-1: int/float/bool/str alan icin tip-coercion; uyumsuzsa
                # ValueError. Tuple alanlar zaten yukarida list->tuple olur.
                field_obj = _FIELDS_BY_NAME.get(key)
                if field_obj is not None:
                    value = _coerce_for_field(field_obj, value)
            setattr(config, key, value)
        elif key == "confluence_weights" and isinstance(value, dict):
            for wk, wv in value.items():
                if wk in _WEIGHT_FIELDS:
                    # ConfluenceWeights tum alanlar float
                    try:
                        wv = float(wv)
                    except (TypeError, ValueError) as exc:
                        raise ValueError(
                            f"config[confluence_weights.{wk}]: float bekleniyordu, "
                            f"{type(wv).__name__} cast edilemedi: {wv!r}"
                        ) from exc
                    setattr(config.confluence_weights, wk, wv)
        elif key == "tf_lookback" and isinstance(value, dict):
            for tf_name, bars in value.items():
                tf = TimeFrame[tf_name] if isinstance(tf_name, str) else tf_name
                config.tf_lookback[tf] = bars
        elif key in ("poi_kind_quality", "zone_status_factor") and isinstance(
            value, dict
        ):
            # Alt-harita override — tf_lookback desenindeki gibi: yalnizca
            # verilen anahtarlar ezilir, gerisi varsayilan kalir.
            target = getattr(config, key)
            for sub_k, sub_v in value.items():
                target[sub_k] = sub_v
        elif key == "live" and isinstance(value, dict):
            for sub_k, sub_v in value.items():
                attr = _LIVE_KEYS.get(sub_k)
                if attr is None:
                    continue
                setattr(config, attr, sub_v)
        elif key == "binance" and isinstance(value, dict):
            for sub_k, sub_v in value.items():
                attr = _BINANCE_KEYS.get(sub_k)
                if attr is None:
                    continue
                setattr(config, attr, sub_v)
        # bilinmeyen anahtarlar sessizce yok sayılır

    return config
