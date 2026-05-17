# Sub-proje #2 Handoff — Claude Code'a Geçiş

Bu paket smc-engine sub-proje #2'yi (Binance live signal pipeline) **Claude Code (VSCode lokal)** ortamında inşa etmen için hazırlandı. Cowork'tekinin yerine geçer — `signals.jsonl` günler boyunca senin diskinde birikecek, scheduler senin makinende çalışacak.

## Paket içeriği

- `smc-engine-subproject-2-binance-design-2026-05-16.md` — **Spec** (16 bölüm, ne inşa edilecek)
- `smc-engine-subproject-2-binance-plan-2026-05-16.md` — **İmplementasyon planı** (5 faz, ~25 bite-sized TDD task, tahmini süreler)
- `CLAUDE_CODE_BINANCE_PROMPT.md` — **Claude Code brief'i** (tek seferde yapıştır, ajan spec+plan'ı okur ve uygular)
- `subproject-2-handoff-README.md` — bu dosya

## Kurulum (3 adım)

### 1. Paketi smc-engine repo'na koy

`subproject-2-handoff.zip`'i şuraya çıkar (smc-engine kök altında yeni `docs/subproject-2/` klasörü):

```powershell
cd "C:\Users\utkuc\OneDrive\Masaüstü\smc-engine"
mkdir docs\subproject-2 -ErrorAction SilentlyContinue
# Sonra zip'i bu klasöre aç (Windows: sağ tık → Extract All)
```

Sonuçta şu yapı oluşmalı:
```
smc-engine/
├── docs/
│   ├── integrations/TRADINGVIEW.md   (mevcut)
│   └── subproject-2/
│       ├── smc-engine-subproject-2-binance-design-2026-05-16.md
│       ├── smc-engine-subproject-2-binance-plan-2026-05-16.md
│       ├── CLAUDE_CODE_BINANCE_PROMPT.md
│       └── subproject-2-handoff-README.md
├── smc_engine/
├── backtest/
├── ...
```

### 2. Commit + push

```powershell
cd "C:\Users\utkuc\OneDrive\Masaüstü\smc-engine"
git add docs/subproject-2
git commit -m "docs: subproject-2 (Binance) spec + plan + Claude Code brief"
git push
```

### 3. Claude Code başlat

- VSCode'da `smc-engine` klasörünü aç (zaten açtıysan refresh)
- Claude Code panel'i aç (sidebar veya Cmd/Ctrl+L)
- **`docs/subproject-2/CLAUDE_CODE_BINANCE_PROMPT.md` içeriğini kopyala → Claude Code chat'ine yapıştır**
- Claude Code spec/plan'ı okuyacak, faz B0'dan başlayıp B5'e kadar TDD ile inşa edecek

Tahmini süre: ~2.5 saat agent çalışma, 4 commit + push.

## Çalıştırırken neler bekleyebilirsin

- **Her faz sonu rapor:** Claude Code pytest çıktısını, hangi dosyaların değiştiğini, skill kullanımını yazacak. Sapma/şüphe varsa açıkça belirtecek.
- **Test green disiplini:** her task TDD ile gider — kırmızı görmeden impl yazılmaz. Mevcut 354 testin korunması zorunlu.
- **Commit cadence:** faz başına 1 commit + push (B0, B1, B2, B3, B4, B5).
- **API key:** `.env` dosyasını sen oluşturacaksın (gerçek read-only Binance key). Claude Code repo'ya commit etmez (gitignore'da).

## Manuel smoke test (Faz B4.2)

Plan B4'ün sonunda manuel smoke yapacaksın:

```powershell
cd "C:\Users\utkuc\OneDrive\Masaüstü\smc-engine"
# .env dosyasını oluştur (.env.example template):
copy .env.example .env
# .env'i editle, gerçek read-only Binance key'leri yaz

# Test çalıştır (1 M15 cycle bekle):
python examples/run_live.py --symbols BTCUSDT --equity 10000

# Log'u incele:
type logs/signals-*.jsonl
```

İlk M15 kapanışı geldiğinde (max 16dk bekleme) sinyaller log'a düşmeye başlar. Ctrl+C ile durdur.

## Sub-proje #2 sonrası

1. **Birkaç gün canlı izleme** (3-7 gün) — `signals.jsonl` topla, sinyal kalitesini değerlendir
2. **Sinyaller iyi görünüyorsa** → **sub-proje #5** (execution & risk yönetimi)
3. **Sinyaller iyileştirme gerektiriyorsa** → ratchet ile detektör/confluence ağırlıklarını optimize et
4. **#5 tamamlanınca** → senin $100 canlı testi, 4-5 coinde, sıkı risk_guard

## Cowork (ben) ile irtibat

Bu noktadan sonra Cowork'te değil Claude Code'da çalışacağız. Ama büyük tasarım soruları, sub-proje sonrası kapsamlı code-review/audit, ya da efloud-bot ↔ smc-engine entegrasyon kararları için Cowork'e geri dönebiliriz — projenin "stratejik" katmanı burada kalabilir, "operasyonel" katman Claude Code'da.

İyi inşalar.
