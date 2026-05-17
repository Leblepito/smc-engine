# SMC Engine — Sub-proje #2: Binance veri & bağlantı katmanı (Tasarım)

> Tarih: 2026-05-16 · Bağımlılık: smc-engine v1 (354 test, deploy-edilebilir) · Stack: Python 3.10+, python-binance, ccxt, APScheduler, pytest

---

## 1. Amaç & Sınır

smc-engine v1 sinyal üretebiliyor (offline backtest); bu sub-proje motoru **canlı Binance USDT-M futures verisine** bağlar, kapanan her M15 barından sonra sinyal üretir ve loglar. **Emir gönderme YOK** — o sub-proje #5'in işi. v1 = "log-only" mod.

**Doğal sıra:**
1. Bu sub-proje #2 (log-only) → birkaç gün sinyalleri izle → kaliteyi değerlendir
2. Kaliteliyse #5 (execution layer)
3. Kullanıcı $100 ile 4-5 coinde canlı test, risk_guard sıkı parametrelerle

---

## 2. Kapsam

### Dahil
- `BinanceAdapter` — `ExchangeAdapter` protokolünün ilk somut implementasyonu (futures USDT-M perpetual)
- Historical OHLCV fetch (CCXT, mevcut `data/fetch.py` adapter'a sarmalanır)
- Live REST market data (klines, funding rate, open interest, ticker)
- Sembol metadata (tick size, lot size, price precision) — #5'te emir validation için lazım
- Live runner: APScheduler ile zamanlanmış pipeline (M15 kapanış + 5sn buffer) — **scheduler tek tetikleyici**
- Signal logger: JSONL + stdout (günlük rotasyon)
- Read-only API key auth (env-based)
- CLI: `python -m smc_engine.live --symbols BTCUSDT,ETHUSDT,SOLUSDT`

### Dahil değil (#2 dışı, sonraya)
- Emir gönderme (place_order, cancel_order, fetch_positions) → #5
- **WS kline stream** → v2 (alt-saniye latency gerekirse). v1'de REST + scheduler yeterli.
- Paper mode → MT5 sub-projesine bırakıldı (kullanıcı kararı)
- MT5/forex adapter → sub-proje #2.5
- Spot trading → şu an futures-only (gerekirse v2)
- Backtest motoru genişletmesi → v1'de yeterli

---

## 3. Mimari

### Adapter pattern (Spec §11 + efloud-bot/CLAUDE.md ile uyumlu)

```python
# smc_engine/integrations/_base.py (yeni)
class ExchangeAdapter(Protocol):
    def fetch_ohlcv(self, symbol: str, timeframe: TimeFrame, lookback_bars: int) -> pd.DataFrame: ...
    def fetch_funding_rate(self, symbol: str) -> float: ...
    def fetch_open_interest(self, symbol: str) -> float: ...
    def fetch_symbol_info(self, symbol: str) -> SymbolMeta: ...   # tick, lot, precision
    def subscribe_klines(self, symbol: str, timeframe: TimeFrame, on_close: Callable[[Kline], None]) -> None: ...
    def close(self) -> None: ...
```

`BinanceAdapter` bu protokolü implement eder. İleride `MT5Adapter`, `OandaAdapter` aynı protokole oturur.

### Akış (pull-based, WS yalnız trigger)

```
APScheduler M15:00 + 5sn buffer
    │
    ▼
BinanceAdapter.fetch_ohlcv(symbol, [D1, H4, M15], TF_LOOKBACK[tf])
    │
    ▼ {TimeFrame: DataFrame}
orchestrator.analyze(ohlcv_by_tf, config, at_bar=last_closed_M15, cache=htf_cache)
    │
    ▼ MarketPicture
setup_builder.build(picture, config)
    │
    ▼ Setup | None
risk_guard.validate(setup, account_state, config)
    │
    ▼ ValidatedSetup | Rejection
signal_logger.emit(...)
    │
    ▼
signals.jsonl  +  stdout
```

**Look-ahead garantisi:** `at_bar = last_closed_M15_timestamp` — şu anki forming bar değil, son kapanmış bar. Orchestrator `_slice_to_at_bar` + TF_LOOKBACK alt-sınır (R1 fix'i) zaten doğru davranıyor.

**WS rolü (minimal):** kline `is_closed=True` push'u yalnızca scheduler'a "yeni M15 kapandı, hemen çalış" sinyali. Pull'u tetikler, analiz hâlâ pull'dan. WS reconnect kırılırsa scheduler timer'ı zaten her M15:05'te çalışır → tolerans var.

---

## 4. Dosya yapısı

```
smc_engine/integrations/
├── _base.py                    # YENİ — ExchangeAdapter Protocol, SymbolMeta, Kline dataclass
├── binance/
│   ├── __init__.py             # adapter export
│   ├── adapter.py              # BinanceAdapter (orchestrasyon, ExchangeAdapter impl)
│   ├── client.py               # python-binance REST + WS sarmalayıcısı, retry/rate-limit
│   ├── data.py                 # fetch_klines, fetch_funding, fetch_oi (REST)
│   ├── ws.py                   # WS subscription manager (kline stream, on_close callback)
│   └── symbols.py              # USDT-M perp sembol normalize, tick/lot/precision metadata
│
└── tradingview/                # MEVCUT — değişmiyor

smc_engine/live/
├── __init__.py
├── runner.py                   # canlı pipeline: scheduler tetikler → analyze → emit
├── scheduler.py                # APScheduler wrapper (M15 close + buffer cron)
├── account_state.py            # AccountState builder (log-only mod için statik/dummy)
└── signal_logger.py            # ValidatedSetup/Rejection → JSONL + stdout

examples/
└── run_live.py                 # CLI entrypoint: --symbols, --equity, --log-dir vb.

tests/
├── test_binance_adapter.py     # mock client ile adapter testleri
├── test_binance_client.py      # client wrapper testleri (mock REST)
├── test_live_runner.py         # runner end-to-end (mock adapter)
└── test_signal_logger.py       # JSONL format, replay edilebilir mi
```

**~900 satır kod + ~400 satır test.** Faz 5'e benzer hacim, tek build turunda biter.

---

## 5. Veri tipleri (types.py'a eklenecek)

```python
@dataclass(frozen=True)
class SymbolMeta:
    symbol: str
    tick_size: float
    lot_size: float
    min_qty: float
    price_precision: int
    qty_precision: int

@dataclass(frozen=True)
class Kline:
    symbol: str
    timeframe: TimeFrame
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool
```

---

## 6. Konfigürasyon

`SMCConfig`'e eklenir (env-override mantığı korunur):

```yaml
# config.yaml
live:
  symbols: [BTCUSDT, ETHUSDT, SOLUSDT]
  exchange: binance
  asset_class: futures_usdtm
  scheduler_buffer_seconds: 5
  log_dir: ./logs
  account_equity: 10000.0      # log-only mod için varsayılan (sadece R-sizing hesabı)
binance:
  testnet: false
  rate_limit_buffer: 0.8       # rate-limit'in %80'inde dur
```

API kimlikleri `.env`:
```
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
```
`.gitignore`'da `.env` zaten var.

---

## 7. Güvenlik

- **Read-only API key:** Binance'ta key oluştururken yalnızca "Enable Reading" işaretlenir. "Enable Futures" işareti **off**. Bu key kaybolsa bile zarar veremez.
- **Mainnet guard:** `--live` flag bilinçli olarak implement edilmiyor (#5'te `SMC_ALLOW_LIVE=1` env şartı eklenecek, efloud-bot pattern'i).
- **Rate limit:** python-binance otomatik kontrol; ayrıca `rate_limit_buffer=0.8` ile manuel tampon.
- **Reconnect:** WS koparsa otomatik reconnect (3 saniye backoff, 5 deneme), başaramazsa scheduler timer'ı zaten bağımsız çalışıyor.

---

## 8. AccountState canlıda (log-only mod)

risk_guard `AccountState` istiyor. Log-only mod'da gerçek hesap bakiyesi/pozisyon çekmiyoruz; yerine **statik bir AccountState** kullanırız:

```python
account_state = AccountState(
    equity=config.live.account_equity,    # config'den (default 10000)
    open_position=False,                  # log-only'da her zaman False (emir yok)
    recent_results=None,
    consecutive_losses=0,
    max_drawdown_pct=0.0,
)
```

#5'te bu Binance'ın gerçek `fetch_account` çıktısından doldurulur.

**Sonuç:** log-only mod'da risk_guard'ın `drawdown_breaker` ve `averaging` gate'leri pasif. Diğer gate'ler (`confluence`, `regime`, `deviation`, `no_sl`, `min_rr`, `session/funding`) tam çalışır → sinyaller hâlâ kalite-filtreli.

---

## 9. Signal logger format (`signals.jsonl`)

Her satır bir JSON event. Replay edilebilir, post-mortem analiz için zengin.

```json
{
  "ts": "2026-05-16T15:00:05Z",
  "symbol": "BTCUSDT",
  "timeframe": "M15",
  "at_bar": "2026-05-16T14:45:00Z",
  "kind": "validated_setup",
  "setup": {
    "direction": "LONG",
    "entry": 67432.5,
    "sl": 67100.0,
    "tp": [67750.5, 68250.0, 69000.0],
    "tp_weights": [0.5, 0.3, 0.2],
    "rr": 0.96,
    "confluence_score": 0.62,
    "confluence_factor_count": 3
  },
  "guard_log": ["confluence","regime","deviation","no_sl","min_rr","averaging","drawdown_breaker","funding","r_sizing"],
  "position_size": 0.0298
}
```

Rejection da loglanır:
```json
{"ts":"...","symbol":"ETHUSDT","kind":"rejection","gate":"regime","reason":"LONG setup ama HTF bias BEARISH — yön ters"}
```

→ Sonradan `signals.jsonl` üzerinde walk-forward/bootstrap çalıştırmak mümkün (canlıda üretilen sinyaller backtest'lenebilir).

---

## 10. Hata yönetimi

| Senaryo | Davranış |
|---|---|
| Binance API down | Retry 3x, başaramazsa o turu atla (log "fetch failed"), bir sonraki M15'te tekrar dene |
| WS bağlantı koparsa | Otomatik reconnect; başaramazsa scheduler timer bağımsız çalışır |
| Rate limit hit | python-binance throttle; manuel tampon `rate_limit_buffer` ile |
| Sembol delisting | Adapter `fetch_symbol_info` boş döndüğünde sembol skip (log warning) |
| Sistem saati kayması | Binance server time ile karşılaştır, >5sn fark warn log |
| Disk full (log_dir) | Stdout'a yedek, JSONL skip + error log |

---

## 11. Test stratejisi

| Test | Yöntem |
|---|---|
| `BinanceAdapter` | Mock `python-binance.Client` ile birim test; gerçek API çağrısı YOK |
| `BinanceClient` | requests-mock ile REST mock, ws-mock ile WS event simülasyonu |
| `LiveRunner` | FakeAdapter ile end-to-end: sentetik OHLCV → orchestrator → setup → guard → logger çağrılıyor mu |
| `SignalLogger` | JSONL format roundtrip (write → read → assert), Setup serialize/deserialize |
| Smoke (manuel) | Gerçek read-only key ile `examples/run_live.py` 1 dakika koş, log dosyasını incele |

**Ağ erişimi gerektiren testler `pytest.mark.integration` ile işaretlenir** ve CI'da skip edilir (kullanıcı yerel makinede çalıştırır).

---

## 12. Kararlar & gerekçeler

| Karar | Seçim | Gerekçe |
|---|---|---|
| Borsa | **Binance USDT-M futures** | efloud-bot uyumu, en likit, SMC fit, kullanıcı pivotu |
| Asset class | Futures perpetual | Funding window mantığımız zaten futures için, leverage, kısa açma |
| Akış modeli | **Pull-based scheduler** | Basit, deterministik, SMC bar-close based, test edilebilir |
| Kütüphane | `python-binance` + mevcut `ccxt` | Mature, free, REST+WS+futures; CCXT historical için bırakıldı |
| Scheduler | APScheduler | Cron syntax, persistent jobs, bot-friendly |
| Sembol seti | BTC + ETH + SOL | Likitete, sinyal frekansı, config'lenebilir |
| Paper mode | **Skip** (MT5 sub-projesine) | Kullanıcı kararı: yerine $100 canlı test (#5'te) |
| Auth scope | Read-only | #2 emir göndermiyor; risk yok |
| `--live` mod | **#2'de yok** | Mainnet guard #5'te; bilinçli kapatma |

---

## 13. Trade-off'lar

1. **Pull cadence vs WS push:** Pull seçildi → 5sn gecikme kabul (SMC bar-close based, sorun yok). v2'de WS-driven ekleme yarım gün.
2. **Multi-symbol: tek runner vs çoklu process:** Tek runner v1, log'lar konsolide. Yatay ölçek sonra (Docker container/sembol).
3. **HTF cache canlıda:** RAM'de runner ömrü boyu kalır, restart'ta sıfırdan ısınır (~30sn). Persistence v2.
4. **AccountState statik (log-only):** drawdown_breaker pasif. Sinyal kalitesini etkilemez; #5'te dinamik olur.

---

## 14. Açık noktalar

- **Sembol sayısı:** Kullanıcı 3 (BTC+ETH+SOL) onayladı. "SOL-EXP" yazımı netleşmedi — XRP/BNB ekleme ihtimali config-bazlı (kod değişikliği yok).
- **Scheduler süresi:** M15 + 5sn — Binance kapanış garantisinden güvenli marj. Test edilecek.
- **`signals.jsonl` rotasyon:** Günlük yeni dosya mı tek büyük dosya mı? **v1: günlük rotasyon** (`signals-YYYYMMDD.jsonl`), kolay temizlik.

---

## 15. Bağımlılıklar (yeni)

```toml
# pyproject.toml [project.dependencies]'a eklenir:
"python-binance>=1.0.21",   # REST + WS
"apscheduler>=3.10",        # zamanlama
"python-dotenv>=1.0",       # .env loader
```

`ccxt` zaten var.

---

## 16. Sonraki adım

Spec onaylanırsa → `writing-plans` skill'i ile implementasyon planı yazılır (faz'lara bölünmüş bite-sized TDD task'lar, ~6-8 task), sonra skill-kullanan build agent ile inşa edilir, bağımsız doğrulama, code-review.

**Tahmini scope:** 1 faz, 6-8 task, ~900 satır kod + ~400 satır test, Faz 5'e benzer hacim.
