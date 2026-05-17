# SMC Engine — Sub-proje #2 (Binance) İmplementasyon Planı

> Tarih: 2026-05-16 · Spec: `smc-engine-subproject-2-binance-design-2026-05-16.md`
> Metodoloji: writing-plans (faz'a bölünmüş bite-sized TDD task'lar, tam dosya yolları, doğrulama adımları)
> Hedef ortam: **Claude Code (VSCode, lokal)** — `C:\Users\utkuc\OneDrive\Masaüstü\smc-engine\`

## Nasıl okunmalı

Her task tek aksiyon (2-5 dk). Tahminî süre parantezde. TDD: önce başarısız test, sonra minimal impl, sonra yeşil. Her task'ın bir **doğrulama** adımı var. Faz sonunda commit + push.

**Bağımlılık sırası:** B0 → B1 → B2 → B3 → B4 → B5.

**Mevcut 354 test KIRILMAMALI.** Faz B1+ her task sonrası hızlı suite çalıştırılır.

Stack: Python 3.10+, python-binance, apscheduler, python-dotenv, pytest. Mevcut: ccxt, pandas, pyarrow.

---

## Faz B0 — Proje hazırlığı (~25 dk)

**B0.1** (~5dk) `pyproject.toml` güncelle: `[project.dependencies]`'e ekle:
```toml
"python-binance>=1.0.21",
"apscheduler>=3.10",
"python-dotenv>=1.0",
```
Doğrulama: `pip install -e ".[dev]"` hatasız.

**B0.2** (~3dk) `.env.example` oluştur:
```
BINANCE_API_KEY=
BINANCE_API_SECRET=
SMC_ALLOW_LIVE=0
```
`.gitignore`'da `.env` zaten var; teyit et. Doğrulama: `git status` `.env.example` track ediyor, `.env` ignore ediyor.

**B0.3** (~7dk) `smc_engine/config.py`'a Spec §6'daki `live` + `binance` blokları için alanlar ekle (`SMCConfig.live_*`, `SMCConfig.binance_*`). `load_config` YAML override mekanizmasını genişlet. Test: `tests/test_config.py`'a yeni alan default + override testleri.

**B0.4** (~8dk) **Test+impl:** `smc_engine/integrations/_base.py` — `ExchangeAdapter` Protocol (Spec §3), `SymbolMeta` + `Kline` dataclass'ları (Spec §5). `smc_engine/types.py`'a `SymbolMeta` ve `Kline` export'larını ekle. Test: tipler örneklenebilir, Protocol implementasyonu type-check geçiyor.

**B0.5** Commit: `feat: subproject-2 hazırlığı (deps, .env scaffold, _base Protocol, types)` + push.

---

## Faz B1 — Binance adapter & client (~50 dk)

### B1.0 (~3dk) Dizinleri oluştur
`smc_engine/integrations/binance/__init__.py` (boş), `smc_engine/integrations/binance/` dizini.

### B1.1 Client (~20dk)
- **B1.1.1** (~7dk) Test: `tests/test_binance_client.py` — `requests-mock` veya `unittest.mock` ile python-binance.Client sarmalanmış halini test et: rate-limit tampon (`rate_limit_buffer=0.8` ile %80'de wait/yield), retry (3 deneme, exponential backoff), 5xx error path, 4xx pass-through. **KIRMIZI**.
- **B1.1.2** (~10dk) Impl: `smc_engine/integrations/binance/client.py` — `BinanceClient` sınıfı, `python-binance.Client` wrap, USDT-M futures endpoint'leri (`futures_klines`, `futures_funding_rate`, `futures_open_interest`, `futures_exchange_info`). Auth: `BINANCE_API_KEY` + `BINANCE_API_SECRET` env'den. **YEŞİL**.
- **B1.1.3** (~3dk) Doğrulama: `pytest tests/test_binance_client.py -v` yeşil. Commit.

### B1.2 Adapter (~25dk)
- **B1.2.1** (~10dk) Test: `tests/test_binance_adapter.py` — mock `BinanceClient` ile:
  - `fetch_ohlcv(symbol, tf, lookback)` → DataFrame, DatetimeIndex, kolonlar tam, CCXT historical + son kline merge (forming bar dahil edilmez)
  - `fetch_funding_rate(symbol)` → float
  - `fetch_open_interest(symbol)` → float
  - `fetch_symbol_info(symbol)` → `SymbolMeta` (tick_size, lot_size, price_precision)
  - `ExchangeAdapter` Protocol uyumlu (type-check)
  **KIRMIZI**.
- **B1.2.2** (~12dk) Impl: `smc_engine/integrations/binance/adapter.py` — `BinanceAdapter` sınıfı, Protocol implementasyonu. Yardımcı modüller: `smc_engine/integrations/binance/data.py` (REST helper'ları) + `symbols.py` (sembol normalize, metadata cache). **YEŞİL**.
- **B1.2.3** (~3dk) Doğrulama: pytest yeşil. Tüm hızlı suite yeşil (mevcut 354 + yeni ~10).

**B1.3** Commit: `feat: BinanceAdapter REST + client (futures USDT-M)` + push.

---

## Faz B2 — Live runner & scheduler (~35 dk)

### B2.1 AccountState builder (~5dk)
- Test + impl: `smc_engine/live/account_state.py` — `build_static_account_state(config)` → log-only mod için Spec §8'deki statik `AccountState` döner. Test: değer tam doğru.

### B2.2 Scheduler (~10dk)
- **B2.2.1** Test: `tests/test_scheduler.py` — APScheduler `BackgroundScheduler` wrap, M15 kapanış + `scheduler_buffer_seconds` cron ifadesi doğru (`*/15 * * * *` + offset), fake-time ile tetikleme. **KIRMIZI**.
- **B2.2.2** Impl: `smc_engine/live/scheduler.py` — `LiveScheduler` sınıfı, `start(callback)`, `stop()`, M15:05 cron. **YEŞİL**.

### B2.3 Runner (~20dk)
- **B2.3.1** (~8dk) Test: `tests/test_live_runner.py` — FakeAdapter (sentetik OHLCV döndüren) ile end-to-end mock:
  - scheduler tick → adapter.fetch_ohlcv → orchestrator.analyze → setup_builder.build → risk_guard.validate → signal_logger.emit çağrılıyor mu
  - HTF cache runner ömrü boyu RAM'de tutuluyor mu
  - Adapter hatası → log error, sonraki tick'i bekle (crash etme)
  - Sembol başına ayrı pipeline (BTCUSDT + ETHUSDT)
  **KIRMIZI**.
- **B2.3.2** (~10dk) Impl: `smc_engine/live/runner.py` — `LiveRunner` sınıfı, `run(symbols, signal_logger)`, scheduler'a callback bağlar, `at_bar = last_closed_M15` hesaplar, HTF cache dict tutar. **YEŞİL**.
- **B2.3.3** (~2dk) Doğrulama: pytest yeşil.

**B2.4** Commit: `feat: live runner + scheduler + account_state` + push.

---

## Faz B3 — Signal logger (~20 dk)

**B3.1** (~8dk) Test: `tests/test_signal_logger.py`:
- JSONL roundtrip: `emit(ValidatedSetup)` → dosyaya satır yazılır → satır JSON parse edilince setup alanları tam
- Rejection da yazılır (`kind: "rejection"`, `gate`, `reason`)
- Günlük rotasyon: tarihe göre `signals-YYYYMMDD.jsonl`, gün dönünce yeni dosya
- Stdout'a da basılıyor (capsys)
- `log_dir` yoksa otomatik oluştur
**KIRMIZI**.

**B3.2** (~10dk) Impl: `smc_engine/live/signal_logger.py` — `SignalLogger` sınıfı:
- `__init__(log_dir)`: dizini oluştur
- `emit(validated_setup_or_rejection)`: günlük dosyaya JSONL satırı + stdout
- ISO timestamp, sembol, timeframe, at_bar, kind, payload
- Setup/Rejection serialize: `_to_dict` helper
**YEŞİL**.

**B3.3** (~2dk) Doğrulama + commit: `feat: signal logger (JSONL günlük rotasyon)` + push.

---

## Faz B4 — CLI + smoke (~15 dk)

**B4.1** (~10dk) Impl: `examples/run_live.py` — argparse CLI:
- `--symbols` (default config'den), `--equity` (default 10000), `--log-dir` (default `./logs`), `--testnet` (default false)
- `.env` yükle (python-dotenv)
- API key kontrol: yoksa açıklayıcı hata
- `BinanceAdapter` + `LiveRunner` + `SignalLogger` kur
- Ctrl+C handle: scheduler.stop() + signal_logger.close()
Doğrulama: `python examples/run_live.py --help` çıktı doğru.

**B4.2** (~5dk) Manuel smoke (kullanıcı yapar — script bir kez çalışır, sonra durur):
- `.env`'e gerçek read-only API key yaz
- `python examples/run_live.py --symbols BTCUSDT --equity 10000` çalıştır
- 1 M15 cycle bekle (~16dk veya cron mock ile hemen tetikle)
- `logs/signals-YYYYMMDD.jsonl` dosyasını incele: setup veya rejection satırı var mı, format doğru mu
- Ctrl+C, log dosyası kapanır

**B4.3** Commit: `feat: live CLI (examples/run_live.py)` + push.

---

## Faz B5 — Bağımsız doğrulama + code-review

**B5.1** (~3dk) Tüm test suite: `pytest tests/ -q` — mevcut 354 + yeni ~30. Hepsi yeşil olmalı.

**B5.2** (~5dk) `engineering:code-review` skill ile öz-inceleme:
- `BinanceAdapter` kritik yollar (auth, rate limit, error handling)
- `LiveRunner` look-ahead garantisi (`at_bar = last_closed_M15`)
- `SignalLogger` JSONL determinism (sıralama, format)
- Mevcut testler tüm gate'leri kapsıyor mu (`engineering:testing-strategy`)

**B5.3** Bulgular varsa düzelt, tekrar test. Yoksa commit: `feat: subproject-2 v1 tamamlandı — live Binance signal pipeline (log-only)` + push.

**B5.4** README güncelle: `docs/integrations/BINANCE.md` (kurulum, .env, run_live komutu, signal logger format, troubleshooting).

---

## Toplam scope

| Faz | Süre | Yeni test sayısı | Ana dosyalar |
|---|---|---|---|
| B0 | 25 dk | 2 | pyproject, .env.example, _base.py |
| B1 | 50 dk | ~10 | binance/{client,adapter,data,symbols}.py |
| B2 | 35 dk | ~8 | live/{scheduler,runner,account_state}.py |
| B3 | 20 dk | ~5 | live/signal_logger.py |
| B4 | 15 dk | smoke | examples/run_live.py |
| B5 | 10 dk | — | code-review + docs |
| **Toplam** | **~2.5 saat** | **~25** | ~10 yeni dosya |

## Bağımlılık & risk notları

- **API key güvenliği:** `.env` repo'ya commit edilmemeli (gitignore zaten var). Read-only key kullan.
- **Look-ahead:** `at_bar = last_closed_M15`. Forming bar (şu anki) ASLA kullanılmaz.
- **HTF cache:** runner ömrü boyu RAM'de. Restart'ta sıfırdan ısınır (ilk M15 tick ~30sn yavaş).
- **Rate limit:** python-binance otomatik; manuel `rate_limit_buffer=0.8` tampon ile çift kat koruma.
- **Spec'in WS bölümleri v2'ye ertelendi** (Spec §2 "Dahil değil"). v1'de yalnız REST + scheduler.
- **#5 emir gönderme YOK** — bu sub-proje sadece sinyal üretir.

## Sonraki sub-proje

Bu tamamlandıktan sonra: birkaç gün `signals.jsonl` izle → sinyal kalitesini değerlendir → kaliteliyse **sub-proje #5 (execution & risk yönetimi)**: gerçek emir gönderme, mainnet guard, $100 canlı test.
