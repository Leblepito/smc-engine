# MINIMAX HANDOFF — SMC Engine Calibration Sweep

## Ortam
- **Path:** `/opt/data/smc-engine`
- **OS:** Linux, 32 cores / 251 GB RAM
- **Git commit (harness):** `988665a` — feat(calibration): parallel combo execution (ProcessPoolExecutor)
- **M15 dataset:** 37,920 bars, 2024-04-01 00:00 → 2025-04-30 23:45
- **Grid:** sl_min_atr_multiple × sl_band_buffer_mult = 6 × 3 = 18 kombinasyon

## Başlangıç Durumu (Kırık)
1. `scripts/calibration_sweep.py` çalıştırıldığında `ModuleNotFoundError` — `__init__.py` eksikti.
2. PYTHONPATH resolve hatası — `sys.path.insert(0, '/opt/data/smc-engine')` ile çözüldü.
3. `--m15-window` default 250 bar (tuzak!) — `m15_window=None` (full dataset) olarak düzeltildi (commit `4f357c2`).

## Denenen Tüm Run'lar (Kronolojik)

| # | Tarih | Komut/Script | Sonuç | Not |
|---|-------|-------------|-------|-----|
| 1 | 2026-05-20 erken | `run_sweep.py` (seri, 18 combos) | sweep-P1.csv ✓, sweep-P2.csv ✓, sweep-P3.csv ✓ | 3 ayrı pencere |
| 2 | 2026-05-20 | `run_sweep.py` P1 baseline | p1_baseline.csv ✓ | sl=0.5, buf=0.25 |
| 3 | 2026-05-21 | `scripts/calibration_sweep.py` (parallel, workers=32) | wf-run.log ✓ → calibration-results-2026-05-21.csv ✓ | 18 rows, 0 ratchet-passing |
| 4 | 2026-05-21 | Aynı script (2. çağrı, BrokenProcessPool) | **wf-run2.log CRASH** | `BrokenProcessPool: A process in the process pool was terminated abruptly` |

**Run #4 crash:** ProcessPoolExecutor workers=32 ile back-to-back sweep çağrısı → process pool corrupted. Bunu takip eden tüm sweep denemeleri aynı process'te çalışamaz. Temiz çözüm: `max_workers=1` (seri) veya her run'dan sonra Python interpreter yeniden başlat.

## Dosyalar (Source of Truth = ham CSV'ler)

> ⚠️ **DİKKAT:** Transkript/tablo sayıları raporlar arasında değişti. Tablolarım değil, ham CSV dosyaları authoritative'dir.

| Dosya | Path | mtime | Durum |
|-------|------|-------|-------|
| sweep-P1.csv | `/opt/data/smc-engine/logs/calibration/sweep-P1.csv` | 2026-05-20 08:03 | ✓ Geçerli, 18 rows |
| sweep-P2.csv | `/opt/data/smc-engine/logs/calibration/sweep-P2.csv` | 2026-05-20 08:11 | ✓ Geçerli, 18 rows |
| sweep-P3.csv | `/opt/data/smc-engine/logs/calibration/sweep-P3.csv` | 2026-05-20 08:29 | ✓ Geçerli, 18 rows |
| p1_baseline.csv | `/opt/data/smc-engine/logs/calibration/p1_baseline.csv` | 2026-05-20 06:50 | ✓ Geçerli (sl=0.5/buf=0.25 baseline row) |
| sweep_run.log | `/opt/data/smc-engine/logs/calibration/sweep_run.log` | 2026-05-20 04:46 | ✓ Meta log (Sweep B başlatma) |
| calibration-results-2026-05-21.csv | `/opt/data/smc-engine/logs/calibration/calibration-results-2026-05-21.csv` | 2026-05-21 09:44 | ❌ **INVALID** — harness crash: tc=5, pf=0, wr=0.0, exp=-49.14 (broken process pool üretti). KORUNUYOR (veri silinmedi). |

## P1 / P2 / P3 Pencere Tanımları

### P1 — In-Sample (En Eski Pencere)
```
offset  = 0
bars    = 29,420 (m15.iloc[0:29420])
start   = 2024-04-01 00:00:00+00:00
end     = 2025-02-01 10:45:00+00:00
durum   = ~306 gün

Tam komut (run_sweep.py):
  from backtest.harness import run as harness_run
  m_slice = m15.iloc[0:29420]
  ohlcv = {D1, H4, H8, M15: m_slice}
  # grid: sl_min=[0.25..0.50], sl_buf=[0.25,0.375,0.50]
```

