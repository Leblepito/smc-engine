# Sub-proje #5A Execution Runbook

> Spec: `docs/superpowers/specs/2026-05-17-subproject-5-execution-design.md`
> Plan: `docs/superpowers/plans/2026-05-17-subproject-5A-execution-implementation-plan.md`
> Stack: smc-engine main branch, Hetzner VPS nbg1 cx23, systemd `smc-engine.service`

Bu runbook 5A (walking skeleton) için günlük operasyon adımlarını içerir.
Tüm execution kodu **default OFF** — sadece explicit opt-in ile aktif olur.

---

## 1) Service durumu — günlük kontrol

```bash
ssh smc@94.130.148.21 "sudo systemctl is-active smc-engine"
ssh smc@94.130.148.21 "tail -20 ~/smc-engine/logs/signals-\$(date -u +%Y%m%d).jsonl | head"
```

`active` + log lines görüyorsan OK. Sub-proje #2 hâlâ log-only — execution yok.

---

## 2) Testnet smoke (2 gün, $0 risk)

### 2.1 Binance Futures Testnet hesap aç

1. https://testnet.binancefuture.com/ → kayıt ol
2. Sağ üstteki "Get Test Funds" → 100,000 USDT al
3. Account → API Management → Create API → testnet key + secret üret
4. Permissions: "Enable Reading" + **"Enable Futures Trading"** işaretle

### 2.2 Config + .env hazırlık (VPS'te)

```bash
ssh smc@94.130.148.21

cd ~/smc-engine
# Mevcut .env'yi yedekle
cp .env .env.bak.subproje2

# .env'i güncelle
cat > .env <<'EOF'
BINANCE_API_KEY=<testnet_key>
BINANCE_API_SECRET=<testnet_secret>
SMC_ALLOW_LIVE=0
EOF
chmod 600 .env

# config.yaml oluştur (yoksa)
cat > config.yaml <<'EOF'
execution:
  enabled: true
  phase: "5A"
  testnet: true
  live_enabled: false
  risk_per_trade_dollar: 2.0
  leverage: 10
  order_timeout_minutes: 60
  symbols: [BTCUSDT]
  kill_switch:
    consecutive_losses: 3
    daily_loss_dollar: 5.0
    equity_minimum: 50.0
EOF
```

### 2.3 systemd unit'i güncelle

`/etc/systemd/system/smc-engine.service` içindeki `ExecStart` satırına `--execution-enabled` flag'i ekle:

```ini
ExecStart=/home/smc/smc-engine/.venv/bin/python /home/smc/smc-engine/examples/run_live.py \
  --log-dir /home/smc/smc-engine/logs \
  --buffer-seconds 5 \
  --execution-enabled
```

Sonra:
```bash
sudo systemctl daemon-reload
sudo systemctl restart smc-engine
sudo systemctl status smc-engine
```

Beklenen: `Active: active (running)`, MainnetGuard log `mode=TESTNET use_testnet=true`.

### 2.4 İzleme (2 gün)

```bash
# Pipeline:
ssh smc@94.130.148.21 "tail -F ~/smc-engine/logs/trades-\$(date -u +%Y%m%d).jsonl"

# Service log:
ssh smc@94.130.148.21 "sudo journalctl -u smc-engine -f"

# Daily summary (PC'den):
scp smc@94.130.148.21:~/smc-engine/logs/trades/*.jsonl logs/trades/
python scripts/analyze_trades.py --date $(date -u +%Y-%m-%d)
```

Beklenen iki gün içinde:
- ≥1 ORDER_PLACED → ORDER_FILLED → TP_HIT veya SL_HIT akışı tamamlanmış
- RECONCILE_DRIFT = 0
- KILL_SWITCH_TRIGGERED = 0 (sıkı eşikler değilse)

---

## 3) Mainnet smoke ($20-25, 7 gün)

**ÖN ŞART:** Testnet smoke 2 gün boyunca temiz çalıştı, en az 1 trade tamamlandı.

### 3.1 Mainnet API key

1. https://www.binance.com/en/my/settings/api-management → Create API
2. Title: `smc-engine-execution-5A`
3. Permissions: **"Enable Reading"** + **"Enable Futures"** (withdraw KAPALI)
4. **IP Access Restriction:** Restrict access to trusted IPs → `94.130.148.21`
5. Save → key + secret görünür

### 3.2 Mainnet wallet hazırlık

Binance Futures hesabına ≥$25 USDT transfer et (Spot → Futures internal transfer).
Bu $25 risk-of-loss budget. Daha fazla koyma.

### 3.3 Config + .env

```bash
ssh smc@94.130.148.21
cd ~/smc-engine

cat > .env <<'EOF'
BINANCE_API_KEY=<mainnet_key>
BINANCE_API_SECRET=<mainnet_secret>
SMC_ALLOW_LIVE=1
EOF
chmod 600 .env

# config.yaml güncelle: testnet → false, live_enabled → true
sed -i 's/testnet: true/testnet: false/' config.yaml
sed -i 's/live_enabled: false/live_enabled: true/' config.yaml
# equity_minimum'u risk seviyene göre ayarla
sed -i 's/equity_minimum: 50.0/equity_minimum: 15.0/' config.yaml
```

### 3.4 Restart + initial verification

```bash
sudo systemctl restart smc-engine
# 5 saniye boyunca terminal'de "MAINNET MODE ACTIVE" WARNING görürsün
# Ctrl+C'ye basma! 5sn sonra geçer.

sudo systemctl status smc-engine | head -20
sudo journalctl -u smc-engine -n 30
```

