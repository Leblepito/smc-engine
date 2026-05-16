# Prompt: Structure — CHoCH / BOS (4-bar confirmed swings, close-based)

> Bu prompt'u Claude Code'a yapistir. Onkosul: `tradingview-mcp` kurulu, TradingView Desktop debug-modda (`--remote-debugging-port=9222`) ve `tv_health_check` cdp_connected=true donmus olmali.

---

Build me a Pine Script v6 indicator called "SMC Structure (CHoCH/BOS)".

It should:

1. Identify swing highs and swing lows using a 4-bar confirmation rule (default; expose as `swing_lookback` input). A swing is only confirmed after `swing_lookback` bars close on its right side — no look-ahead.
2. Track the current bias (BULLISH / BEARISH / NEUTRAL) based on the most recent confirmed swings.
3. Detect **BOS (Break of Structure)** when price closes beyond the last confirmed swing in the direction of the prevailing bias.
4. Detect **CHoCH (Change of Character)** when price closes beyond the last opposing swing — the first such event flips the bias.
5. Draw a horizontal line at each broken swing level and place a label "BOS" or "CHoCH" with the direction (↑ / ↓).
6. Use **close-confirmation** (closing price beyond the swing), not wick-only.
7. Expose inputs: `swing_lookback` (int, default 4), label colors for BOS and CHoCH.

Use Pine Script v6 syntax only. Use `ta.*`, not bare names. Use `indicator()`, not `study()`. Ensure it compiles with zero errors.
