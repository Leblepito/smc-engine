# TradingView Entegrasyonu (Pine v6 + MCP)

> SMC Engine'in **TradingView Desktop'a kopru** katmani. Kullanici makinesinde
> tradingview-mcp + TradingView Desktop debug-mode calistirir; Claude Code
> dogal dil prompt'lariyla canli Pine Script v6 indikator uretir. Bu repo o
> akisi *besler* — prompt sablonlari, `SMCConfig` -> Pine input export'u ve
> backtest setup'larini chart'a izdusurmek icin overlay export'u.

## Tek Seferlik Kurulum

Bu kisim makaledeki ([Yasinarsal — "Claude AI + MCP ile TradingView'da canli
Pine Script"](https://yasinarsal.com)) akisin ozetidir. Tum komutlar
**kullanicinin makinesinde** calisir; bu repo MCP server'i kurmaz.

1. **MCP server'i clone et + kur**:
   ```bash
   git clone https://github.com/tradesdontlie/tradingview-mcp.git
   cd tradingview-mcp
   npm install
   npm run build
   ```
2. **Claude Code MCP config**: kullanici `~/.config/claude-code/mcp.json`
   (veya proje koku `.mcp.json`) icine `tradingview-mcp` server'ini ekler.
3. **TradingView Desktop'i debug modda baslat** (Windows ornek):
   ```
   "C:\Users\<you>\AppData\Local\Programs\TradingView\TradingView.exe" --remote-debugging-port=9222
   ```
4. **Saglik kontrolu** — Claude Code'da:
   ```
   tv_health_check
   ```
   `cdp_connected: true` donmeli.

## SMC Indikatoru Nasil Uretilir

1. `smc_engine/integrations/tradingview/prompts/` altindan bir sablon sec:
   - `prompt_full_smc_overlay.md` — **onerilen baslangic** (tum katmanlar tek
     indikator)
   - Tekil katman icin: `prompt_range_detector.md`, `prompt_structure_chochbos.md`,
     `prompt_orderblock.md`, `prompt_fvg.md`, `prompt_liquidity_sweep.md`
2. Sablonun icindeki "Build me a Pine Script v6 indicator..." metnini kopyala
   ve Claude Code chat'ine yapistir.
3. Claude `pine_set_source` + `pine_smart_compile` ile Pine'i enjekte eder,
   `pine_get_errors` ile hatalari ayiklar. 1-2 iterasyonda sifir hata olmasi
   beklenir (makaledeki gibi).
4. `pine_save` ile script TradingView Desktop kullaniciya kaydedilir.

## Config Sync Workflow (ratchet -> Pine)

Ratchet `SMCConfig` parametrelerini optimize eder. Yeni parametreleri Pine
indikatorune **birebir** aktarmak icin:

```bash
python3 examples/export_pine_config.py > pine_inputs.pine
```

Cikti, Pine v6 `input.*` deyimleri olarak gelir — alan isimleri SMCConfig
field adlariyla **birebir esit** (snake_case). Pine Editor'da indikatorun
giris bloguna yapistir, kaydet. Boylelikle Python tarafindaki defaultlar ve
chart'taki Pine indikatoru parametreleri *sync'te* kalir.

## Backtest Setup Overlay

Backtest kosulduktan sonra `Setup` nesneleri uretiyor olabilirsin. Bunlari
canli chart'ta gormek icin:

```python
from smc_engine.integrations.tradingview.pine_setup_export import to_pine_overlays
pine_snippet = to_pine_overlays(my_setups)
print(pine_snippet)
```

Cikti `var line.new` / `var label.new` cagrilari iceren bir Pine v6 snippet.
Bunu yeni bir Pine v6 indikatoru icine yapistir (`indicator(overlay=true)`),
kaydet — entry/SL/TP seviyeleri grafikte (LONG yesil, SHORT kirmizi) belirir.

## Sinirlar

- **OI / funding verisi yalniz kripto perp'te** — hisse senedinde TradingView'in
  bu veriyi sunmadigi sembollerde "OI" katmani devre disi. Makalede de ayni
  uyari var.
- **MCP server kullanici makinesinde** — biz onu repo'dan kuramayiz. Eger
  kullanici Mac/Linux'ta TradingView Desktop bulamiyorsa makaledeki `--remote-debugging-port`
  flag'ini browser'a yonlendirebilir (deneysel).
- **Pine kodu burada *derlenmiyor*** — biz string export ederiz, TradingView
  derler. Bu yuzden tests/test_pine_*.py *format* dogrulugunu test eder,
  *semantik* dogruluk MCP akisinin sorumlulugundadir.
- **Pine v6 disiplini**: `ta.*`, `indicator()`, `alert()` — sablonlarda
  acikca vurgulu. Eger kullanici farkli Pine versiyonu zorunlu kilarsa
  sablonu ozellestirmelisin.

## Referans Pine Dosyasi

`smc_engine/integrations/tradingview/reference/smc_overlay_reference.pine` —
MCP olmadan calismak isteyen kullanici icin "ne iyi gorunur" ornek. Pine
Editor'a yapistirilir, kaydedilir. Claude'a ek context olarak da verilebilir.
