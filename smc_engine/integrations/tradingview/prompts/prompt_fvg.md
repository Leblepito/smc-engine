# Prompt: Fair Value Gap (3-bar FVG + fill ratio + magnet zone)

> Bu prompt'u Claude Code'a yapistir. Onkosul: `tradingview-mcp` kurulu, TradingView Desktop debug-modda (`--remote-debugging-port=9222`) ve `tv_health_check` cdp_connected=true donmus olmali.

---

Build me a Pine Script v6 indicator called "SMC FVG (3-bar imbalance)".

It should:

1. Detect 3-bar Fair Value Gaps (FVGs):
   - **Bullish FVG**: bar[2].high < bar[0].low (gap above bar[2])
   - **Bearish FVG**: bar[2].low > bar[0].high (gap below bar[2])
2. Filter gaps by minimum size: gap height must exceed `fvg_min_gap_atr` × ATR (default `0.3`).
3. Draw each FVG as a translucent box (green for bullish, red for bearish) that extends to the right.
4. Track each FVG's **fill_ratio** — the percentage of the gap consumed by subsequent price action. Once fill_ratio >= 1.0, mark the FVG as "filled" and stop extending it.
5. Highlight unfilled FVGs as "magnet zones" — these are the targets price is statistically attracted to.
6. Expose inputs: `fvg_min_gap_atr` (float, default 0.3), `atr_period` (int, default 14), bullish/bearish colors, max number of active FVGs.
7. Place a small label on each FVG with its fill_ratio percentage.

Use Pine Script v6 syntax only. Use `ta.atr`, not bare names. Use `indicator()`, not `study()`. Ensure it compiles with zero errors.
