# SMC Engine — Sub-proje #5 (Execution & Risk Management) Tasarım Dokümanı

> **Tarih:** 2026-05-17
> **Bağımlılık:** Sub-proje #2 (Binance live signal pipeline) tamamlanmış, VPS'te çalışıyor
> **Hedef:** smc-engine'in `signals.jsonl` ürettiği validated_setup'larını gerçek emire dönüştürmek
> **Yaklaşım:** 2 faz (5A walking skeleton → 5B production v1)

---

## 1. Bağlam ve Amaç

Sub-proje #2 ile smc-engine her M15 kapanışında Binance USDT-M futures verisini alıp orchestrator → setup_builder → risk_guard akışını çalıştırıyor, sonuçları `signals.jsonl`'a yazıyor. Şu an **log-only** mod — gerçek emir yok.

Sub-proje #5'in amacı: bu pipeline'a **execution layer** eklemek. Validated_setup'lar gerçek limit order'a dönüşür, position takip edilir, SL/TP tetiklendiğinde state güncellenir, audit log tutulur.

**Faz ayrımı:**

- **5A — Walking skeleton** (~5-6 gün impl + 2 gün testnet + ~7 gün mainnet smoke)
  - Bütçe: $20-25 mainnet
  - Symbol: BTCUSDT only
  - Hedef: 10-12 trade, emir gönderme pipeline'ı kanıtı
  - Single TP/SL, manuel reconcile + detect-only auto reconcile
  
- **5B — Production v1** (~10 gün impl, sonra ~30 gün canlı)
  - Bütçe: $100
  - Symbol: BTC + ETH + SOL + AVAX + LINK (5 coin)
  - Hedef: 50+ trade, production-grade
  - Multi-TP partial close, breakeven SL, drawdown breaker, auto reconcile + fix, WebSocket

**Bu spec sub-proje 5A'nın detaylı implementation rehberi; 5B için yön gösterici çerçeve.** 5A bittikten + sinyal kalitesi onaylandıktan sonra 5B için ayrı spec yazılır.

## 2. Kapsam

### Dahil

