# Prompt: Range Detector (RH / RL / EQ + premium-discount shading)

> Bu prompt'u Claude Code'a yapistir. Onkosul: `tradingview-mcp` kurulu, TradingView Desktop debug-modda (`--remote-debugging-port=9222`) ve `tv_health_check` cdp_connected=true donmus olmali.

---

Build me a Pine Script v6 indicator called "SMC Range Detector".

It should:

1. Detect the most recent swing-based range on the current chart using a configurable swing lookback (default 4 bars, matching Efloud SMC config).
2. Identify the Range High (RH), Range Low (RL), and Equilibrium (EQ = midpoint) of that range.
3. Draw RH and RL as horizontal lines that extend to the right.
4. Shade the **premium zone** (top 50% of the range) with a translucent red fill.
5. Shade the **discount zone** (bottom 50% of the range) with a translucent green fill.
6. Draw the EQ line as a thin dashed line in the middle.
7. Add labels "RH", "RL", "EQ" anchored on the right edge.
8. Expose inputs: `swing_lookback` (int, default 4), premium/discount colors, line widths.

Use Pine Script v6 syntax only. Use `ta.*` (e.g. `ta.highest`, `ta.lowest`), not bare names. Use `indicator()`, not `study()`. Ensure it compiles with zero errors.
