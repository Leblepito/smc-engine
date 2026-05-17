# Hetzner Deploy Runbook — smc-engine

Sub-proje #2'nin canlı koşturulması için Hetzner VPS provisioning + deploy adımları.
Bu runbook, tam otomatik agent deploy denemesinin auto-mode classifier tarafından
bloklanması üzerine "user-driven 3 komut + agent-driven SSH deploy" hibrit
modeline indirgenmiştir. Idempotent — tekrar çalıştırma duplicate yaratmaz.

## 1) Önkoşullar (sende olması gerekenler)

| | |
|---|---|
| **Hetzner API token** | `.env` içinde `HCLOUD_API_KEY=...` |
| **Binance read-only API** | `.env` içinde `BINANCE_API_KEY=...` + `BINANCE_API_SECRET=...` |
| **SSH key pair** | `~/.ssh/id_ed25519` (private) + `id_ed25519.pub` (public) |
| **python3** | Yerel makinede PATH'te (script JSON payload'larını Python ile kuruyor; jq dependency yok) |
| **GitHub private repo erişimi** | Aşağıda Deploy Key adımı var |

## 2) VPS create (1 komut, ~30sn)

```bash
# repo kökünden
bash deploy/hetzner/create_vps.sh
# Default: cpx11 / fsn1 / Ubuntu 24.04 → €5.99/ay
#
# Override örnekleri:
#   SERVER_TYPE=cpx22 LOCATION=hel1 bash deploy/hetzner/create_vps.sh
#   LOCATION=nbg1 bash deploy/hetzner/create_vps.sh
```

Çıktıda `VPS_IP=X.X.X.X` ve `VPS_ID=...` görürsün. **IP'yi not al** — bir sonraki adımlar için lazım.

Idempotency: `smc-engine-runner-01` zaten varsa skip eder, mevcut IP'yi yazar.

## 3) cloud-init bitişini bekle (~2-3 dakika)

Sunucu `running` olur olmaz SSH açıkır ama paket kurulumu birkaç dakika sürer:

```bash
VPS_IP=<yukarıdaki IP>
ssh -o StrictHostKeyChecking=accept-new smc@$VPS_IP "echo ok"
# henüz hazır değilse "Connection refused" görürsün; 30sn bekle, tekrar dene.

# cloud-init done flag'ini bekle
while ! ssh -o ConnectTimeout=5 smc@$VPS_IP "test -f /var/lib/cloud/instance/cloud-init-done" 2>/dev/null; do
    echo "cloud-init devam ediyor..."
    sleep 10
done
echo "cloud-init bitti."
```

## 4) GitHub deploy key (one-time, ~2 dakika)

Repo private — VPS'in GitHub'a SSH ile erişebilmesi için bir deploy key lazım.

```bash
# VPS'te key oluştur
ssh smc@$VPS_IP "ssh-keygen -t ed25519 -f ~/.ssh/github_deploy -N '' -C 'smc-engine-vps-deploy'"
# Public key'i göster
ssh smc@$VPS_IP "cat ~/.ssh/github_deploy.pub"
```

Bu public key çıktısını **kopyala**, sonra:

1. https://github.com/Leblepito/smc-engine/settings/keys → **Add deploy key**
2. Title: `smc-engine-runner-01 (Hetzner fsn1)`
3. Key: yapıştır
4. **Allow write access: KAPALI** (read-only deploy key)
5. Add key

Sonra SSH config oluştur ki `git@github.com:...` çekerken bu key kullanılsın:

```bash
ssh smc@$VPS_IP "cat > ~/.ssh/config <<'EOF'
Host github.com
  IdentityFile ~/.ssh/github_deploy
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
EOF
chmod 600 ~/.ssh/config"
```

## 5) Repo deploy + venv + service (1 komut, ~3-4 dakika)

```bash
ssh smc@$VPS_IP "curl -fsSL https://raw.githubusercontent.com/Leblepito/smc-engine/main/deploy/hetzner/deploy.sh | bash"
```

Bu script:
- Repo clone (ilk sefer) veya `git pull --ff-only`
- venv kur + dependencies install
- Smoke test suite çalıştırır (380 test, ~10sn)
- systemd unit'i `/etc/systemd/system/`'e kopyalar + `daemon-reload`
- Service'i enable + start eder
- Status basar

**Not:** İlk çalıştırmada `.env` yok, service hata verir — Phase 6 (sonraki) `.env` upload eder.

## 6) `.env` upload (yerel makineden, manuel)

VPS'e sadece Binance keys gitsin, Hetzner token GİTMESİN:

```bash
# Lokalden (PowerShell'de eşdeğer komut için aşağıyı oku)
grep -v '^HCLOUD_API_KEY=' .env > /tmp/env.for_vps
scp /tmp/env.for_vps smc@$VPS_IP:~/smc-engine/.env
ssh smc@$VPS_IP "chmod 600 ~/smc-engine/.env && sudo systemctl restart smc-engine"
rm /tmp/env.for_vps
```

PowerShell eşdeğeri:
```powershell
Get-Content .env | Where-Object { $_ -notmatch '^HCLOUD_API_KEY=' } | Set-Content $env:TEMP\env.for_vps -Encoding ASCII
scp $env:TEMP\env.for_vps smc@${VPS_IP}:~/smc-engine/.env
ssh smc@${VPS_IP} "chmod 600 ~/smc-engine/.env && sudo systemctl restart smc-engine"
Remove-Item $env:TEMP\env.for_vps
```

