# SMC Engine

Smart Money Concepts (SMC) trade motoru + backtest harness. Multi-timeframe
(D1 -> H8/H4 -> M15) kaskad analiz, 6 detektor, confluence skorlama, hard-gate
risk yonetimi ve look-ahead'siz bar-replay backtest. Anti-overfit katmani:
train/test split, walk-forward dogrulama, bootstrap Sharpe guven araligi.

> Durum: v1 -- motor + backtest + Faz 6 uctan uca dogrulama tamamlandi.
> Test: `pytest tests/ -q` -> 276 test yesil.

---

## Kurulum

Python 3.10+ gerekir.

```bash
cd smc-engine
pip install -e .            # pandas, ccxt, pyarrow, pyyaml
pip install -e ".[dev]"     # + pytest, pytest-xdist
```

Dogrulama:

```bash
python3 -c "import smc_engine; print('ok')"
pytest tests/ -q            # 276 test
```

---

## Kullanim

### Uctan uca calistirma -- `examples/run_btc.py`

Gercek BTC/USDT OHLCV uzerinde tam akis: veri yukle -> H8 resample -> `ohlcv_by_tf`
kur -> `harness.run()` -> walk-forward -> rapor + bootstrap CI.

```bash
python3 examples/run_btc.py
```

- **Veri kaynagi**: `data/btc/BTCUSDT_{D1,H4,H1,M15}.parquet` (2024-04-01 .. 2025-04-30,
  ~13 ay, Binance/CCXT ile cekilmis gercek veri). Parquet yoksa script CCXT ile
  yeniden ceker (ag gerekir).
- **Cikti**: backtest ozeti + walk-forward tablosu + `RATCHET_METRIC` satiri +
  bootstrap Sharpe %95 CI + `examples/btc_trades.csv`.
- **Deterministik**: ayni veri + config -> birebir ayni cikti (iki calistirma
  byte-identical).

### Smoke test -- `examples/smoke_test.py`

Minimal sentetik D1+H4+M15 set ile tum pipeline'in cokmeden calistigini dogrular
(orchestrator -> setup_builder -> risk_guard -> harness). Her faz sonunda calistirilir.

```bash
python3 examples/smoke_test.py    # "SMOKE OK"
```

### Programatik kullanim

```python
from smc_engine.config import SMCConfig
from smc_engine.types import TimeFrame
from backtest.harness import run as run_backtest
from backtest import report

config = SMCConfig()                       # config.yaml ile override edilebilir
ohlcv_by_tf = {TimeFrame.D1: d1, TimeFrame.H4: h4,
               TimeFrame.H8: h8, TimeFrame.M15: m15}
result = run_backtest(ohlcv_by_tf, config, initial_equity=10_000.0)
print(report.summary(result, config))
```

---

## Mimari Ozet

```
                 ohlcv_by_tf (D1 / H4 / H8 / M15)
                          |
              +-----------v------------+
              |   orchestrator.analyze  |  MTF kaskad (Spec 7)
              |   Katman 1: D1   (HTF bias + range + hedefler)
              |   Katman 2: H8/H4 (6 detektor, HTF context'le FILTRELENIR)
              |   Katman 3: M15  (fiyat aktif POI'deyken refine)
              +-----------+------------+
                          | MarketPicture
              +-----------v------------+
              |   setup_builder.build   |  confluence agirlikli skor -> Setup | None
              +-----------+------------+
                          | Setup
              +-----------v------------+
              |   risk_guard.validate   |  hard gate'ler + R-sizing -> ValidatedSetup | Rejection
              +-----------+------------+
                          |
              +-----------v------------+
              |   backtest.harness.run  |  bar-replay, look-ahead'siz, tek pozisyon
              |   + position_manager    |  TP merdiveni, BE, spread/komisyon/slippage
              |   + metrics + report    |  Sharpe/Sortino/PF/DD + walk-forward + CI
              +------------------------+
```

### 6 Detektor (`smc_engine/detectors/`)

Izole saf fonksiyonlar -- `detect(ohlcv, config, **kwargs) -> list[TypedObject]`.
Paylasilan `_swing_utils` (4-mum swing) ve `_atr` yardimcilari.