- BinanceOrderClient (REST write endpoint'leri: place/cancel/get_orders/get_position)
- OrderManager (sinyal → limit order, timeout watcher)
- PositionTracker (state machine: PENDING → ACTIVE → CLOSED)
- MainnetGuard (3 katmanlı doğrulama: env + config + startup delay)
- AuditLog (`trades-YYYYMMDD.jsonl` günlük rotasyon)
- KillSwitch (3 metrik: ardışık loss, daily loss, equity minimum)
- ReconcileLoop (5dk auto-detect, drift → kill switch; manuel script with --fix)
- Configuration extensions (`config.execution.*` bölümü)
- runner.py içinde execution hook
- Restart recovery (`positions-state.json`)
- `analyze_trades.py` script (sub-proje #2'deki analyze_signals.py muadili)

### Dahil değil (5B'ye ertelendi)

- Multi-TP partial close (TP1/TP2/TP3 ayrı ayrı)
- Breakeven SL (TP1 hit sonrası SL → entry)
- Drawdown breaker (günlük/haftalık/global %)
- Auto-fix reconciliation
- WebSocket user data stream
- Multi-symbol concurrent positions
- Symbol-specific config (BTC vs altcoin)
- Retry queue / dead-letter
- ExchangeAdapter'ı forex'e genişletme (sub-proje #2.5)

## 3. Mimari

### 3.1 Konum

`smc-engine` repo'su, **self-contained** (efloud-bot ile entegre değil). Hetzner VPS nbg1 üzerinde, sub-proje #2 ile **aynı runner içinde**. Ayrı service yok. `live/runner.py` execution layer'a köprü kurar.

### 3.2 Repo yapısı (yeni)

```
smc_engine/
├── execution/                     [YENI — 5A]
│   ├── __init__.py
│   ├── _base.py                   (ExecutionAdapter Protocol — ileride MT5 için)
│   ├── order_manager.py           (sinyal → emir akışı)
│   ├── position_tracker.py        (state machine + state persistence)
│   ├── mainnet_guard.py           (3 katman doğrulama)
│   ├── audit_log.py               (trades.jsonl writer)
│   ├── kill_switch.py             (5A: 3 metrik)
│   ├── reconcile.py               (5A: detect-only loop + manuel script)
│   └── position_sizing.py         (calc_position_size helper)
├── integrations/
│   └── binance/
│       └── order_client.py        [YENI — REST order endpoint'leri]
├── live/
│   └── runner.py                  [DEĞİŞİM — execution hook]
├── config.py                      [DEĞİŞİM — execution config alanları]

examples/
└── run_live.py                    [DEĞİŞİM — execution flag]

scripts/
├── analyze_trades.py              [YENI — trades.jsonl analizi]
├── analyze_combined.py            [YENI — signals + trades join]
├── reconcile_check.py             [YENI — manuel reconcile]
└── kill_switch_reset.sh           [YENI — manuel reset]

tests/
├── test_order_manager.py
├── test_position_tracker.py
├── test_mainnet_guard.py
├── test_audit_log.py
├── test_kill_switch.py
├── test_reconcile.py
├── test_binance_order_client.py
├── test_position_sizing.py
└── test_execution_integration.py  (FakeOrderClient end-to-end)
```

## 4. Bileşenler

### 4.1 `BinanceOrderClient`

`integrations/binance/order_client.py`. `BinanceClient`'a (read-only, sub-proje #2'den) paralel ama write endpoint'leri:

| Metod | Binance endpoint | Açıklama |
|---|---|---|
| `place_order(symbol, side, type, qty, price, stop_price=None, time_in_force="GTC")` | `POST /fapi/v1/order` | Limit, Market, Stop, StopLimit |
| `cancel_order(symbol, order_id)` | `DELETE /fapi/v1/order` | Tek order cancel |
| `get_open_orders(symbol=None)` | `GET /fapi/v1/openOrders` | Hepsi veya tek symbol |
| `get_order(symbol, order_id)` | `GET /fapi/v1/order` | Tek order detayı (status, fill_qty, fill_price) |
| `get_position(symbol)` | `GET /fapi/v2/positionRisk` | Position qty, entry, unrealized PnL, liquidation price |
| `get_account()` | `GET /fapi/v2/account` | Equity, available margin, isolated/cross margin |
| `set_leverage(symbol, leverage)` | `POST /fapi/v1/leverage` | Sembol başına leverage |
| `set_margin_mode(symbol, mode="ISOLATED")` | `POST /fapi/v1/marginType` | Isolated zorunlu (5A) |

**Constructor:**
```python
BinanceOrderClient(
    api_key: str,
    api_secret: str,
    testnet: bool,           # MainnetGuard'tan geçmiş olmalı
    rate_limit_buffer: float = 0.8,  # config'den
)
```

**Mainnet/testnet URL switching:**
- testnet=True → `https://testnet.binancefuture.com`
- testnet=False → `https://fapi.binance.com` (MainnetGuard onayı şart)

Constructor'da assertion: `testnet=False` ise `MainnetGuard.is_approved()` çağrısı, false dönerse `RuntimeError("Mainnet not approved")`.

### 4.2 `OrderManager`

`execution/order_manager.py`. Sinyali emire çevirme akışını yöneten central bileşen:

```python
class OrderManager:
    def __init__(self,
                 order_client: BinanceOrderClient,
                 position_tracker: PositionTracker,
                 audit_log: AuditLog,
                 kill_switch: KillSwitch,
                 config: SMCConfig):
        ...
    
    def process_setup(self, setup: ValidatedSetup, at_bar: datetime) -> ProcessResult:
        # 1. Kill switch kontrol
        if self.kill_switch.is_triggered():
            self.audit_log.emit("SETUP_SKIPPED_KILL_SWITCH", setup, ...)
            return ProcessResult.SKIPPED
        
        # 2. Mainnet guard kontrol (her process'te değil, init'te ama defansif)
        self.mainnet_guard.assert_approved()
        
        # 3. Position sizing
        size = calc_position_size(
            risk_dollar=self.config.execution.risk_per_trade_dollar,
            entry=setup.entry,
            sl=setup.sl,
            leverage=self.config.execution.leverage,
            symbol_meta=self.order_client.get_symbol_meta(setup.symbol),
            account=self.order_client.get_account(),
        )
        
        # 4. Order place
        order = self.order_client.place_order(
            symbol=setup.symbol,
            side="BUY" if setup.direction == "LONG" else "SELL",
            type="LIMIT",
            qty=size,
            price=setup.entry,
            time_in_force="GTC",
        )
        
        # 5. Audit + state
        self.audit_log.emit("ORDER_PLACED", order, setup, ...)
        self.position_tracker.add(
            order_id=order.id,
            setup=setup,
            placed_at=at_bar,
            timeout_at=at_bar + timedelta(minutes=60),
        )
        
        return ProcessResult.PLACED
    
    def tick_timeout_watcher(self):
        # APScheduler'dan her 30sn'de bir çağrılır
        for pending in self.position_tracker.pending():
            if datetime.utcnow() > pending.timeout_at:
                self.order_client.cancel_order(pending.symbol, pending.order_id)
                self.audit_log.emit("ORDER_TIMEOUT", pending, ...)
                self.position_tracker.mark_aborted(pending.order_id, reason="TIMEOUT")
    
    def tick_fill_polling(self):
        # APScheduler'dan her 30sn'de bir çağrılır
        for pending in self.position_tracker.pending():
            order_status = self.order_client.get_order(pending.symbol, pending.order_id)
            if order_status.status == "FILLED":
                self._on_fill(pending, order_status)
        
        for active in self.position_tracker.active():
            position = self.order_client.get_position(active.symbol)
            if position.qty == 0:
                self._on_position_close(active)
    
    def _on_fill(self, pending, order_status):
        # Fill geldi → SL ve TP order'larını koy
        sl_order = self.order_client.place_order(
            symbol=pending.symbol,
            side="SELL" if pending.side == "BUY" else "BUY",
            type="STOP_MARKET",
            qty=pending.qty,
            stop_price=pending.sl,
        )
        tp_order = self.order_client.place_order(
            symbol=pending.symbol,
            side="SELL" if pending.side == "BUY" else "BUY",
            type="LIMIT",
            qty=pending.qty,
            price=pending.tp,
            time_in_force="GTC",
        )
        self.position_tracker.mark_active(
            pending.order_id, sl_order_id=sl_order.id, tp_order_id=tp_order.id,
            fill_price=order_status.fill_price, fill_qty=order_status.fill_qty,
        )
        self.audit_log.emit("ORDER_FILLED", pending, order_status, ...)
```

### 4.3 `PositionTracker`

`execution/position_tracker.py`. State machine + state persistence (`positions-state.json`).

**State enum:**

```python
class PositionState(Enum):
    PENDING  = "PENDING"   # Limit order placed, fill bekliyor
    ACTIVE   = "ACTIVE"    # Filled, SL+TP order'ları aktif
    CLOSED   = "CLOSED"    # SL veya TP hit, position kapalı
    ABORTED  = "ABORTED"   # Timeout, reject, veya manuel cancel
```

**Geçişler (5A):**

```
PENDING
  ├─ on_fill          → ACTIVE   (SL+TP konuldu)
  ├─ on_timeout       → ABORTED  (60dk, cancel edildi)
  └─ on_reject        → ABORTED  (Binance reddetti)

ACTIVE
  ├─ on_tp_hit        → CLOSED   (kazanç, audit pnl>0)
  ├─ on_sl_hit        → CLOSED   (kayıp, audit pnl<0, kill switch counter++)
  ├─ on_manual_close  → CLOSED   (reconcile yakalar, audit pnl=actual)
  └─ on_drift         → CLOSED   (reconcile drift, kill switch tetikle)
```

**State persistence:**

```json
// positions-state.json (file write atomic — temp file + rename)
{
  "version": 1,
  "saved_at": "2026-05-17T03:18:22Z",
  "engine_sha": "abc123",
  "positions": [
    {
      "order_id": "12345",
      "state": "ACTIVE",
      "symbol": "BTCUSDT",
      "side": "BUY",
      "qty": 0.00224,
      "entry": 78327.50,
      "sl": 77435.51,
      "tp": 79670.00,
      "sl_order_id": "12346",
      "tp_order_id": "12347",
      "placed_at": "2026-05-17T03:15:05Z",
      "timeout_at": "2026-05-17T04:15:05Z",
      "filled_at": "2026-05-17T03:18:22Z",
      "signal_at_bar": "2026-05-17T03:15:00Z",
      "risk_dollar": 2.0,
      "leverage": 10
    }
  ]
}
```

**Write strategy:** Her state geçişinde immediate write (atomic rename pattern). Restart-safe.

### 4.4 `MainnetGuard`

`execution/mainnet_guard.py`. 3 katmanlı doğrulama:

```python
class MainnetGuard:
    @staticmethod
    def check(config: SMCConfig) -> Literal["TESTNET", "MAINNET", "DENIED"]:
        # Layer 1: Env var
        env_allow = os.environ.get("SMC_ALLOW_LIVE") == "1"
        if not env_allow:
            logger.info("MainnetGuard: SMC_ALLOW_LIVE env var not set, forcing TESTNET")
            return "TESTNET"
        
        # Layer 2: Config flag
        config_live = config.execution.live_enabled
        if not config_live:
            logger.warning("MainnetGuard: SMC_ALLOW_LIVE=1 but config.execution.live_enabled=false, forcing TESTNET")
            return "TESTNET"
        
        # Layer 3: Startup delay + warning
        logger.critical("⚠️" * 20)
        logger.critical("⚠️  MAINNET ACTIVE — REAL MONEY  ⚠️")
        logger.critical("⚠️" * 20)
        logger.critical(f"Starting in 5 seconds. Ctrl+C to abort.")
        time.sleep(5)
        return "MAINNET"
    
    @staticmethod
    def is_approved() -> bool:
        return MainnetGuard.check(get_config()) == "MAINNET"
```

**Pattern:** efloud-bot'un `EFLOUD_ALLOW_MAINNET` benzeri ama isim farklı (`SMC_ALLOW_LIVE`). İki bot aynı VPS'te koşarsa env namespace çakışmaz.

**Test:** 4 kombinasyon (env yes/no × config yes/no) — sadece (yes,yes) MAINNET, diğerleri TESTNET.

### 4.5 `AuditLog`

`execution/audit_log.py`. `trades-YYYYMMDD.jsonl` günlük rotasyon, append-only.

**Event tipleri:**

| Event | Tetikleyici |
|---|---|
| `ORDER_PLACED` | OrderManager.process_setup başarılı |
| `ORDER_FILLED` | Fill polling FILLED status |
| `ORDER_PARTIAL_FILL` | Fill polling fill_qty < total_qty |
| `ORDER_TIMEOUT` | Timeout watcher 60dk doldu |
| `ORDER_REJECTED` | Binance API 4xx error |
| `ORDER_RETRY` | 429/5xx exponential backoff |
| `TP_HIT` | Polling ACTIVE position'ın TP order'ı filled |
| `SL_HIT` | Polling ACTIVE position'ın SL order'ı filled |
| `MANUAL_CLOSE` | Reconcile: ACTIVE state'te Binance position yok |
| `RECONCILE_DRIFT` | Reconcile loop discrepancy buldu |
| `KILL_SWITCH_TRIGGERED` | KillSwitch.check() true döndü |
| `KILL_SWITCH_RESET` | kill_switch_reset.sh çalıştırıldı |
| `SETUP_SKIPPED_KILL_SWITCH` | Kill switch aktif, yeni signal reject |
| `RECOVERY_COMPLETE` | Restart sonrası state restore bitti |

**Format ortak field'lar:**
```json
{
  "ts": "2026-05-17T03:15:05.123Z",
  "event": "ORDER_PLACED",
  "phase": "5A",
  "engine_sha": "abc123",
  "testnet": false,
  ...event-specific fields
}
```

**Event-specific examples** (bkz. Sektör 4.C tasarım dokümanı).

### 4.6 `KillSwitch`

`execution/kill_switch.py`. 5A — 3 metrik, **whichever fires first**:

```python
class KillSwitch:
    def __init__(self, config: SMCConfig, audit_log: AuditLog):
        self.consecutive_loss_threshold = config.execution.kill_switch.consecutive_losses  # 3
        self.daily_loss_threshold = config.execution.kill_switch.daily_loss_dollar         # 5.0
        self.equity_minimum = config.execution.kill_switch.equity_minimum                  # 15.0
        self._state = self._load_state()  # persistence: kill_switch_state.json
    
    def check_after_trade(self, trade_result: TradeResult, account: Account) -> bool:
        """Her TP_HIT / SL_HIT / MANUAL_CLOSE sonrası çağrılır."""
        if trade_result.is_loss:
            self._state.consecutive_losses += 1
        else:
            self._state.consecutive_losses = 0  # Win → reset
        
        self._state.daily_pnl += trade_result.pnl_dollar
        self._save_state()
        
        # 3 metrik kontrolü
        triggers = []
        if self._state.consecutive_losses >= self.consecutive_loss_threshold:
            triggers.append(f"consecutive_losses={self._state.consecutive_losses}")
        if self._state.daily_pnl <= -self.daily_loss_threshold:
            triggers.append(f"daily_pnl={self._state.daily_pnl:.2f}")
        if account.equity <= self.equity_minimum:
            triggers.append(f"equity={account.equity:.2f}")
        
        if triggers:
            self._state.triggered = True
            self._state.triggered_at = datetime.utcnow()
            self._state.triggered_reasons = triggers
            self._save_state()
            self.audit_log.emit("KILL_SWITCH_TRIGGERED", reasons=triggers, ...)
            return True
        return False
    
    def is_triggered(self) -> bool:
        return self._state.triggered
    
    def reset(self):
        """Manuel — kill_switch_reset.sh script'i çağırır."""
        old = self._state.triggered
        self._state = KillSwitchState()  # Fresh
        self._save_state()
        if old:
            self.audit_log.emit("KILL_SWITCH_RESET", ...)
```

**Drift trigger:** ReconcileLoop drift bulursa `KillSwitch.trigger_external(reason="RECONCILE_DRIFT", details=...)` çağırır. Manuel reset şart.

### 4.7 `ReconcileLoop`

`execution/reconcile.py`. **5A: detect-only** (auto-fix YOK).

```python
class ReconcileLoop:
    def __init__(self, order_client, position_tracker, audit_log, kill_switch, config):
        self.interval_seconds = config.execution.reconcile_loop_seconds  # 300 (5dk)
    
    def tick(self):
        """APScheduler'dan her 5dk çağrılır."""
        local_pending = self.position_tracker.pending()
        local_active = self.position_tracker.active()
        binance_orders = self.order_client.get_open_orders()
        binance_positions = self._get_all_positions()
        
        drifts = []
        
        # Check 1: Local PENDING var ama Binance'te order yok
        for p in local_pending:
            if not any(o.order_id == p.order_id for o in binance_orders):
                drifts.append(f"PENDING {p.order_id} not in Binance")
        
        # Check 2: Local ACTIVE var ama Binance'te position yok
        for a in local_active:
            pos = next((bp for bp in binance_positions if bp.symbol == a.symbol), None)
            if not pos or pos.qty == 0:
                drifts.append(f"ACTIVE {a.symbol} position={a.qty}, Binance has none")
        
        # Check 3: Binance'te order var ama local'de yok (manuel açtın mı?)
        local_order_ids = {p.order_id for p in local_pending} | {a.sl_order_id for a in local_active} | {a.tp_order_id for a in local_active}
        for o in binance_orders:
            if o.order_id not in local_order_ids:
                drifts.append(f"Binance order {o.order_id} ({o.symbol}) not in local state — manuel açıldı?")
        
        # Check 4: Position qty mismatch
        for a in local_active:
            pos = next((bp for bp in binance_positions if bp.symbol == a.symbol), None)
            if pos and pos.qty != a.qty:
                drifts.append(f"ACTIVE {a.symbol} local_qty={a.qty} != binance_qty={pos.qty}")
        
        if drifts:
            self.audit_log.emit("RECONCILE_DRIFT", drifts=drifts, ...)
            self.kill_switch.trigger_external(reason="RECONCILE_DRIFT", details=drifts)
```

**Manuel script: `scripts/reconcile_check.py`** — aynı logic ama interactive (--fix flag opsiyonu, sadece manuel müdahale için).

## 5. Konfigürasyon

`config.yaml`'da yeni `execution` bölümü:

```yaml
execution:
  enabled: false                # MASTER FLAG. Default false. Açmak için explicit.
  phase: "5A"                   # "5A" veya "5B"
  testnet: true                 # MainnetGuard ile birlikte 3 katmanın 2.'si
  live_enabled: false           # MainnetGuard 2. katman. testnet:true ise bu ignored
  
  # 5A sabit değerler (5B'de override edilebilir)
  risk_per_trade_dollar: 2.0
  leverage: 10
  margin_mode: "isolated"
  order_timeout_minutes: 60
  
  # Kill switch eşikleri
  kill_switch:
    consecutive_losses: 3
    daily_loss_dollar: 5.0
    equity_minimum: 15.0
  
  # Polling
  fill_polling_seconds: 30
  reconcile_loop_seconds: 300   # 5dk
  
  # Audit
  audit_log_dir: "logs/trades"
  state_dir: "logs/state"       # positions-state.json, kill_switch_state.json
  
  # 5A için tek symbol
  symbols: ["BTCUSDT"]          # 5B'de ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "LINKUSDT"]
```

**`.env` ek:**
```
SMC_ALLOW_LIVE=0    # 1 = mainnet allowed (MainnetGuard layer 1)
```

## 6. Veri Akışı

### 6.1 Normal happy path (LONG setup)

```
M15 kapanış @ 03:15:00 UTC
  └─ scheduler.tick @ 03:15:05
       └─ orchestrator.analyze → ValidatedSetup(BTCUSDT LONG entry=78329 sl=77435 tp=79670)
       └─ signal_logger.emit (signals.jsonl)
       └─ order_manager.process_setup
            ├─ kill_switch.is_triggered() → False
            ├─ mainnet_guard.assert_approved() → MAINNET (3 katman geçti)
            ├─ size = 0.00224 BTC (2.0 / 893 distance)
            ├─ order = binance_client.place_order(LIMIT, BUY, 0.00224, 78329)
            │     → order_id=12345
            ├─ audit_log.emit("ORDER_PLACED", ...)
            └─ position_tracker.add(12345, PENDING, timeout=04:15:05)

[3 dakika sonra...]
03:18:22 UTC fill_polling.tick
  └─ get_order(BTCUSDT, 12345) → FILLED @ 78327.50
       ├─ order_manager._on_fill
       │     ├─ sl_order = place_order(STOP_MARKET, SELL, 0.00224, stop=77435)
       │     ├─ tp_order = place_order(LIMIT, SELL, 0.00224, 79670)
       │     ├─ position_tracker.mark_active(12345, sl=12346, tp=12347, ...)
       │     └─ audit_log.emit("ORDER_FILLED", slippage=-1.80, fill_latency_ms=197235)

[~2.5 saat sonra...]
05:42:11 UTC fill_polling.tick
  └─ get_position(BTCUSDT) → qty=0 (TP hit, position kapandı)
       ├─ get_order(12347) → FILLED @ 79670 (TP)
       ├─ position_tracker.on_tp_hit(12345, exit=79670, pnl=+3.01)
       ├─ kill_switch.check_after_trade(WIN +3.01) → consecutive_losses=0 (reset)
       └─ audit_log.emit("TP_HIT", pnl_dollar=3.01, duration_minutes=147, actual_rr=1.50)
```

### 6.2 Timeout path

```
03:15:05 ORDER_PLACED (limit @ 78329)
[fiyat asla 78329'a inmedi...]
04:15:05 timeout_watcher.tick
  ├─ binance_client.cancel_order(12345)
  ├─ audit_log.emit("ORDER_TIMEOUT", duration_minutes=60)
  └─ position_tracker.mark_aborted(12345, reason="TIMEOUT")
```

### 6.3 Reject path

```
03:15:05 process_setup
  └─ place_order → Binance 400 -4131 PERCENT_PRICE (limit çok uzak spot'tan)
       ├─ audit_log.emit("ORDER_REJECTED", error_code=-4131, ...)
       └─ position_tracker NOT created (zaten hiç başlamadı)
       └─ NOTE: kill_switch.check_after_trade ÇAĞIRILMAZ (trade yoktu)
```

### 6.4 Restart recovery

```
[VPS restart, systemd auto-restart runner]
runner.boot()
  ├─ signal_logger.init()
  ├─ IF config.execution.enabled:
  │     ├─ mainnet_guard.check() → MAINNET (env+config still set)
  │     ├─ position_tracker.load_state("positions-state.json")
  │     │     └─ 1 PENDING (12345), 1 ACTIVE (12340)
  │     ├─ FOR pending 12345:
  │     │     status = get_order → FILLED
  │     │     → trigger _on_fill (SL+TP order'ları koy)
  │     │     → audit RECOVERY_PENDING_FILLED
  │     ├─ FOR active 12340:
  │     │     pos = get_position → qty=0
  │     │     get_order(tp_order_id) → FILLED
  │     │     → on_tp_hit, kill_switch.check_after_trade
  │     │     → audit RECOVERY_ACTIVE_CLOSED
  │     └─ audit_log.emit("RECOVERY_COMPLETE", drift_count=0, ...)
```

### 6.5 Reconcile drift path

```
[10:00 UTC reconcile_loop.tick]
  ├─ local_pending = [12345]
  ├─ binance_orders = []  ← drift!
  ├─ drifts = ["PENDING 12345 not in Binance"]
  ├─ audit_log.emit("RECONCILE_DRIFT", drifts=[...])
  └─ kill_switch.trigger_external(reason="RECONCILE_DRIFT", details=[...])
       → kill_switch.is_triggered() = True
       → next process_setup → SETUP_SKIPPED_KILL_SWITCH
[Kullanıcı SSH ile inceler:
  ssh smc@<IP> ".venv/bin/python -m smc_engine.execution.reconcile_check"
  Drift analiz, manuel fix veya state silme
  ssh smc@<IP> "./scripts/kill_switch_reset.sh"]
```

## 7. State Machine

PositionTracker tutar (bkz. 4.3).

```
       ┌──────────┐
       │ PENDING  │
       └────┬─────┘
            │
   ┌────────┼────────┐
   │        │        │
   v        v        v
┌─────┐ ┌────────┐ ┌──────────┐
│ACTIVE│ │ABORTED │ │ABORTED  │
│      │ │TIMEOUT │ │REJECTED │
└──┬──┘ └────────┘ └──────────┘
   │
   ├──── TP_HIT       → CLOSED_WIN
   ├──── SL_HIT       → CLOSED_LOSS
   ├──── MANUAL_CLOSE → CLOSED_MANUAL
   └──── DRIFT        → CLOSED_DRIFT (+ kill_switch trigger)
```

## 8. Restart Recovery

`runner.boot()` içinde execution-enabled ise:

1. `mainnet_guard.check()` (yine 3 katman doğrulama)
2. `position_tracker.load_state()`
3. Her PENDING için `get_order()` ile Binance state çek
   - FILLED → `_on_fill` (SL/TP koy)
   - CANCELED/EXPIRED → mark aborted
   - NEW → hala pending, dokunma
4. Her ACTIVE için `get_position()`
   - qty == 0 → TP/SL hit veya manuel close, audit + close
   - qty != local → drift, audit + kill switch
5. `audit_log.emit("RECOVERY_COMPLETE", ...)`

Restart-safe: her state geçişinde immediate atomic write.

## 9. Reconciliation

### 9.1 Auto detect (5A)

`ReconcileLoop` (bkz. 4.7) — 5dk'da bir, drift bulursa kill switch tetikler. Auto-fix YOK.

### 9.2 Manuel script

`scripts/reconcile_check.py`:

```bash
ssh smc@<IP> ".venv/bin/python -m smc_engine.execution.reconcile_check"
ssh smc@<IP> ".venv/bin/python -m smc_engine.execution.reconcile_check --fix"  # interactive
```

`--fix` ile her drift için kullanıcıya seçenek sunar:
- (a) Local state'i Binance ile sync et
- (b) Binance'ten yapılan değişikliği ignore et (local doğru)
- (c) Skip (manuel inceleyeceğim)

### 9.3 5B teaser

Auto-fix loop, drift tipine göre otomatik aksiyon. Sadece "safe" drift'ler için (örn. PENDING→CANCELED). Risky drift'ler (qty mismatch) için yine kill switch.

## 10. Risk Yönetimi

### 10.1 Position sizing (5A)

```python
def calc_position_size(risk_dollar, entry, sl, leverage, symbol_meta, account):
    sl_distance = abs(entry - sl)
    raw_size = risk_dollar / sl_distance
    rounded_size = round_to_lot(raw_size, symbol_meta.lot_size)
    
    notional = rounded_size * entry
    if notional < symbol_meta.min_notional:
        raise OrderSizeBelowMinimum(notional, symbol_meta.min_notional)
    
    margin = notional / leverage
    if margin > account.available_margin * 0.8:
        raise InsufficientMargin(margin, account.available_margin)
    
    return rounded_size
```

### 10.2 Liquidation buffer

10x isolated margin BTC entry 78329 → liquidation ≈ 70850 (~9.5% düşüş). SL ~1.1% — liquidation SL'den 8.5x daha uzak. Güvenli.

### 10.3 Kill switch

3 metrik, **whichever fires first** (bkz. 4.6):
- 3 ardışık loss
- Daily loss ≥ $5
- Equity ≤ $15

### 10.4 5B drawdown breaker (teaser)

- Daily: %5 equity drop → durdur 24 saat
- Weekly: %10 equity drop → durdur 7 gün
- Global: %20 equity drop → durdur, manuel inceleme

## 11. Mainnet Guard

3 katmanlı (bkz. 4.4):

1. **Env var:** `SMC_ALLOW_LIVE=1`
2. **Config flag:** `config.execution.live_enabled = true`
3. **Startup delay + warning:** 5sn boyunca terminal'e büyük WARNING

Hepsi geçmezse **TESTNET** zorlanır. URL otomatik switching.

## 12. Error Handling

### 12.1 Order rejection mapping

| Code | Anlam | Aksiyon |
|---|---|---|
| -1013 (PRICE_FILTER) | Tick size violation | Bug — kill switch, retry yok |
| -2010 (NEW_ORDER_REJECTED) | Generic reject | Audit + abort |
| -2011 (CANCEL_REJECTED) | Order zaten executed | Reconcile, normal flow |
| -2019 (MARGIN_INSUFFICIENT) | Margin yetmedi | Kill switch tetikle |
| -4131 (PERCENT_PRICE) | Limit price spot'tan uzak | Audit + abort, signal kalitesi sorgulanmalı |
| 429 (RATE_LIMIT) | Rate limit aşıldı | Exponential backoff 3x |
| 5xx (server error) | Binance side problem | Exponential backoff 3x |
| Network timeout | Bağlantı problemi | Retry 3x, sonra audit + abort |

### 12.2 Partial fill (nadir, 5A)

Fill < %100:
- Cancel kalan kısmı
- Kısmi position ACTIVE (SL/TP kısmi qty ile)
- Audit `ORDER_PARTIAL_FILL`

### 12.3 Network kesintisi

- 30sn polling timeout
- Exponential backoff (1s, 2s, 4s)
- Hala başarısız → audit + skip bu tick
- Reconcile loop sonra catch up

## 13. Audit Log

`trades-YYYYMMDD.jsonl` günlük rotasyon, append-only, atomic line write.

**Event tipleri:** bkz. 4.5
**Format:** bkz. 4.5
**Analiz:** `scripts/analyze_trades.py` (5A sonunda, `analyze_signals.py` pattern'i)

### 13.1 `analyze_trades.py` outline

```bash
python scripts/analyze_trades.py --date 2026-05-18

═══════════════════════════════════════════
 SMC Engine — Trade Report
 Period: 2026-05-18 (1 day, 5A phase)
═══════════════════════════════════════════

OVERALL
  Orders placed:        8
  Filled:               6  (75.0%)
  Timeouts:             1
  Rejects:              1
  
  Closed positions:     5  (1 still active)
  Wins:                 3  (60.0%)
  Losses:               2  (40.0%)

PNL
  Total PnL:           +$1.85
  Avg win:             +$2.12
  Avg loss:            -$1.98
  R-multiple avg:      +0.42
  Equity after:        $26.85 (started $25.00)

LATENCY
  Avg signal→place:    150ms
  Avg place→fill:      45.2 min
  Avg slippage:        -$0.85 (negative = better than signal)

KILL SWITCH
  Triggered: NO
  Consecutive losses: 1 (last trade was loss)
  Daily PnL: +$1.85
═══════════════════════════════════════════
```

### 13.2 `analyze_combined.py` outline

signals.jsonl ↔ trades.jsonl join (signal_at_bar key):

```
Setup at 2026-05-18 03:15 UTC (BTCUSDT LONG, conf=0.80, factors=4)
  → ORDER_PLACED 03:15:05 (12345)
  → ORDER_FILLED 03:18:22 (slippage -1.80)
  → TP_HIT 05:42:11 (PnL +3.01, RR 1.50)
  → Setup-to-close: 2h 27m

Setup at 2026-05-18 06:30 UTC (BTCUSDT LONG, conf=0.75, factors=3)
  → SETUP_SKIPPED_KILL_SWITCH (kill switch active: 3 consecutive losses)
```

## 14. Test Stratejisi

### 14.1 Birim testler

| Modül | Test sayısı tahmini | Yöntem |
|---|---|---|
| `binance_order_client` | ~8 | Mock REST (unittest.mock), error code mapping, retry backoff |
| `order_manager` | ~10 | FakeAdapter + FakeOrderClient, sinyal processing, sizing edge cases |
| `position_tracker` | ~12 | State machine geçişleri, restart recovery, drift |
| `mainnet_guard` | ~4 | 4 env+config kombinasyon |
| `audit_log` | ~5 | JSONL roundtrip, günlük rotasyon, concurrent write |
| `kill_switch` | ~8 | 3 metrik kombinasyonları, win-reset, manuel reset |
| `reconcile` | ~6 | FakeBinance ile drift simulation |
| `position_sizing` | ~6 | Edge cases (min_notional, lot_size, insufficient margin) |
| **Toplam yeni** | **~60** | **Mevcut 401 + ~60 = ~460** |

### 14.2 Integration testler

| Test | Yöntem |
|---|---|
| End-to-end FakeOrderClient | Signal → place → fill → TP/SL → audit, deterministik fill |
| Restart recovery | State yaz, runner reload, recovery doğru |
| Kill switch + restart | Kill switch tetikle, restart, hala aktif |
| Drift detection | FakeBinance drift inject, audit + kill switch |
| Timeout watcher | 60dk fill yok, cancel + abort |

### 14.3 Manuel smoke (kullanıcı)

**5A testnet smoke (2 gün):**
1. testnet API key (Binance Futures Testnet)
2. `SMC_ALLOW_LIVE=0` (zorunlu)
3. `config.execution.testnet=true`, `live_enabled=false`
4. Kontrol: ORDER_PLACED → FILLED → TP_HIT/SL_HIT pipeline çalışıyor mu, audit doğru mu

**5A mainnet smoke ($20-25):**
1. Mainnet read+trade API key (yeni, sub-proje #2'deki read-only'den farklı)
2. `SMC_ALLOW_LIVE=1`
3. `config.execution.testnet=false`, `live_enabled=true`
4. Binance Console → API → IP whitelist VPS IP
5. Restart service
6. 7 gün izleme: 10-12 trade, kill switch tetiklenmedi, drift yok

### 14.4 Coverage hedef

`pytest --cov=smc_engine/execution --cov-report=term-missing`

Hedef: **%90+** kritik modüllerde (mainnet_guard, kill_switch, position_tracker, order_manager).

## 15. Dependencies + Repo Yapısı

### 15.1 Yeni dependencies

```toml
# pyproject.toml [project.dependencies] zaten var:
# - python-binance>=1.0.21
# - apscheduler>=3.10
# - python-dotenv>=1.0

# Yeni ekleme YOK (5A için). Mevcut yeterli.
```

### 15.2 Repo yapısı

Bkz. 3.2.

## 16. Acceptance Kriterleri

### 16.1 5A bittiğinde

- [ ] Tüm yeni testler yeşil + mevcut 401 test korunmuş (~460 total)
- [ ] `pytest --cov=smc_engine/execution` ≥ %90
- [ ] Testnet smoke: 2 gün, ORDER pipeline tam, audit log doğru
- [ ] Mainnet smoke: 10-12 trade BTCUSDT, $20-25 bütçe
- [ ] Kill switch hiç tetiklenmedi (veya tetikleyebilirse manuel reset çalıştı)
- [ ] Reconcile drift hiç görülmedi
- [ ] `analyze_trades.py` çıktısı kullanıcının "anlaşılır" geri bildirimi
- [ ] `docs/operations/EXECUTION_RUNBOOK.md` yazıldı (operasyon)

### 16.2 5B'ye geçiş kriterleri (5A sonrası karar)

- 5A trade win rate ≥ %45 (backtest paritesi)
- 5A total PnL ≥ -$5 (en kötü %20 kayıp)
- 5A kill switch ≤ 1 kez tetiklendi (sistem stabil)
- 5A reconcile drift = 0 (state management sağlam)
- Kullanıcı analiz çıktısından "iyi" geri bildirim

Bu kriterler geçilirse 5B başlar. Geçilemezse → ratchet ile detector/confluence/gate optimization (Cowork ile, autoresearch skill).

---

## Sonraki adım

Bu spec onaylandıktan sonra: **writing-plans skill** ile 5A implementation plan'ı üretilir (faz 5A için bite-sized TDD task'lar, ~6-8 fazda). 5B için spec sonradan, 5A bittikten + sinyal kalitesi onaylandıktan sonra.
