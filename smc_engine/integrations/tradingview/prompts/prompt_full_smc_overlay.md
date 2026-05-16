# Prompt: FULL SMC Overlay (range + structure + OB + FVG + sweep + M15 alert)

> Bu prompt'u Claude Code'a yapistir. Onkosul: `tradingview-mcp` kurulu, TradingView Desktop debug-modda (`--remote-debugging-port=9222`) ve `tv_health_check` cdp_connected=true donmus olmali.
>
> **Onerilen baslangic noktasi.** Tek bir indikatorde turn SMC katmanlari — pratikte M15 chart'a yapistir, calistir, gozle.

---

Build me a Pine Script v6 indicator called "SMC Full Overlay (Efloud)".

It should combine, in one indicator on the chart:

1. **Range**: detect the most recent swing-based range (RH / RL / EQ) and shade premium (top 50%, red) + discount (bottom 50%, green) zones.
2. **Structure**: detect confirmed swing highs / lows using a 4-bar rule, then label BOS (Break of Structure) and CHoCH (Change of Character) using close-confirmation.
3. **Order Blocks**: identify demand and supply order blocks (Efloud rule: consecutive same-color bars preceding a strong displacement bar, body > `ob_breakout_threshold` * ATR). Draw each OB as a translucent box with status-coded opacity (FRESH / TESTED / MITIGATED / BROKEN).
4. **FVGs**: detect 3-bar Fair Value Gaps with min size `fvg_min_gap_atr` * ATR. Draw as boxes; fade boxes that are >= 100% filled.
5. **Liquidity Sweeps**: detect equal highs/lows + sweep + reclaim. Mark with arrows + labels.
6. **Alerts**: on M15 timeframe, fire an `alert()` whenever a sweep+reclaim **inside a fresh/tested OB** is confirmed — the highest-confluence event. Message must include: symbol, direction (LONG / SHORT bias), confluence summary.

Expose inputs (with snake_case names matching the SMC engine):

- `swing_lookback` (int, default 4)
- `ob_breakout_threshold` (float, default 1.5)
- `fvg_min_gap_atr` (float, default 0.3)
- `equal_level_tolerance` (float, default 0.001)
- `max_zone_age_bars` (int, default 200)
- `atr_period` (int, default 14)
- Color inputs for each layer
- A toggle per layer (`show_range`, `show_structure`, `show_ob`, `show_fvg`, `show_sweep`, `enable_alerts`).

Use Pine Script v6 syntax only. Use `ta.*` (e.g. `ta.atr`, `ta.highest`, `ta.lowest`), not bare names. Use `indicator(..., overlay=true)`, not `study()`. Use `alert()` (not `alertcondition()`) for runtime alerts. Use `var line`, `var box`, `var label` for persistent drawings. Ensure it compiles with zero errors — `pine_smart_compile` must return success in one or at most two iterations.
