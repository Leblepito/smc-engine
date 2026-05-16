# Prompt: Order Block (Efloud OB — pump/dump origin + consecutive same-color)

> Bu prompt'u Claude Code'a yapistir. Onkosul: `tradingview-mcp` kurulu, TradingView Desktop debug-modda (`--remote-debugging-port=9222`) ve `tv_health_check` cdp_connected=true donmus olmali.

---

Build me a Pine Script v6 indicator called "SMC Order Block (Efloud)".

It should:

1. Detect **demand order blocks**: a cluster of consecutive same-color (bullish) bars immediately preceding a strong bullish pump (the displacement bar closing beyond the previous swing with a body multiple of average true range — default `ob_breakout_threshold = 1.5 * ATR`).
2. Detect **supply order blocks**: the mirror case — consecutive bearish bars immediately preceding a strong bearish dump.
3. Draw each OB as a colored translucent box spanning the OB's high-to-low and extending to the right.
4. Color the box: green for DEMAND, red for SUPPLY.
5. Track status per OB:
   - **FRESH**: untested
   - **TESTED**: price has revisited the box at least once
   - **MITIGATED**: price has filled at least 50% of the box
   - **BROKEN**: price has fully closed beyond the opposite side
6. Status-driven box opacity: fresh = solid-ish, mitigated/broken = faded.
7. Expose inputs: `ob_breakout_threshold` (float, default 1.5), `atr_period` (int, default 14), `max_zone_age_bars` (int, default 200).
8. Drop a small label on each OB with kind + status.

Use Pine Script v6 syntax only. Use `ta.atr`, `ta.highest`, etc., not bare names. Use `indicator()`, not `study()`. Ensure it compiles with zero errors.