Beklenen log:
```
MainnetGuard: all 3 layers passed → MAINNET
execution mainnet guard: mode=MAINNET use_testnet=False
```

### 3.5 7-gün izleme

Günlük:
```bash
# PC'den
scp smc@94.130.148.21:~/smc-engine/logs/trades/*.jsonl logs/trades/
python scripts/analyze_trades.py --since $(date -u -d "7 days ago" +%Y-%m-%d) --until $(date -u +%Y-%m-%d)
```

Acceptance kriterleri (Spec §16.2):
- 10-12 trade tamamlandı
- Win rate ≥ %45
- Total PnL ≥ -$5
- Kill switch ≤ 1 tetikleme
- Reconcile drift = 0

---

## 4) Kill switch tetiklendi — kurtarma

### 4.1 Tanı

```bash
ssh smc@94.130.148.21 "sudo journalctl -u smc-engine -n 100 | grep -i kill_switch"
```

Audit log'da `KILL_SWITCH_TRIGGERED` event'inin `reasons` field'ına bak:
- `consecutive_losses=3` → 3 ardışık SL
- `daily_pnl=-5.50` → günlük loss eşiği
- `equity=14.50` → equity floor
- `RECONCILE_DRIFT: [...]` → state vs Binance uyuşmazlığı

### 4.2 Manuel inceleme

```bash
ssh smc@94.130.148.21
cd ~/smc-engine
.venv/bin/python scripts/reconcile_check.py     # Drift var mı?
.venv/bin/python scripts/analyze_trades.py     # PnL detay
```

### 4.3 Reset

```bash
ssh smc@94.130.148.21 "~/smc-engine/scripts/kill_switch_reset.sh"
# Confirm prompt'a "yes" yaz
sudo systemctl restart smc-engine
```

Audit log'a `KILL_SWITCH_RESET` event'i yazılır.

---

## 5) Reconcile drift — manuel müdahale

```bash
ssh smc@94.130.148.21
cd ~/smc-engine

# 1. Drift'i tanımla
.venv/bin/python scripts/reconcile_check.py

# 2. Karar ver:
#    a) Local state Binance ile sync değil → local'i sil
#    b) Binance'te manuel açtığın order var → onu sil
#    c) Beklenmedik durum → Spec §9.2 + Binance UI ile inceleme

# Local state sıfırlama:
rm -f logs/state/positions-*.json
rm -f logs/state/kill_switch_state.json

# Restart
sudo systemctl restart smc-engine
```

---

## 6) Hızlı komutlar (cheat sheet)

```bash
VPS_IP=94.130.148.21

# Status
ssh smc@$VPS_IP "sudo systemctl status smc-engine --no-pager | head -10"
ssh smc@$VPS_IP "tail ~/smc-engine/logs/trades/trades-\$(date -u +%Y%m%d).jsonl"

# Stop/start
ssh smc@$VPS_IP "sudo systemctl stop smc-engine"
ssh smc@$VPS_IP "sudo systemctl start smc-engine"

# Logs (live)
ssh smc@$VPS_IP "sudo journalctl -u smc-engine -f"

# Code update + restart
ssh smc@$VPS_IP "cd ~/smc-engine && git pull && sudo systemctl restart smc-engine"

# Pull all logs to local for analysis
rsync -avz smc@$VPS_IP:~/smc-engine/logs/ logs/

# Analyze
python scripts/analyze_trades.py --since 2026-05-18 --until 2026-05-25
python scripts/analyze_combined.py --date 2026-05-18
```

---

## 7) 5B'ye geçiş kararı (smoke sonrasi)

Acceptance kriterleri geçildi mi?

| Kriter | Geçti? |
|---|---|
| 10-12 trade tamamlandı | ☐ |
| Win rate ≥ %45 | ☐ |
| Total PnL ≥ -$5 | ☐ |
| Kill switch ≤ 1 trigger | ☐ |
| Reconcile drift = 0 | ☐ |

**Hepsi geçti** → Cowork ile 5B spec yaz → 5B plan → 5B implementation (5 coin, $100 budget).

**Bazıları geçemedi** → ratchet ile detector/confluence optimization. Autoresearch skill ile root cause analizi yap.

---

## Troubleshooting

| Sorun | Çözüm |
|---|---|
| MainnetGuard `mode=TESTNET` (mainnet beklerken) | env `SMC_ALLOW_LIVE=1` ve config `execution.live_enabled: true` ikisi de aktif mi? |
| `RuntimeError: Mainnet not approved` | yukarıdaki ile aynı; service restart sonrası 5sn warning görmen lazım |
| `Binance: -2019 MARGIN_INSUFFICIENT` | Futures wallet bakiyesi yetersiz; spot → futures transfer yap |
| `Binance: -1013 PRICE_FILTER` | Setup entry price spot'tan çok uzak; signal kalitesi sorunlu — kill switch otomatik tetiklenir |
| Service `Active: failed` | `sudo journalctl -u smc-engine -n 50` ile sebep gör; .env eksik / Binance auth / config syntax error |
| Audit log boş ama signals var | `--execution-enabled` flag systemd unit'te var mı? `cat /etc/systemd/system/smc-engine.service` |
| Order placed ama fill gelmedi (60dk timeout) | Normal — fiyat entry'ye gelmedi; bir sonraki M15'te tekrar deneriz |