## 7) Binance sanity (~10sn)

```bash
ssh smc@$VPS_IP "cd ~/smc-engine && .venv/bin/python -c \"
from dotenv import load_dotenv
load_dotenv()
from smc_engine.integrations.binance.client import BinanceClient
import os
c = BinanceClient(os.environ['BINANCE_API_KEY'], os.environ['BINANCE_API_SECRET'])
syms = c.futures_exchange_info()['symbols']
print(f'Binance OK: {len(syms)} symbols, e.g. {syms[0][\\\"symbol\\\"]}')
\""
```

Beklenen: `Binance OK: 600+ symbols, e.g. BTCUSDT`.

## 8) Smoke wait — ilk M15 tick (~max 16 dakika)

M15 kapanışları: 00, 15, 30, 45 dakika. Bir sonraki kapanıştan ~5sn sonra log dosyası gelmeli.

```bash
TODAY=$(date -u +%Y%m%d)
# Live tail:
ssh smc@$VPS_IP "tail -F ~/smc-engine/logs/signals-${TODAY}.jsonl"
# Veya status + journald:
ssh smc@$VPS_IP "sudo journalctl -u smc-engine -n 30 --no-pager"
```

En az 1 event (validated_setup veya rejection) görünce smoke ✓.

## 9) Snapshot (rollback için, ~€0.50/ay)

```bash
bash deploy/hetzner/snapshot.sh "smc-engine v1 + first-deploy clean"
```

Çıktıdaki `SNAPSHOT_ID`'yi not al — recovery için console.hetzner.cloud'dan kullanılır.

## 10) Cheat sheet (PC'den)

```bash
VPS_IP=<your-ip>
ssh smc@$VPS_IP "sudo systemctl status smc-engine"
ssh smc@$VPS_IP "sudo journalctl -u smc-engine -f"
ssh smc@$VPS_IP "tail -F ~/smc-engine/logs/signals-\$(date -u +%Y%m%d).jsonl"
ssh smc@$VPS_IP "~/smc-engine/deploy/hetzner/status.sh"
ssh smc@$VPS_IP "~/smc-engine/deploy/hetzner/deploy.sh"   # pull latest + restart
# Local'de raporlama:
python scripts/analyze_signals.py --date $(date -u +%Y-%m-%d)
# (logs/ klasörünü VPS'ten çekmek istersen)
rsync -avz smc@$VPS_IP:~/smc-engine/logs/ logs/
```

## 11) Operasyonel notlar

- **Mainnet guard:** `examples/run_live.py` `--live` flag yok — emir göndermez. Sub-proje #5'te `SMC_ALLOW_LIVE=1` env şartı eklenecek.
- **Binance IP whitelist (öneri):** Console → API Management → ilgili key → IP access restriction → VPS IP'ni ekle. Key sızsa bile başka IP'den kullanılamaz.
- **Snapshot maliyeti:** ~€0.012/GB/ay. cpx11'de root disk 40 GB → ~€0.50/ay. İhmal edilebilir değil ama düşük.
- **HCLOUD_API_KEY rotasyonu:** Deploy bittikten sonra `.env`'den `HCLOUD_API_KEY=` satırını silebilirsin (gelecekteki create/snapshot için tekrar lazım olur). Veya tutmaya devam et — `.env` zaten `.gitignore`'da.

## 12) Teardown (silmek istersen)

```bash
HCLOUD_TOKEN=$(grep '^HCLOUD_API_KEY=' .env | cut -d= -f2- | tr -d '[:space:]\r')
VPS_ID=$(curl -sS -H "Authorization: Bearer $HCLOUD_TOKEN" \
    "https://api.hetzner.cloud/v1/servers?name=smc-engine-runner-01" \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['servers'][0]['id'])")
curl -sS -X DELETE -H "Authorization: Bearer $HCLOUD_TOKEN" \
    "https://api.hetzner.cloud/v1/servers/${VPS_ID}"
```

## Troubleshooting

| Sorun | Çözüm |
|---|---|
| `HTTP 401` | Token geçersiz / revoke edilmiş — Hetzner console'da yeni token üret, `.env`'i güncelle |
| `HTTP 422 server type X deprecated` | `SERVER_TYPE=cpx11` env var ile yeni tipe geç |
| `HTTP 422 unsupported location for server type` | DC'de kapasite yok — `LOCATION=hel1` veya `LOCATION=fsn1` ile dene |
| `HTTP 409 SSH key not unique` | Senin pub key zaten Hetzner'da farklı isim altında kayıtlı — `create_vps.sh` zaten fingerprint match ile yakalıyor |
| Service `Active: failed` | `sudo journalctl -u smc-engine -n 50` ile sebep gör; çoğunlukla `.env` eksik veya Binance auth hatası |
| `git clone Permission denied (publickey)` | GitHub deploy key adımı atlandı; (4)'ü tekrar yap |
| cloud-init asılı kaldı | `ssh smc@$VPS_IP "sudo tail -100 /var/log/cloud-init-output.log"` |
