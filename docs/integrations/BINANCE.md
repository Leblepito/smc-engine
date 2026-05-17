# Binance Live Signal Pipeline (Sub-proje #2)

SMC engine v1'in offline motorunu canlı Binance USDT-M futures verisine bağlar.
Her M15 kapanışından ~5 saniye sonra orchestrator çalışır; üretilen
`ValidatedSetup` / `Rejection` kayıtları JSONL'e ve stdout'a yazılır.
**Emir gönderme YOK** — log-only mod (gerçek emir akışı sub-proje #5).

## Kurulum

1. Bağımlılıkları kur (zaten `pyproject.toml`'da):
   ```bash
   pip install -e ".[dev]"
   ```
   Yeni paketler: `python-binance`, `apscheduler`, `python-dotenv`.

2. `.env` oluştur (`.env.example`'i kopyala):
   ```bash
   cp .env.example .env
   ```
   Binance'ta **read-only** API key oluştur:
   - Account → API Management → Create API
   - "Enable Reading" işaretle, **"Enable Futures" KAPALI** bırak
   - Key + secret'i `.env`'ye yaz

   Public futures kline endpoint'leri çoğunlukla key'siz de çalışır; key
   olmadan da pipeline koşar (rate-limit avantajı için yine de tavsiye edilir).

## Çalıştırma

Tek sembol:
```bash
python examples/run_live.py --symbols BTCUSDT --equity 10000
```

Çoklu sembol (varsayılan: BTC + ETH + SOL):
```bash
python examples/run_live.py
```

Tüm flag'ler:
```
--symbols          Virgülle ayrılmış (default: config.live_symbols)
--equity           R-sizing için sabit equity (default: 10000)
--log-dir          JSONL log dizini (default: ./logs)
--testnet          Binance futures testnet
--buffer-seconds   M15 kapanışından sonra cron offset (default: 5)
```

Ctrl+C ile temiz kapanır (scheduler.stop + adapter.close).

## Sinyal log formatı

`logs/signals-YYYYMMDD.jsonl` — her satır bir JSON event (UTF-8, sort_keys).

**ValidatedSetup:**
```json
{
  "ts": "2026-05-16T15:00:05+00:00",
  "symbol": "BTCUSDT",
  "timeframe": "M15",
  "at_bar": "2026-05-16T14:45:00",
  "kind": "validated_setup",
  "setup": {
    "direction": "LONG",
    "entry": 67432.5,
    "sl": 67100.0,
    "tp": [67750.5, 68250.0, 69000.0],
    "tp_weights": [0.5, 0.3, 0.2],
    "rr": 0.96,
    "confluence_score": 0.62,
    "confluence_factor_count": 3,
    "bias_context": "BULLISH",
    "poi": {...}
  },
  "position_size": 0.0298,
  "risk_amount": 100.0,
  "guard_log": ["confluence", "regime", ...]
}
```

**Rejection:**
```json
{
  "ts": "...",
  "symbol": "ETHUSDT",
  "kind": "rejection",
  "gate": "regime",
  "reason": "LONG setup ama HTF bias BEARISH",
  "setup": {...}
}
```

Günlük rotasyon (UTC) — `signals-20260516.jsonl`, `signals-20260517.jsonl`, ...

## Mimari

```
APScheduler (M15:05 cron)
        │
        ▼
LiveRunner.run_once(symbol)
        │
        ▼
BinanceAdapter.fetch_ohlcv(D1, H4, M15)  ── BinanceClient (python-binance wrap)
        │
        ▼
orchestrator.analyze(..., at_bar=last_closed_M15, cache=per_symbol_htf)
        │
        ▼ MarketPicture
setup_builder.build → Setup | None
        │
        ▼
risk_guard.validate(setup, AccountState(static, equity), config)
        │
        ▼ ValidatedSetup | Rejection
SignalLogger.emit → signals-YYYYMMDD.jsonl + stdout
```

**Look-ahead garantisi:** `at_bar = last_closed_M15.open_time`. Forming bar
(`is_closed=False` veya `close_time > now`) BinanceAdapter tarafında filtrelenir.

**HTF cache:** per-symbol RAM cache (cross-symbol kontaminasyon yok). Runner
ömrü boyu yaşar; restart'ta sıfırdan ısınır.

**AccountState (log-only):** statik snapshot, `open_position=False`,
`consecutive_losses=0`, `max_drawdown_pct=0.0`. Bu yüzden `drawdown_breaker` +
`averaging` gate'leri pasif; diğer gate'ler (`confluence`, `regime`, `deviation`,
`no_sl`, `min_rr`, `funding`) tam çalışır.

## Troubleshooting

| Sorun | Çözüm |
|---|---|
| `ImportError: No module named 'binance'` | `pip install -e ".[dev]"` |
| `BinanceAPIException: Invalid API-key` | `.env`'deki key/secret'i kontrol; permissions read-only |
| Boş `signals.jsonl` | Normal — kalite-filtreli; kalite eşiğinin altında setup yok demek |
| Tüm sinyaller rejection | Gate konfigürasyonunu gevşet (`min_confluence_factors`, `min_rr`) |
| Scheduler tetiklenmiyor | Sistem saatini kontrol (NTP); cron `0,15,30,45 * * * *` + `--buffer-seconds` |

## Sonraki adım (sub-proje #5)

`signals.jsonl` üzerinden birkaç gün canlı izleme → kaliteyi değerlendir →
kaliteli ise sub-proje #5 (execution + risk yönetimi): gerçek emir gönderme,
`SMC_ALLOW_LIVE=1` env guard, $100 canlı test.