| Detektor | Cikti | Ne tespit eder |
|---|---|---|
| `structure_detector` | `StructureBreak` | CHoCH / BOS -- kapanisla terk teyidi |
| `range_detector` | `Range` | RH/RL/EQ, premium/discount bolgeleri |
| `zone_detector` | `Zone` | Order block + breaker block / indecision candle |
| `imbalance_detector` | `Imbalance` | FVG / liquidity void, fill_ratio |
| `liquidity_detector` | `LiquidityEvent` | Sweep / deviation / SFP, reclaim |
| `level_detector` | `Level` | MO/WO/DO kurumsal seviyeler + funding window |

### Orchestrator + setup_builder + risk_guard

- **orchestrator** (`smc_engine/orchestrator.py`): MTF kaskad. HTF bias'a uyumsuz
  POI'leri eler. Look-ahead'siz -- `at_bar` parametresi DataFrame'i `[:t+1]` diler,
  yalnizca kapanmis barlari kullanir. HTF cache (opsiyonel, determinizmi etkilemez).
- **setup_builder** (`smc_engine/setup_builder.py`): confluence agirlikli skor
  (POI kalitesi, premium/discount, likidite, level cakismasi, FVG, clustering --
  agirliklar `config.yaml`'da). Entry/SL/TP merdiveni uretir. Skor < esik -> `None`.
- **risk_guard** (`smc_engine/risk_guard.py`): hard gate'ler -- confluence, regime,
  deviation, yapisal SL, min RR, averaging-ban, drawdown breaker, session/funding
  farkindaligi. R-sizing (sabit % risk). Gecen setup -> `ValidatedSetup`,
  aksi -> `Rejection(gate=...)`.

### Backtest (`backtest/`)

- **harness.py** -- M15 bar-replay dongusu (Spec 8). `t` kapanisinda uretilen
  setup `t+1`'de dolar (ayni-bar fill yok). Bar-ici SL/TP cakismasinda en kotu
  senaryo (SL once). Tek pozisyon kurali. Fill modelleri: `next_open` (varsayilan,
  deterministik) ve `limit_retest` (path-dependent).
- **position_manager.py** -- pozisyon acma/kapama, TP merdiveni kademeli kapanis
  (`tp_weights`), TP1 sonrasi SL -> breakeven, spread + komisyon + slippage.
- **metrics.py** -- Sharpe/Sortino (yillik), win rate, profit factor, max DD
  (% + sure), R-multiple dagilimi, expectancy, confluence-kovasi performansi.
  `<30 trade -> low_trade_count_warning`. `bootstrap_sharpe_ci()` -- sabit seed'li
  bootstrap %95 CI.
- **walk_forward.py** -- kayan pencere dogrulama (train -> test -> kaydir, min 3
  pencere). Look-ahead'siz: `train_end <= test_start`.
- **report.py** -- chat ozeti + `trades.csv` + ratchet-uyumlu `RATCHET_METRIC`
  satiri + walk-forward tablosu.

### Veri katmani (`data/`)

- **fetch.py** -- `fetch_ohlcv(symbol, timeframe, since, until)` (CCXT) + parquet I/O.
- **resample.py** -- `resample_ohlcv(df, target_tf)` -- H1->H8/H4, multi-TF alignment.

---

## Anti-Overfit & Dogrulama (Spec 8.1)

| Yontem | Uygulama |
|---|---|
| Train/test split | %70/%30 zaman bazli (shuffle yok) -- `test_backtest_e2e.py` |
| Walk-forward | Kayan pencere, min 3 pencere -- `backtest/walk_forward.py` |
| Determinizm | Ayni veri + config -> birebir ayni `BacktestResult` |
| Look-ahead yok | Detektorler kapanmis mum kullanir; `at_bar` dilimleme; window-independence testi |
| Min trade count | `<30 trade` -> uyari bayragi (train ve test ayri) |
| Bootstrap CI | 1000 resample, sabit seed -> Sharpe %95 CI; alt sinir <=0 -> ratchet reddetmeli |

### Walk-forward sonuc ornegi

`examples/run_btc.py` ciktisindan (gercek BTC, sinirli M15 replay penceresi):

```
=== Walk-Forward Tablosu ===
  pencere | train araligi          | test araligi           | tr_sharpe | te_sharpe | tr_exp | te_exp | tr_tr | te_tr
        1 | 2024-06-02..2024-06-03 | 2024-06-03..2024-06-04 |    19.595 |     0.000 |  3.519 |  0.000 |     3 |     0
        2 | 2024-06-03..2024-06-04 | 2024-06-04..2024-06-04 |     0.906 |     0.000 |  0.550 |  0.000 |     2 |     0
        3 | 2024-06-03..2024-06-04 | 2024-06-05..2024-06-05 |    28.963 |     2.139 |  0.997 |  0.000 |     1 |     0
        4 | 2024-06-04..2024-06-05 | 2024-06-05..2024-06-06 |     1.077 |     0.000 |  0.000 |  0.000 |     0 |     0
  --- ozet: pencere=4 | ort tr_sharpe=12.635 | ort te_sharpe=0.535 | toplam test trade=0
  [UYARI] toplam test trade < 30 -- walk-forward Sharpe yaniltici olabilir.
```

> **Onemli -- karlilik iddiasi YOK.** v1 dogrulama kriteri: motor uctan uca
> calisiyor + deterministik + look-ahead yok + metrikler dogru hesaplaniyor +
> walk-forward >=3 pencere uretiyor. Trade sayisi dusuk (sinirli M15 replay
> penceresi -- asagidaki "Bilinen Sinirlar"a bakin); istatistiksel anlamlilik ve
> parametre optimizasyonu ratchet'in isidir.

---

## Ratchet Baglantisi (Spec 12)

Motor + backtest hazir oldugu icin bir `program.smc.md` ratchet connector'i
yazilabilir:

- **Metrik**: backtest Sharpe (yillik) + expectancy -- `RATCHET_METRIC` satirindan
  parse edilir (`backtest/report.py::ratchet_metric_line`).
- **Dogrulama komutu**: `python3 examples/run_btc.py`.
- **In-scope** (ratchet optimize eder): detektor esikleri, confluence agirliklari
  (`config.yaml` -- Spec 7 tablosu), fill modeli parametreleri.
- **Out-of-scope** (ground truth, degistirilmez): `backtest/harness.py`,
  `metrics.py`, `position_manager.py`.
- **Gate'ler** (metrik kabulunden once):
  - Min-trades gate: trade sayisi `<30` -> metrik reddedilir.
  - CI gate: bootstrap Sharpe %95 CI alt siniri `<=0` -> metrik reddedilir
    (Sharpe sifirdan istatistiksel olarak ayirt edilemez).

---

## Bilinen Sinirlar

- **Harness M15 replay maliyeti**: Faz 5 harness'i her M15 barinda orchestrator'i
  M15 dilimi uzerinde yeniden calistirir; structure/liquidity detektorleri
  dilim-uzunluguna gore maliyetli (O(n^2) egilim). Bu yuzden `run_btc.py` ve E2E
  testleri 13 aylik HTF baglamini (D1/H4/H8) **tam** korur ama M15 **replay'i**
  sinirli bir pencereye (~250-300 bar) keser ve `m15_lookback` parametresiyle
  per-cagri maliyetini baglar. Look-ahead guvenligi korunur: pencere yalnizca
  gecmisi keser, `at_bar`'dan sonrasini asla icermez. Tam 37920-barlik M15
  replay'i icin harness'in inkremental hale getirilmesi gerekir (gelecek is).
- **Dusuk trade sayisi**: sinirli replay penceresi nedeniyle ornek calistirmada
  az trade uretilir -> Sharpe/CI yaniltici. Gercek istatistiksel dogrulama tam
  replay + ratchet optimizasyonu gerektirir.
- **Multi-asset**: v1 yalnizca BTC. ETH + forex pair v2'de (Spec 8.1).
- **`limit_retest` fill modeli**: path-dependent; v1'de `next_open` ile dogrulandi.

---

## Test

```bash
pytest tests/ -q                          # 276 test
pytest tests/test_backtest_e2e.py -q      # E2E + determinizm (gercek BTC verisi)
pytest tests/test_walk_forward.py -q      # walk-forward kayan pencere
pytest tests/test_metrics.py -q           # metrikler + bootstrap CI
```

Test stratejisi: detektorler ve saf mantik TDD (bilinen fixture'da beklenen
cikti). Orchestrator/harness icin look-ahead bias assertion'lari. Determinizm
regression testleri. `examples/smoke_test.py` her faz sonunda smoke kontrolu.
