"""SMC Engine tipli veri modeli — Spec §4.

Tüm zaman referansları ``datetime`` bazlı (asla integer ``candle_idx`` değil).
Detektör çıktıları ve yardımcı tipler ``frozen=True`` (immutable);
kompozit / mutable state tipleri ``frozen=False``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    import pandas as pd

# ============================================================
# Enum'lar
# ============================================================


class TimeFrame(Enum):
    M15 = "M15"
    H1 = "H1"
    H4 = "H4"
    H8 = "H8"
    D1 = "D1"


class Bias(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class Direction(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class ZoneKind(Enum):
    SUPPLY = "SUPPLY"
    DEMAND = "DEMAND"


class ZoneStatus(Enum):
    FRESH = "FRESH"
    TESTED = "TESTED"
    MITIGATED = "MITIGATED"
    BROKEN = "BROKEN"


class ZoneAnchor(Enum):
    WICK = "WICK"
    BODY = "BODY"


class ImbalanceKind(Enum):
    FVG = "FVG"
    LIQ_VOID = "LIQ_VOID"
    INEFFICIENCY = "INEFFICIENCY"


class LevelKind(Enum):
    YO = "YO"
    MO = "MO"
    WO = "WO"
    DO = "DO"
    PMO = "PMO"
    PWO = "PWO"
    MONDAY_H = "MONDAY_H"
    MONDAY_L = "MONDAY_L"
    OLD_ATH = "OLD_ATH"
    PREV_ATH = "PREV_ATH"


class LiquidityKind(Enum):
    SWEEP = "SWEEP"
    DEVIATION = "DEVIATION"
    SFP = "SFP"


class Significance(Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class StructureKind(Enum):
    CHoCH = "CHoCH"
    BOS = "BOS"


class SwingKind(Enum):
    HIGH = "HIGH"
    LOW = "LOW"


class POIKind(Enum):
    ZONE = "ZONE"
    IMBALANCE = "IMBALANCE"
    LEVEL = "LEVEL"


# ============================================================
# Yardımcı tipler (frozen=True)
# ============================================================


@dataclass(frozen=True)
class SwingPoint:
    """``_swing_utils`` çıktısı; range + structure detektörleri tüketir.

    ``confirm_timestamp`` (KR-2): Swing'in 4-mum kuralıyla resmi olarak
    onaylandığı bar timestamp'i. Bir swing high/low ancak ``lookback`` mum
    sonra (sağında ``lookback`` bar kapandıktan sonra) onaylanır; bu zamana
    kadar swing real-time bilinmez. Structure detektörü swing'i aktif
    sayma için ``confirm_timestamp <= ts`` koşulunu kullanmalı (look-ahead
    sızıntısı önlemek için). Geriye uyumluluk: alan opsiyonel, default
    ``None`` (verilmediği durumda detektör fallback olarak swing
    timestamp'ini kullanır — eski semantik).
    """

    timestamp: datetime
    price: float
    kind: SwingKind
    confirm_timestamp: Optional[datetime] = None


# ============================================================
# Detektör çıktıları (frozen=True)
# ============================================================


@dataclass(frozen=True)
class Range:
    high: float
    low: float
    equilibrium: float
    premium_zone: tuple[float, float]
    discount_zone: tuple[float, float]
    timeframe: TimeFrame
    formed_at: datetime


@dataclass(frozen=True)
class Zone:
    kind: ZoneKind
    top: float
    bottom: float
    timeframe: TimeFrame
    created_at: datetime
    status: ZoneStatus
    origin_candle_ts: datetime
    anchor: ZoneAnchor
    age_bars: int


@dataclass(frozen=True)
class Imbalance:
    kind: ImbalanceKind
    top: float
    bottom: float
    direction: Direction
    timeframe: TimeFrame
    created_at: datetime
    filled: bool
    fill_ratio: float


@dataclass(frozen=True)
class Level:
    kind: LevelKind
    price: float
    timeframe: TimeFrame
    valid_from: datetime
    valid_until: Optional[datetime]


@dataclass(frozen=True)
class LiquidityEvent:
    kind: LiquidityKind
    swept_price: float
    direction: Direction
    candle_ts: datetime
    reclaimed: bool
    significance: Significance


@dataclass(frozen=True)
class StructureBreak:
    kind: StructureKind
    direction: Direction
    broken_swing_price: float
    confirm_candle_ts: datetime
    timeframe: TimeFrame


# ============================================================
# POI referansı (frozen=True)
# ============================================================


@dataclass(frozen=True)
class POIRef:
    """Orchestrator'dan setup_builder'a geçen POI referansı."""

    kind: POIKind
    ref: Union[Zone, Imbalance, Level]
    htf_aligned: bool
    score_hint: float


# ============================================================
# Kompozit / mutable state tipleri (frozen=False)
# ============================================================


@dataclass
class Setup:
    direction: Direction
    entry: float
    sl: float
    tp: list[float]
    tp_weights: list[float]
    poi: POIRef
    confirmation: Optional[StructureBreak]
    bias_context: Bias
    confluence_score: float
    rr: float
    created_at: datetime
    # Sifir-olmayan confluence faktoru sayisi (6 faktorden kac tanesi >0).
    # risk_guard confluence gate'i bu sayiyi config.min_confluence_factors ile
    # karsilastirir. Default'lu (sona) eklendi -> mevcut Setup yapimlari kirilmaz.
    confluence_factor_count: int = 0
    # --- volatility regime filter (Spec §13.2, 2026-05-23) ---
    # setup_builder olusturma sirasinda hesapladigi rejim olcumleri.
    # Su an: {"atr_percentile": float}. risk_guard._check_volatility_regime
    # bu sozlukten okur. Default factory mutable footgun'unu engeller.
    regime_metrics: dict = field(default_factory=dict)


@dataclass
class Rejection:
    """risk_guard red çıktısı."""

    reason: str
    gate: str
    setup: Setup


@dataclass
class AccountState:
    equity: float
    open_position: bool
    consecutive_losses: int
    max_drawdown_pct: float
    # U-6: ``recent_results`` risk_guard tarafindan KULLANILMIYORDU; alan
    # Optional ve default None'a dusuruldu (geriye uyumluluk icin alan
    # tutuldu — disaridan dolduran kod kirilmasin). Bir gun risk_guard
    # "son N trade net R" gate'i eklerse buradan okuyacak.
    recent_results: Optional[list[float]] = None


@dataclass
class ValidatedSetup:
    setup: Setup
    position_size: float
    risk_amount: float
    guard_log: list[str]


@dataclass
class TFSnapshot:
    range_: Optional[Range]
    bias: Bias
    zones: list[Zone]
    imbalances: list[Imbalance]
    levels: list[Level]
    liquidity_events: list[LiquidityEvent]
    structure: list[StructureBreak]
    # Bu TF'in (at_bar'a kadar dilimlenmis) OHLCV'sinden hesaplanan ATR.
    # orchestrator her snapshot insa ederken doldurur; default 0.0 ki
    # mevcut yapim yerleri (testler vb.) kirilmasin.
    atr: float = 0.0
    # --- volatility regime filter (Spec §13.2, 2026-05-23) ---
    # Son N H4 bar'in ATR degerleri (rolling history). orchestrator H4
    # snapshot insa ederken doldurur. None = warm-up veya eski test
    # fixture (geriye uyumluluk). setup_builder bu listeden ATR percentile
    # rank hesaplar.
    atr_history: Optional[list[float]] = None


@dataclass
class MarketPicture:
    per_tf: dict[TimeFrame, TFSnapshot]
    htf_bias: Bias
    htf_range: Optional[Range]
    active_pois: list[POIRef]
    at_timestamp: datetime
    current_price: float


# ============================================================
# Faz 5 — Backtest tipleri
# ============================================================


@dataclass  # frozen=False — current_sl breakeven'a kayar, remaining_size azalir
class Position:
    """Acik pozisyon — backtest harness + position_manager tarafindan mutate edilir."""

    direction: Direction
    entry: float
    entry_ts: datetime
    original_sl: float
    current_sl: float  # TP1 sonrasi breakeven (entry) degerine kayar
    tp: list[float]
    tp_weights: list[float]
    total_size: float
    remaining_size: float
    tp_hits: list[int]  # vurulan TP indeksleri (0-bazli)
    validated_setup: "ValidatedSetup"


@dataclass(frozen=True)
class Trade:
    """Kapanmis trade kaydi — immutable."""

    direction: Direction
    entry: float
    entry_ts: datetime
    exit_price: float
    exit_ts: datetime
    exit_reason: str  # "SL" / "TP1" / "TP2" / "TP3" / "BREAKEVEN" ...
    pnl: float
    r_multiple: float
    size: float
    confluence_score: float
    confluence_factor_count: int


@dataclass  # frozen=False
class BacktestResult:
    """Backtest sonucu — trades + equity curve + metrikler."""

    trades: list[Trade]
    equity_curve: "pd.Series"  # index=M15 timestamp, value=equity
    metrics: dict


# ============================================================
# Sub-proje #2 — borsa adapter veri tipleri (Spec §5)
# ============================================================
# ``_base.ExchangeAdapter`` Protocol bu tipleri tüketir / üretir; ortak
# olduğu için burada tanımlı, ``_base`` üzerinden re-export edilir.


@dataclass(frozen=True)
class SymbolMeta:
    """Sembol metadata — emir validation + R-sizing için (Spec §5)."""

    symbol: str
    tick_size: float
    lot_size: float
    min_qty: float
    price_precision: int
    qty_precision: int
    min_notional: float = 0.0


@dataclass(frozen=True)
class Kline:
    """Tek bar (Spec §5). ``is_closed=False`` forming bar; analiz dışında bırakılır."""

    symbol: str
    timeframe: TimeFrame
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool
