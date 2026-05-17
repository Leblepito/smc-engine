# SMC Engine — Sub-proje #2 (Binance live signal pipeline) | Claude Code Brief

> Bu dokümanı Claude Code'a tek seferde ver. 5 faz, ~25 task, ~2.5 saat ajan iş.
> Bittiğinde commit: `feat: subproject-2 v1 tamamlandı — live Binance signal pipeline (log-only)`

---

## Bağlam

- **Repo:** `smc-engine` (v1 bitti, 354 test yeşil, deploy-edilebilir). Branch: `main`.
- **Spec:** `docs/subproject-2/smc-engine-subproject-2-binance-design-2026-05-16.md` — 16 bölüm; oku.
- **Plan:** `docs/subproject-2/smc-engine-subproject-2-binance-plan-2026-05-16.md` — 5 faz, bite-sized TDD task'lar; bu sırayı takip et.
- **Çalışma dizini:** repo kökü (`smc-engine/`). VSCode + Claude Code üzerinde lokal.

## Amaç tek cümlede

smc-engine v1'in offline motorunu canlı Binance USDT-M futures verisine bağla; her M15 kapanışında orchestrator → setup_builder → risk_guard akışını çalıştır; üretilen `ValidatedSetup` / `Rejection`'ları **log-only** mod'da JSONL + stdout'a yaz. **Emir gönderme YOK** — o #5'in işi.

## Kurallar (esnetilmez)

1. **TDD:** her impl'den önce başarısız test yaz, kırmızı gör, sonra impl, sonra yeşil. Her task sonrası pytest çalıştır.
2. **Mevcut 354 test KIRILMAMALI.** Faz sonu hızlı suite + ilgili yavaş chunk yeşil olmalı.
3. **Skill kullan:**
   - Yeni testleri planlamadan önce `engineering:testing-strategy`
   - Faz B5'te (ve dönmeden önce) `engineering:code-review` ile öz-inceleme
   - "Tamamlandı" demeden önce **taze pytest kanıtı** olmadan claim atma (`engineering:verification-before-completion` çerçevesi)
4. **Look-ahead bias YOK:** runner `at_bar = last_closed_M15_timestamp` kullanır; forming bar asla görmez. Plan B2.3.2'ye bak.
5. **Timestamp bazlı**, `candle_idx` yok. Python 3.10+, dataclass, type hints. Pydantic yok.
6. **API key güvenliği:** `.env` repo'ya commit etme (gitignore zaten ignore ediyor). `.env.example` template olarak commit'lenir. **Read-only Binance key yeterli** (#2 emir göndermiyor).
7. **Emir gönderme yasak (#2'de):** `place_order`, `cancel_order` gibi fonksiyonlar bu sub-projede İMPLEMENT EDİLMEZ. `--live` flag bilinçli olarak yok. Bunlar #5'in işi.
8. **Her faz sonu commit + push:** plan'daki commit mesajlarını kullan.

## İlk adımlar (sırayla)

1. **Spec ve plan'ı oku** (`docs/subproject-2/` altında).
2. **Sandbox kontrolü:** `pytest tests/ -q --ignore=tests/test_harness.py --ignore=tests/test_backtest_e2e.py --ignore=tests/test_walk_forward.py --ignore=tests/test_r2a_walkforward_content.py --ignore=tests/test_r2a_lookahead_trade.py --ignore=tests/test_r2a_determinism_fill_cost.py` → 326 yeşil. (Yavaş chunklar opsiyonel; her biri ~30s.)
3. **Faz B0'dan başla**, plan'daki sırayla ilerle. Her task'tan sonra pytest.
4. Her faz sonu: commit + push + raporla (kaç test eklendi, hangi dosyalar değişti, sapma var mı).

## Spec özet (referans)

**Yeni dizin yapısı:**
```
smc_engine/integrations/_base.py            # ExchangeAdapter Protocol + SymbolMeta + Kline
smc_engine/integrations/binance/            # adapter, client, data, symbols (ws.py v2'ye ertelendi)
smc_engine/live/                            # runner, scheduler, account_state, signal_logger
examples/run_live.py                        # CLI
tests/test_binance_{client,adapter}.py      # mock-based unit tests
tests/test_{scheduler,live_runner,signal_logger}.py
```

**Akış (Spec §3):**
```
APScheduler M15:00 + 5sn → BinanceAdapter.fetch_ohlcv(D1, H4, M15)
  → orchestrator.analyze(at_bar=last_closed_M15, cache=htf_cache)
  → setup_builder.build → risk_guard.validate
  → signal_logger.emit → signals.jsonl + stdout
```

**Yeni dependencies (Spec §15):**
```toml
"python-binance>=1.0.21",
"apscheduler>=3.10",
"python-dotenv>=1.0",
```

**Konfig (Spec §6):** `live.symbols=[BTCUSDT, ETHUSDT, SOLUSDT]`, `live.scheduler_buffer_seconds=5`, `binance.rate_limit_buffer=0.8`.

**AccountState log-only mod'da (Spec §8):** statik `AccountState(equity=config.live.account_equity, open_position=False, ...)`. drawdown_breaker + averaging gate'leri pasif; diğer gate'ler tam çalışır.

**Signal logger format (Spec §9):** JSONL satır başına bir event (ts, symbol, timeframe, at_bar, kind=`validated_setup`/`rejection`, payload). Günlük rotasyon: `signals-YYYYMMDD.jsonl`.

## Test stratejisi (Spec §11)

| Test | Yöntem |
|---|---|
| BinanceClient | mock REST (requests-mock/unittest.mock), rate-limit + retry + error path |
| BinanceAdapter | mock client ile fetch_* fonksiyonları, ExchangeAdapter Protocol uyumu |
| LiveScheduler | fake-time tetikleme, cron ifadesi doğru |
| LiveRunner | FakeAdapter ile end-to-end mock, look-ahead garantisi, multi-symbol |
| SignalLogger | JSONL roundtrip, günlük rotasyon, stdout |
| Smoke (manuel) | Kullanıcı gerçek read-only key ile `run_live.py` çalıştırır, log incele |

**Gerçek API çağrısı yapan test YOK** — hepsi mock. Manuel smoke kullanıcının işi (Faz B4.2).

## Çıktı formatı (her faz sonu)

Plan'daki faz sonu commit mesajını at + kısa rapor:
- `pytest` özeti (kaç geçti/kaldı)
- Hangi dosyalar oluştu/değişti
- Hangi skill'leri kullandın
- Sapma/varsayım/açık nokta varsa açıkça

**Bittiğinde (Faz B5 sonu):** master raporun: toplam test, yeni dosya listesi, smoke test çıktısı, code-review bulguları/düzeltmeler, deploy verdict.

## Sonraki adımlar (#2 sonrası)

- **Birkaç gün canlı izleme:** `signals.jsonl` üzerinden sinyal kalitesi değerlendirmesi.
- **Eğer iyi:** sub-proje #5 (execution & risk yönetimi) — gerçek emir, mainnet guard, $100 canlı test.
- **Eğer iyileştirme gerekirse:** detektör/confluence ağırlıklarını ratchet ile optimize et (`smc_engine/integrations/tradingview/` ile config sync).