### P2 — In-Sample (Orta Pencere)
```
offset  = 29,420  (len(m15) - 8500)
bars    = 8,500    (m15.iloc[29420:37920])
start   = 2025-02-01 11:00:00+00:00
end     = 2025-04-30 23:45:00+00:00
durum   = ~88.5 gün

Tam komut (run_sweep.py / run_sweep_batch.py):
  offset = 37920 - 8500  # = 29420
  m_slice = m15.iloc[offset:]  # = m15.iloc[29420:]
  # ya da: m_slice = m15.iloc[29420:37920]
  ohlcv = {D1, H4, H8, M15: m_slice}
```

### P3 — Out-of-Sample / Son Test Penceresi
```
offset  = 35,920  (len(m15) - 2000)
bars    = 2,000    (m15.iloc[35920:37920])
start   = 2025-04-10 04:00:00+00:00
end     = 2025-04-30 23:45:00+00:00
durum   = ~20.8 gün (500 saat)

Tam komut:
  offset = 35920
  m_slice = m15.iloc[35920:37920]
  ohlcv = {D1, H4, H8, M15: m_slice}

wf-run.log log kaydı:
  Window: 2025-04-10 04:00:00+00:00 -> 2025-04-30 23:45:00+00:00, 2000 bars
```

### M15 Bar → Takvim Çevrim Tablosu
```
P1: 29,420 bars × 15 dk = 441,300 dk = 7,355 saat ≈ 306 gün (2024-04-01 → 2025-02-01)
P2:  8,500 bars × 15 dk = 127,500 dk = 2,125 saat ≈  88.5 gün (2025-02-01 → 2025-04-30)
P3:  2,000 bars × 15 dk =  30,000 dk =   500 saat ≈  20.8 gün (2025-04-10 → 2025-04-30)
```

## Teşhis Bulguları (DOĞRULANMAMIŞ Hipotezler)

### (a) WF 120/40/40 → ~5 Trade/Pencere = Anlamsız
Walk-forward train=120/test=40/step=40 ile P3 (2000 bar) penceresi uygulandığında, test penceresi başına ~5 trade üretildi. Bu istatistiksel olarak anlamsız (overfitting riski çok yüksek). Daha büyük test penceresi veya daha fazla pencere gerekli.

### (b) BrokenProcessPool VPS Crash'leri
`ProcessPoolExecutor(max_workers=32)` ile paralel sweep, back-to-back çağrılarda process pool corruption. Muhtemel sebep: önceki worker süreçleri tam temizlenmeden yeni pool oluşturulması. **Geçici çözüm:** `max_workers=1` (seri) veya her run arasında `multiprocessing` pool reset.

### (c) P3 Rejim Çöküşü — DOĞRULANMADI
P3 CSV'sinde sl_min_atr_multiple ≥ 0.45 kombinasyonlarda **tüm metrikler çökmüş:**
- win_rate = 0.0, expectancy çok negatif, pf = 0
- sl_min_atr_multiple 0.25–0.40 arasında (8 trade, win_rate=0.25) — marjinal
- sl_min_atr_multiple 0.45–0.50 arasında (5 trade, win_rate=0.0) — tam çöküş

**Bu gözlem SWEEP artifacts'ından çıkarılmıştır, temiz walk-forward run ile teyit EDİLMEMİŞTİR.** Mayıs 2025 başında P3 penceresi ~20 günlük periyotta piyasa rejimi değişikliği ( Consolidation? trend? Sideways?) olabilir — bu hipotezi test etmek için M15 bar grafiği görselleştirmesi gerekir.

## Güvenilmez Kısımlar (Açıkça Yazılır)

1. **P1/P2/P3 tablolarındaki sayılar** — raporlar arasında değişti. `Source of truth = ham CSV dosyaları`.
2. **P1'deki tekrarlayan satırlar** — sl_min_atr_multiple 0.30–0.50 arasında sl_band_buffer=0.25 aynı expectancy (-0.0699) veriyor. Harness bug veya başka bir mekanizma olabilir — araştırılmalı.
3. **P3 çöküş hipotezi** — yukarıda (c) olarak yazıldı, teyit edilmemiş.
4. **calibration-results-2026-05-21.csv** — INVALID. BrokenProcessPool crash artifact'ı. Tüm tc=5, pf=0 değerleri geçersiz.

## Bitirilmeyen İş
- **Temiz walk-forward** — wf-run2 crash'ten sonra walk-forward tamamlanamadı.
- `calibration_sweep.py --walk-forward --wf-train-bars 120 --wf-test-bars 40 --wf-step-bars 40 --m15-offset 0` ile temiz WF başlat.
- Ratchet baseline: tc=32, exp=-0.0699, wr=0.531, dd=0.0319 (P1'den sl=0.5/buf=0.25).