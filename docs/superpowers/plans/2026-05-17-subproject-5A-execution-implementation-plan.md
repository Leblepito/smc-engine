# SMC Engine — Sub-proje #5A (Execution Walking Skeleton) İmplementasyon Planı

> **Tarih:** 2026-05-17 · **Spec:** `docs/superpowers/specs/2026-05-17-subproject-5-execution-design.md` (commit `2961101`)
> **Metodoloji:** writing-plans (faz'a bölünmüş bite-sized TDD task'lar, tam dosya yolları, doğrulama adımları)
> **Hedef ortam:** Claude Code (VSCode, lokal) — `C:\Users\utkuc\OneDrive\Masaüstü\smc-engine\`
> **Faz scope:** Sadece 5A walking skeleton. 5B production v1 ayrı plan'da, 5A bittikten + sinyal kalitesi onaylandıktan sonra.

## Nasıl okunmalı

Her task tek aksiyon (2-5 dk). Tahminî süre parantezde. TDD: önce başarısız test, sonra minimal impl, sonra yeşil. Her task'ın bir **doğrulama** adımı var. Faz sonunda commit + push.

**Bağımlılık sırası:** X0 → X1 → X2 → X3 → X4 → X5 → X6 → X7.

**Mevcut 401 test KIRILMAMALI.** Her faz sonu hızlı suite çalıştırılır.

Stack: Python 3.10+, python-binance, apscheduler, python-dotenv, pytest. Hiçbir yeni dependency YOK.

**Toplam tahmin:** ~6-7 saat agent çalışması, 7 commit + push, ~60 yeni test (toplam ~460 yeşil).

---

## Faz X0 — Proje hazırlığı (~30 dk)

**X0.1** (~3dk) `.env.example` güncelle:
```
# Mevcut:
BINANCE_API_KEY=
BINANCE_API_SECRET=
SMC_ALLOW_LIVE=0

# Yeni: yorum olarak ekle
# Mainnet için: 1 yap + config.execution.live_enabled=true + service restart
```
Doğrulama: `git status` `.env.example` track ediyor.

**X0.2** (~10dk) `smc_engine/config.py`'a `execution` config bölümü ekle (Spec §5'e göre):
- `execution.enabled` (bool, default False) — master flag
- `execution.phase` (str, default "5A")
- `execution.testnet` (bool, default True)
- `execution.live_enabled` (bool, default False) — MainnetGuard 2. katman
- `execution.risk_per_trade_dollar` (float, default 2.0)
- `execution.leverage` (int, default 10)
- `execution.margin_mode` (str, default "isolated")
- `execution.order_timeout_minutes` (int, default 60)
- `execution.kill_switch.consecutive_losses` (int, default 3)
- `execution.kill_switch.daily_loss_dollar` (float, default 5.0)
- `execution.kill_switch.equity_minimum` (float, default 15.0)
- `execution.fill_polling_seconds` (int, default 30)
- `execution.reconcile_loop_seconds` (int, default 300)
- `execution.audit_log_dir` (str, default "logs/trades")
- `execution.state_dir` (str, default "logs/state")
- `execution.symbols` (list[str], default ["BTCUSDT"])

Test: `tests/test_config.py`'a yeni alan default + YAML override testleri (~3 test).

**X0.3** (~5dk) Dizinleri oluştur:
- `smc_engine/execution/__init__.py` (boş)
- `smc_engine/integrations/binance/order_client.py` (boş skeleton)
- `tests/__init__.py` mevcut

**X0.4** (~8dk) **Test+impl:** `smc_engine/execution/_base.py` — `ExecutionAdapter` Protocol (ileride MT5 için), `OrderRequest` + `OrderResponse` + `Position` + `Account` + `SymbolMeta` dataclass'ları. Test: tipler örneklenebilir, Protocol type-check geçiyor (~3 test).

**X0.5** (~2dk) Doğrulama: `pytest tests/test_config.py tests/test_execution_base.py -v` yeşil. Mevcut suite kırılmadı.

**X0.6** Commit: `feat: subproject-5 hazırlığı (config execution, _base Protocol, types)` + push.

---

## Faz X1 — BinanceOrderClient (~50 dk)

### X1.1 Client skeleton (~10dk)

**X1.1.1** (~5dk) Test: `tests/test_binance_order_client.py` — `unittest.mock` ile python-binance.Client wrap testleri:
- Constructor: testnet/mainnet URL switching (testnet=False ise MainnetGuard.is_approved() ZORUNLU çağrı, mock'la False döndür → RuntimeError)
- Constructor: rate_limit_buffer parametresi geçer
**KIRMIZI**.

**X1.1.2** (~5dk) Impl: `smc_engine/integrations/binance/order_client.py` — `BinanceOrderClient` sınıfı. Constructor signature: `(api_key, api_secret, testnet, rate_limit_buffer=0.8)`. Mainnet için runtime assertion (MainnetGuard placeholder şimdilik — gerçek implementasyonu X3'te). **YEŞİL**.

### X1.2 Write endpoint'leri (~25dk)

**X1.2.1** (~10dk) Test: mock python-binance:
- `place_order(symbol, side, type, qty, price, stop_price=None, time_in_force="GTC")` → `OrderResponse`
- LIMIT, MARKET, STOP_MARKET tip testleri
- `cancel_order(symbol, order_id)` → success/fail
- `get_open_orders(symbol=None)` → list
- `get_order(symbol, order_id)` → status (NEW/FILLED/CANCELED/EXPIRED)
**KIRMIZI**.

**X1.2.2** (~12dk) Impl: yukarıdaki metodlar. python-binance `futures_create_order`, `futures_cancel_order`, `futures_get_open_orders`, `futures_get_order` wrap. **YEŞİL**.

**X1.2.3** (~3dk) Doğrulama: `pytest tests/test_binance_order_client.py -v` yeşil (~8 test).

### X1.3 Read endpoint'leri (~10dk)

**X1.3.1** Test+impl: `get_position(symbol)` → `Position`, `get_account()` → `Account`. python-binance `futures_position_information`, `futures_account`. **YEŞİL**.

### X1.4 Leverage + margin mode (~5dk)

**X1.4.1** Test+impl: `set_leverage(symbol, leverage)`, `set_margin_mode(symbol, mode)`. python-binance `futures_change_leverage`, `futures_change_margin_type`. Idempotent (zaten doğru ise no-op). **YEŞİL**.

### X1.5 Error handling (~10dk)

**X1.5.1** Test: Spec §12.1 mapping table'ı için unit testler:
- -1013 PRICE_FILTER → `BinanceOrderError(retryable=False, kill_switch_signal=True)`
- -2010 NEW_ORDER_REJECTED → `BinanceOrderError(retryable=False)`
- -2011 CANCEL_REJECTED → `BinanceOrderError(retryable=False, reconcile_needed=True)`
- -2019 MARGIN_INSUFFICIENT → `BinanceOrderError(retryable=False, kill_switch_signal=True)`
- -4131 PERCENT_PRICE → `BinanceOrderError(retryable=False)`
- 429 → exponential backoff (1s, 2s, 4s) sonra abort
- 5xx → exponential backoff
- Network timeout → 3 retry
**KIRMIZI**.

**X1.5.2** Impl: `_handle_error()` helper + exception hiyerarşisi. **YEŞİL**.

**X1.6** Commit: `feat: BinanceOrderClient REST + error handling` + push.

---

## Faz X2 — PositionTracker + position sizing (~45 dk)

### X2.1 Position sizing (~15dk)

**X2.1.1** (~8dk) Test: `tests/test_position_sizing.py` — `calc_position_size(risk_dollar, entry, sl, leverage, symbol_meta, account)`:
- Normal case: risk=$2, entry=78329, sl=77435 → ~0.00224 BTC
- Lot size rounding: raw=0.002247 → rounded=0.002 (lot_size=0.001)
- Min notional violation: tiny risk → raise `OrderSizeBelowMinimum`
- Insufficient margin: huge size → raise `InsufficientMargin` (80% buffer)
- SL == entry edge case → raise `InvalidStopLoss` (zero distance)
**KIRMIZI**.

**X2.1.2** (~7dk) Impl: `smc_engine/execution/position_sizing.py`. Decimal arithmetic (no float precision loss). **YEŞİL** (~6 test).

### X2.2 State machine (~20dk)

**X2.2.1** (~10dk) Test: `tests/test_position_tracker.py` — state machine geçişleri:
- PENDING → ACTIVE (on_fill)
- PENDING → ABORTED (on_timeout)
- PENDING → ABORTED (on_reject)
- ACTIVE → CLOSED_WIN (on_tp_hit)
- ACTIVE → CLOSED_LOSS (on_sl_hit)
- ACTIVE → CLOSED_MANUAL (on_manual_close)
- ACTIVE → CLOSED_DRIFT (on_drift)
- Invalid transitions → raise `IllegalStateTransition`
**KIRMIZI**.

**X2.2.2** (~10dk) Impl: `smc_engine/execution/position_tracker.py` — `PositionTracker` sınıfı + `PositionState` enum + `TrackedPosition` dataclass. **YEŞİL** (~12 test).

### X2.3 State persistence (~10dk)

**X2.3.1** Test: `positions-state.json` atomic write/read:
- save() → temp file + rename pattern
- load() → JSON parse + state reconstruction
- Corrupted file → raise + audit
- Schema version mismatch → migrate (5A için no-op)
**KIRMIZI**.

**X2.3.2** Impl: `save_state(path)`, `load_state(path)` metodları PositionTracker'a ekle. **YEŞİL** (~4 test).

**X2.4** Commit: `feat: PositionTracker state machine + persistence + position sizing` + push.

---

## Faz X3 — MainnetGuard + AuditLog (~40 dk)

### X3.1 MainnetGuard (~15dk)

**X3.1.1** (~8dk) Test: `tests/test_mainnet_guard.py` — 4 env+config kombinasyonu:
- env_yes + config_yes → MAINNET (5sn delay, warning log)
- env_yes + config_no → TESTNET (warning log)
- env_no + config_yes → TESTNET (info log)
- env_no + config_no → TESTNET (info log)
- `is_approved()` helper testler
- 5sn delay test (`time.sleep` monkeypatch)
**KIRMIZI**.

**X3.1.2** (~5dk) Impl: `smc_engine/execution/mainnet_guard.py` — `MainnetGuard` sınıfı (Spec §4.4 ve §11). **YEŞİL** (~5 test).

**X3.1.3** (~2dk) BinanceOrderClient X1.1.2'deki placeholder'ı gerçek MainnetGuard çağrısı ile değiştir. Mevcut test güncelle (X1.1.1).

### X3.2 AuditLog (~20dk)

**X3.2.1** (~10dk) Test: `tests/test_audit_log.py`:
- Event tipleri JSONL roundtrip (Spec §4.5 listesi, en az 5 örnek)
- Ortak field'lar her event'te (ts, event, phase, engine_sha, testnet)
- Günlük rotasyon (`trades-YYYYMMDD.jsonl`, tarih dönünce yeni dosya)
- Atomic line write (concurrent emit testten)
- log_dir yoksa otomatik oluştur
- `engine_sha` git rev-parse HEAD (subprocess) — mock'la
**KIRMIZI**.

**X3.2.2** (~10dk) Impl: `smc_engine/execution/audit_log.py` — `AuditLog` sınıfı. `_to_dict` helper (ValidatedSetup, Order, Position serialize). **YEŞİL** (~5 test).

**X3.3** Commit: `feat: MainnetGuard 3 katman + AuditLog JSONL` + push.

---

## Faz X4 — KillSwitch (~30 dk)

**X4.1** (~12dk) Test: `tests/test_kill_switch.py`:
- 3 ardışık loss → triggered (consecutive_losses metric)
- 2 loss + 1 win + 2 loss → NOT triggered (win-reset, consecutive=2)
- Daily PnL -$5 → triggered (daily_loss metric)
- Equity ≤ $15 → triggered (equity_minimum metric)
- Multiple triggers same time → all listed in reasons
- `is_triggered()` persistence (state'i diskten geri yükle)
- `reset()` manuel → state fresh, audit log
- `trigger_external(reason)` → drift için external trigger
**KIRMIZI**.

**X4.2** (~12dk) Impl: `smc_engine/execution/kill_switch.py` — `KillSwitch` + `KillSwitchState`. State persistence (`kill_switch_state.json`). **YEŞİL** (~8 test).

**X4.3** (~6dk) Script: `scripts/kill_switch_reset.sh` (bash) — Python module wrapper (`python -m smc_engine.execution.kill_switch_reset`). User-friendly çıktı (mevcut state göster + confirm prompt + reset + audit).

**X4.4** Commit: `feat: KillSwitch 3 metrik + persistence + manuel reset script` + push.

---

## Faz X5 — OrderManager + ReconcileLoop (~70 dk)

### X5.1 OrderManager core (~30dk)

**X5.1.1** (~12dk) Test: `tests/test_order_manager.py` — `FakeOrderClient` (deterministic responses) ile:
- `process_setup(setup)` happy path: kill_switch check, mainnet_guard check, position_size, place_order, audit, tracker.add
- Kill switch triggered → SETUP_SKIPPED_KILL_SWITCH audit, no order
- Mainnet guard fail → RuntimeError (boot already failed, defansif)
- Position sizing fail (OrderSizeBelowMinimum) → audit + skip
- Place order Binance reject (-2010) → audit ORDER_REJECTED, no tracker entry
- Place order rate limit (429) → retry 3x backoff, sonra abort
**KIRMIZI**.

**X5.1.2** (~15dk) Impl: `smc_engine/execution/order_manager.py` — `OrderManager` sınıfı, `process_setup` method. Spec §4.2 pseudo-code'a göre. **YEŞİL** (~10 test).

**X5.1.3** (~3dk) Doğrulama: pytest yeşil.

### X5.2 Timeout watcher + fill polling (~20dk)

**X5.2.1** (~10dk) Test:
- `tick_timeout_watcher()`: PENDING order timeout_at geçti → cancel + audit ORDER_TIMEOUT + tracker.mark_aborted
- `tick_timeout_watcher()`: PENDING order timeout_at gelmedi → noop
- `tick_fill_polling()`: PENDING order get_order=FILLED → _on_fill (SL+TP orders, tracker.mark_active, audit ORDER_FILLED)
- `tick_fill_polling()`: ACTIVE position get_position.qty=0 → check TP/SL hit (get_order) → on_tp_hit/on_sl_hit, kill_switch check, audit
- Partial fill: fill_qty < total → cancel rest, mark_active with kısmi qty, audit PARTIAL_FILL
**KIRMIZI**.

**X5.2.2** (~10dk) Impl: `tick_timeout_watcher`, `tick_fill_polling`, `_on_fill`, `_on_position_close` metodları. **YEŞİL** (~8 test).

### X5.3 ReconcileLoop (~20dk)

**X5.3.1** (~10dk) Test: `tests/test_reconcile.py` — FakeOrderClient ile drift simulation:
- Check 1: local PENDING [12345], binance_orders [] → drift, audit, kill_switch
- Check 2: local ACTIVE BTCUSDT qty=0.002, binance qty=0 → drift
- Check 3: binance order [12346] not in local → drift (manuel açılmış)
- Check 4: local qty=0.002, binance qty=0.005 → drift (qty mismatch)
- All match → no drift, no audit, no kill switch
**KIRMIZI**.

**X5.3.2** (~10dk) Impl: `smc_engine/execution/reconcile.py` — `ReconcileLoop.tick()`. Spec §4.7. **YEŞİL** (~6 test).

**X5.4** Commit: `feat: OrderManager + timeout watcher + fill polling + ReconcileLoop` + push.

---

## Faz X6 — Runner entegrasyonu + CLI + scripts (~40 dk)

### X6.1 Runner.py hook (~15dk)

**X6.1.1** (~8dk) Test: `tests/test_live_runner.py` (mevcut, genişlet) — execution hook entegrasyonu:
- `config.execution.enabled=false` → eski davranış (sadece signal_logger)
- `config.execution.enabled=true` → her validated_setup için order_manager.process_setup çağrılır
- Timeout watcher + fill polling + reconcile loop scheduler'a register edilir
- Restart recovery: boot()'ta position_tracker.load_state + recovery loop
**KIRMIZI**.

**X6.1.2** (~7dk) Impl: `smc_engine/live/runner.py` değişimi. Spec §6.1 akışı. **YEŞİL** (~6 test).

### X6.2 CLI (~10dk)

**X6.2.1** Test: `examples/run_live.py` argparse:
- `--execution-enabled` flag (default config'den)
- `--testnet` / `--mainnet` (sadece testnet default; mainnet için MainnetGuard 3 katman gerekir)
- `--equity` (display only, position sizing $ risk üzerinden)

**X6.2.2** Impl: `examples/run_live.py` değişimi.

### X6.3 Scripts (~15dk)

**X6.3.1** `scripts/reconcile_check.py` (~5dk): Spec §9.2. CLI: `--fix` flag interactive (a/b/c choice per drift).

**X6.3.2** `scripts/analyze_trades.py` (~5dk): Spec §13.1 pattern. `analyze_signals.py` baz alarak (sub-proje #2'de).

**X6.3.3** `scripts/analyze_combined.py` (~5dk): Spec §13.2. signals + trades join (signal_at_bar key).

**X6.4** Commit: `feat: runner execution hook + CLI flags + analyze scripts` + push.

---

## Faz X7 — Test + smoke + runbook (~50 dk)

### X7.1 Integration testler (~20dk)

**X7.1.1** (~10dk) `tests/test_execution_integration.py` — FakeOrderClient ile end-to-end:
- Signal → place → fill → TP_HIT → kill_switch check → audit (full happy path)
- Signal → place → timeout → abort
- Signal → place → reject → no tracker entry
- Restart recovery: state.json yaz, runner reload, recovery doğru

**X7.1.2** (~10dk) Daha integration test:
- Kill switch + restart: kill switch tetikle, runner restart, hala aktif
- Drift simulation: FakeBinance drift inject, reconcile detect, kill switch trigger
- 60dk timeout flow: zaman ilerlet (monkeypatch), cancel
- Multiple parallel signals: 3 farklı bar 3 farklı setup, hepsi sırayla process

### X7.2 Coverage verify (~5dk)

```bash
pytest --cov=smc_engine/execution --cov-report=term-missing tests/
```

Hedef: %90+ kritik modüllerde (mainnet_guard, kill_switch, position_tracker, order_manager). %90 altıysa missing line'ları test ekle.

### X7.3 Runbook (~15dk)

**X7.3.1** `docs/operations/EXECUTION_RUNBOOK.md` yaz:
- Service start/stop (systemd commands)
- Mainnet activation: 3 katman aktifleştirme adımları
- Testnet smoke: 2 günlük plan, Binance Futures Testnet API key
- Mainnet smoke: $20-25 plan, IP whitelist, izleme
- Kill switch tetiklendiğinde: SSH ile inceleme, reset prosedürü
- Reconcile drift: manuel inceleme, --fix flag kullanımı
- Daily monitoring: `analyze_trades.py`, `analyze_combined.py`
- 5B'ye geçiş kriterleri (Spec §16.2)

### X7.4 Engineering skill öz-inceleme (~5dk)

`engineering:code-review` skill ile öz-inceleme (sub-proje #2 pattern'ı):
- MainnetGuard 3 katman robust mu (env race condition, config reload)
- KillSwitch state persistence atomic mi (concurrent write)
- PositionTracker restart recovery edge case'leri (corrupted state, schema drift)
- ReconcileLoop drift detection complete mı (tüm 4 check)
- AuditLog event tipleri spec ile birebir mi

### X7.5 Final pytest + commit (~5dk)

**X7.5.1** `pytest tests/ -q` — mevcut 401 + yeni ~60 = ~461 yeşil.

**X7.5.2** Commit: `feat: subproject-5A v1 tamamlandı — execution walking skeleton (testnet ready)` + push.

---

## Toplam scope

| Faz | Süre | Yeni test sayısı | Ana dosyalar |
|---|---|---|---|
| X0 | 30 dk | ~6 | config.py, _base.py, dizinler |
| X1 | 50 dk | ~12 | binance/order_client.py |
| X2 | 45 dk | ~22 | execution/{position_tracker,position_sizing}.py |
| X3 | 40 dk | ~10 | execution/{mainnet_guard,audit_log}.py |
| X4 | 30 dk | ~8 | execution/kill_switch.py + script |
| X5 | 70 dk | ~24 | execution/{order_manager,reconcile}.py |
| X6 | 40 dk | ~6 (genişletme) | runner.py + CLI + scripts |
| X7 | 50 dk | ~8 (integration) | integration tests + runbook + review |
| **Toplam** | **~6 saat** | **~96** | **~12 yeni dosya** |

**Bütçe esnekliği:** Coverage ekleme, edge case bulma, review düzeltmesi için +%20 buffer (yani ~7 saat realistik).

## Bağımlılık & risk notları

- **MainnetGuard kritik:** X3.1.2 implementasyonu **kesinlikle test edilmeli**. 5A testnet smoke ÖNCESİ X3.1 testleri %100 yeşil olmalı.
- **Atomic write:** PositionTracker state.json + KillSwitch state.json **mutlaka** atomic (temp + rename), aksi halde crash sırasında corruption.
- **FakeOrderClient:** Bütün testler bunu kullanır. Implementasyonu deterministic olmalı (random fill price = bug).
- **No real API call:** Hiçbir test gerçek Binance'e gitmez. Manuel smoke kullanıcının işi.
- **Restart safety:** Her state geçişinde immediate write. Test: state yaz, process kill, restart, state restore.
- **5A scope locked:** Multi-TP partial close, breakeven SL, drawdown breaker, auto-fix reconcile, WebSocket BU PLAN'DA YOK. 5B'de.

## Smoke test prosedürü (X7 sonrası, kullanıcı yapacak)

### Aşama 1: Testnet (2 gün)

1. Binance Futures Testnet hesap aç: https://testnet.binancefuture.com/
2. API key üret (testnet için)
3. PC'de geçici `.env.testnet` dosyası:
   ```
   BINANCE_API_KEY=<testnet_key>
   BINANCE_API_SECRET=<testnet_secret>
   SMC_ALLOW_LIVE=0          # ÖNEMLI: 0 kalsın
   ```
4. scp ile VPS'e gönder + config'i güncelle:
   ```bash
   scp .env.testnet smc@94.130.148.21:~/smc-engine/.env.testnet
   ssh smc@94.130.148.21
   cd ~/smc-engine
   # config.yaml: execution.enabled=true, execution.testnet=true
   nano config.yaml
   # service restart
   sudo systemctl restart smc-engine
   ```
5. 2 gün izleme:
   - `tail -f logs/trades-*.jsonl`
   - ORDER_PLACED → ORDER_FILLED → TP_HIT / SL_HIT akışı çalışıyor mu
   - Reconcile drift = 0
   - `python scripts/analyze_trades.py --date $(date -u +%Y-%m-%d)` her sabah

### Aşama 2: Mainnet $20-25 (7 gün)

1. Binance mainnet → API → yeni key üret (read+trade, **withdraw KAPALI**)
2. IP whitelist: VPS IP `94.130.148.21`
3. PC'de `.env` güncelle (BINANCE keys + `SMC_ALLOW_LIVE=1`)
4. scp ile VPS'e (sub-proje #2'deki gibi)
5. config: `execution.testnet=false`, `execution.live_enabled=true`
6. Service restart → 5sn MAINNET WARNING gör, abort etme
7. 7 gün izleme: aynı analyze_trades.py akışı

### 5B'ye geçiş kararı (smoke sonrası)

Acceptance kriterleri (Spec §16.2):
- 10-12 trade tamamlandı
- Win rate ≥ %45
- Total PnL ≥ -$5
- Kill switch ≤ 1 tetikleme
- Reconcile drift = 0

Geçilirse → 5B spec yazımı (Cowork) → 5B plan (Cowork) → 5B implementation (Claude Code).
Geçilemezse → ratchet ile detector/confluence optimization (Cowork autoresearch skill).

## Sonraki sub-proje

Bu plan tamamlandıktan sonra:
- **5A testnet smoke + mainnet smoke** (~2 + 7 gün)
- Sinyal kalitesi onaylanırsa → **sub-proje #5B (production v1)** spec + plan + implementation
- Sinyal kalitesi onaylanmazsa → **ratchet optimization** (autoresearch)
